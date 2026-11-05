"""原生 rules 路由(方案 B): 规则引擎 CRUD + test"""
import json
import threading

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute, execute_id
from routes.common import ok, fail, traced, try_err
from core.alert_lifecycle import close as _close_alerts  # 2026-11-03 D8: 关闭统一入口
from core.rule_fields import FIELD_SPEC  # 2026-11-05: 字段注册表(测试回显与校验共用)

router = APIRouter(tags=["rules"])

_FIELDS = "id, name, event, condition_json, alert_type, alert_title, alert_desc, severity, is_active, channel, mode, params, created_at, updated_at, deleted_at"


@router.get("/rules")
@traced
def list_rules(channel: str = "jd", include_deleted: int = 0):
    """规则列表(channel=all 或空返回全部, 否则按渠道过滤——修复 jd+other 混返致前端重复显示)"""
    conds, params = [], []
    if channel and channel != "all":
        conds.append("channel=%s")
        params.append(channel)
    if not include_deleted:
        conds.append("(deleted_at IS NULL OR deleted_at='')")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = query("SELECT %s FROM rules%s ORDER BY id ASC" % (_FIELDS, where), params)
    out = []
    for r in rows:
        try:
            r["condition"] = json.loads(r.get("condition_json") or "{}")
        except Exception as _e:
            try_err('rules', '规则条件解析失败(静默置空——坏规则不触发)', _e)
            r["condition"] = {}
        try:
            _pp = r.get("params")
            r["params"] = json.loads(_pp) if _pp else {}
        except Exception as _e:
            try_err('rules', '规则参数解析失败(静默置空)', _e)
            r["params"] = {}
        out.append(r)
    return ok(out)


def _norm_json(v):
    """规范化 JSON 用于比较(键序/空白不敏感; 旧值可能是带空格的字符串)"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    if v in (None, ""):
        return ""
    try:
        return json.dumps(json.loads(str(v)), ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(v)


def _changed_materially(d, old):
    """本次更新是否改变**评估结果**(2026-11-03 D4)

    只改 name / alert_title / alert_desc / severity → 不重算(告警集合不变, 同步展示字段即可);
    改 condition / params / is_active / channel / mode / event / alert_type → 必须重算。
    这判定的价值: 原实现任何一次保存都叫 run_daily_rules(), 改一个错别字 = 3 条全表 UPDATE
    + 10 万行 × N 渠道评估 + 全表对账。而现在文案类保存只发一条 UPDATE。
    """
    if not old:
        return True
    if isinstance(d.get("condition"), dict) and _norm_json(d["condition"]) != _norm_json(old.get("condition_json")):
        return True
    if isinstance(d.get("params"), dict) and _norm_json(d["params"]) != _norm_json(old.get("params")):
        return True
    for _f in ("is_active", "channel", "mode", "event", "alert_type"):
        if _f not in d:
            continue
        if _f == "is_active":
            if (1 if d.get("is_active") else 0) != (1 if int(old.get("is_active") or 0) else 0):
                return True
        elif str(d.get(_f) or "") != str(old.get(_f) or ""):
            return True
    return False


def _sync_rule_text(rid):
    """文案/严重度变更 → 同步存量 active 告警的展示字段(一条 UPDATE, 不重算)

    不同步会出现"规则改了文案, 告警页还是旧文案"——又一个看起来没生效的坑。
    """
    try:
        _r = one("SELECT alert_type, channel, alert_title, alert_desc, name, severity "
                 "FROM rules WHERE id=%s", [rid])
        if not _r:
            return
        execute("UPDATE alerts SET title=%s, description=%s, severity=%s "
                "WHERE alert_type=%s AND channel=%s AND status='active' AND source='rules_engine'",
                [_r.get("alert_title") or _r.get("name") or "", _r.get("alert_desc") or "",
                 _r.get("severity") or "warning", _r.get("alert_type"), _r.get("channel") or "jd"])
    except Exception as _e:
        try_err('rules', '规则文案同步降级', _e)


@router.post("/rules")
@traced
async def create_rule(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('rules', '静默降级', _e)
    name = d.get("name") or ""
    if not name:
        return fail("缺少 name")
    cond = d.get("condition") or d.get("condition_json") or {}
    # 条件字段校验(防静默 0 恒真/恒假) —— 传 event, 让"该事件上下文不提供的字段"当场被拦
    from core.rules import validate_condition
    _v_err = validate_condition(cond, d.get("event") or None)
    if _v_err:
        return fail("条件字段无效: " + "; ".join(_v_err[:5]))
    _params = d.get("params")
    _pjson = json.dumps(_params, ensure_ascii=False) if isinstance(_params, dict) else None
    _rid = execute_id("INSERT INTO rules(name, event, condition_json, alert_type, alert_title, alert_desc, severity, is_active, channel, mode, params) "
                      "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (name, d.get("event", ""), json.dumps(cond, ensure_ascii=False),
                       d.get("alert_type", ""), d.get("alert_title", ""), d.get("alert_desc", ""),
                       d.get("severity", "warning"), 1 if d.get("is_active", 1) else 0,
                       d.get("channel", "jd"), d.get("mode", ""), _pjson))
    from routes.analysis_cache import invalidate
    invalidate("rules")  # 规则新建 → 看板/接口缓存即时失效
    # 规则新建 → 后台即时重算告警; D4: 只评本渠道 + 本条规则产出的 alert_type
    _tid = _schedule_rule_eval(channel=d.get("channel") or "jd",
                               alert_types=[d.get("alert_type") or ""])
    return ok({"id": _rid, "eval_task_id": _tid})


@router.put("/rules/{rid}")
@traced
async def update_rule(rid: int, request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('rules', '静默降级', _e)
    sets = []
    params = []
    for f in ("name", "event", "alert_type", "alert_title", "alert_desc", "severity", "mode", "channel"):
        if f in d:
            sets.append("`%s` = %%s" % f)
            params.append(d[f])
    if "is_active" in d:
        sets.append("is_active = %s")
        params.append(1 if d["is_active"] else 0)
    if "condition" in d:
        from core.rules import validate_condition
        # 只改条件、不改事件时, 用库里的事件做 scope 校验(否则等于跳过校验)
        _ev = d.get("event") or (one("SELECT event FROM rules WHERE id=%s", [rid]) or {}).get("event")
        _v_err = validate_condition(d["condition"], _ev or None)
        if _v_err:
            return fail("条件字段无效: " + "; ".join(_v_err[:5]))
        sets.append("condition_json = %s")
        params.append(json.dumps(d["condition"], ensure_ascii=False))
    if "params" in d:
        sets.append("params = %s")
        params.append(json.dumps(d["params"], ensure_ascii=False) if isinstance(d["params"], dict) else None)
    if not sets:
        return fail("无更新字段")
    # 告警开关联动(即时, 四维: 不残留/不空等): 读取旧 params 的 alert_enabled 对比新值
    # D4: 同时取全字段做"实质变更"判定(改文案 vs 改逻辑, 后者才需要重算)
    _rl = one("SELECT %s FROM rules WHERE id=%%s" % _FIELDS, [rid])
    _old_ae = None
    _new_ae = None
    if _rl:
        try:
            _old_ae = str((json.loads(_rl.get("params") or "{}") or {}).get("alert_enabled"))
        except Exception as _e:
                try_err('rules', '静默降级', _e)
        if isinstance(d.get("params"), dict):
            _new_ae = str(d["params"].get("alert_enabled"))
    _ch = d.get("channel") or (_rl.get("channel") if _rl else None) or "jd"
    _material = _changed_materially(d, _rl)  # 必须在 UPDATE 之前比较旧值
    params.append(rid)
    execute("UPDATE rules SET %s WHERE id=%%s" % ", ".join(sets), params)
    # 开关变化 → 即时联动: 关=清该规则存量告警; 开=即时评估生成(幂等去重)
    if _rl and _old_ae != _new_ae and _new_ae is not None:
        _at = _rl.get("alert_type")
        if _new_ae == "0":
            _close_alerts("alert_type=%s AND channel=%s AND source='rules_engine'",
                          [_at, _ch], "rule_alert_off")
        elif _new_ae == "1":
            try:
                from core.rules import evaluate_stock_skus
                evaluate_stock_skus(_ch, limit=100000)
            except Exception as _e:
                    try_err('rules', '静默降级', _e)
    from routes.analysis_cache import invalidate
    invalidate("rules")  # 规则编辑 → 缓存即时失效
    _tid = 0
    if _material:
        # 规则变更 → 后台即时重算告警; D4: 渠道 + 受影响 alert_type(改 alert_type 时新旧都评)
        _ats = sorted({str(x) for x in ((_rl or {}).get("alert_type"), d.get("alert_type")) if x})
        _tid = _schedule_rule_eval(channel=_ch, alert_types=_ats)
    else:
        # 只改了文案/严重度: 告警集合不变, 同步存量告警显示字段即可(不评估)
        _sync_rule_text(rid)
    return ok({"eval_task_id": _tid})


_KICK_LOCK = threading.Lock()
_WORKER_ON = {"v": False}


def _kick_eval_worker():
    """确保只有一个消费线程在跑(多次保存不会堆线程); 空闲退出后由 finally 再拉起"""
    with _KICK_LOCK:
        if _WORKER_ON["v"]:
            return
        _WORKER_ON["v"] = True
    try:
        threading.Thread(target=_drain_eval_tasks, daemon=True).start()
    except Exception as _e:
        with _KICK_LOCK:
            _WORKER_ON["v"] = False
        try_err('rules', '评估线程启动降级', _e)


def _drain_eval_tasks():
    from routes.cron import run_daily_rules
    import time as _tm
    try:
        for _ in range(200):
            _t = one("SELECT id, scope FROM eval_tasks WHERE status='pending' ORDER BY id LIMIT 1")
            if not _t:
                break
            _id = int(_t.get("id"))
            # 抢占: 多实例同时看到同一行, 只有 status='pending' 的那次 UPDATE 生效
            if not execute("UPDATE eval_tasks SET status='running', started_at=NOW() "
                           "WHERE id=%s AND status='pending'", [_id]):
                continue
            _t0 = _tm.time()
            try:
                _sc = json.loads(_t.get("scope") or "{}")
                _r = run_daily_rules(channels=_sc.get("channels") or None,
                                     alert_types=_sc.get("ats") or None,
                                     deep=bool(_sc.get("deep")))
                execute("UPDATE eval_tasks SET status='done', rows_out=%s, duration_ms=%s, "
                        "finished_at=NOW() WHERE id=%s",
                        [len(_r.get("rules_triggered") or []), int((_tm.time() - _t0) * 1000), _id])
            except Exception as _e:
                try_err('rules', '评估任务执行失败', _e)
                try:
                    execute("UPDATE eval_tasks SET status='error', error=%s, duration_ms=%s, "
                            "finished_at=NOW() WHERE id=%s",
                            [str(_e)[:200], int((_tm.time() - _t0) * 1000), _id])
                except Exception:
                    pass
            # 评估写完 alerts 必须失效一次 —— 调用方的 invalidate 发生在调度之前,
            #   缓存很可能在评估完成前就被重建, 结果是"告警变了, 看板还显示旧计数"。
            try:
                from routes.analysis_cache import invalidate as _inv
                _inv("rules")
            except Exception as _ei:
                try_err('rules', '评估后缓存失效降级', _ei)
    except Exception as _e:
        try_err('rules', '评估消费循环异常', _e)
    finally:
        with _KICK_LOCK:
            _WORKER_ON["v"] = False
        # 退出窗口内又来了新任务 → 重新拉起(否则任务会一直 pending 到下次保存)
        try:
            if one("SELECT id FROM eval_tasks WHERE status='pending' LIMIT 1"):
                _kick_eval_worker()
        except Exception:
            pass


def _schedule_rule_eval(channel=None, alert_types=None, deep=False):
    """规则变更 → 评估任务入库 + 后台消费; **返回 task_id**(前端凭它轮询, 不靠猜时间)

    D4 精确影响面: 只评"受影响的渠道 × 受影响的 alert_type"。
      · channels   : str 或 list; None/空 = 全渠道
      · alert_types: list; 空 = 该渠道全部规则
      · deep       : True 才跑每日全量口径(孤儿全表清理/批次对账/健康快照)
    实测背景: 原来保存一条规则 = 完整每日任务 —— 3 条全表 UPDATE + 10 万行 × N 渠道
    + 全表对账。改个错别字也要付这个代价, 而且改完还不知道跑没跑完。

    合并语义: 同 scope 且仍在 pending/running 的任务直接复用(返回同一 id) ——
    用户连续保存 3 次只评一次, 前端轮询的也是同一个任务。
    """
    _chs = [channel] if isinstance(channel, str) and channel else (list(channel or []) or [])
    _scope = json.dumps({"channels": _chs, "ats": list(alert_types or []), "deep": bool(deep)},
                        ensure_ascii=False, sort_keys=True)
    try:
        _exist = one("SELECT id FROM eval_tasks WHERE status IN ('pending','running') "
                     "AND scope=%s ORDER BY id DESC LIMIT 1", [_scope])
        if _exist:
            _tid = int(_exist.get("id") or 0)
        else:
            _tid = int(execute_id("INSERT INTO eval_tasks(scope) VALUES(%s)", [_scope]) or 0)
    except Exception:
        # 表缺失自愈(首次部署竞态): 建表后重试一次
        try:
            from db import execute as _ex0
            _ex0("CREATE TABLE IF NOT EXISTS eval_tasks ("
                 "id BIGINT AUTO_INCREMENT PRIMARY KEY, scope VARCHAR(255) DEFAULT '', "
                 "status VARCHAR(16) DEFAULT 'pending', rows_out INT DEFAULT 0, "
                 "duration_ms INT DEFAULT 0, error VARCHAR(255) DEFAULT '', "
                 "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, started_at DATETIME NULL, "
                 "finished_at DATETIME NULL, KEY idx_eval_status (status, created_at))")
            _tid = int(execute_id("INSERT INTO eval_tasks(scope) VALUES(%s)", [_scope]) or 0)
        except Exception as _e:
            try_err('rules', 'eval_tasks 自愈失败', _e)
            return 0
    _kick_eval_worker()
    return _tid


def _close_alerts_for_rules(ids, reason="rule_disabled"):
    """规则删除/停用时联动关闭其产出的 active 告警(PA 行为, 免等每日孤儿清理)

    reason 落 alerts.closed_reason(D8): 同一批告警可能被"停用/删除/永久删"三个入口关闭,
    没有原因就无法区分"用户主动关的"和"规则没了跟着消失的"。
    """
    try:
        if not ids:
            return
        ph = ",".join(["%s"] * len(ids))
        pairs = set()
        for r in query("SELECT alert_type, channel FROM rules WHERE id IN (%s)" % ph, ids):
            pairs.add((r.get("alert_type"), r.get("channel") or "jd"))
        for at, ch in pairs:
            _close_alerts("alert_type=%s AND channel=%s AND source IN ('rules_engine','event_bus')",
                          [at, ch], reason)
    except Exception as _e:
            try_err('rules', '静默降级', _e)


@router.delete("/rules/{rid}")
@traced
def delete_rule(rid: int):
    # 软删除 + 联动关闭该类告警
    _close_alerts_for_rules([rid], "rule_deleted")
    execute("UPDATE rules SET deleted_at=NOW(), is_active=0 WHERE id=%s", [rid])
    from routes.analysis_cache import invalidate
    invalidate("rules")
    return ok({})


@router.post("/rules/{rid}/restore")
@traced
def restore_rule(rid: int):
    execute("UPDATE rules SET deleted_at='', is_active=1 WHERE id=%s", [rid])
    from routes.analysis_cache import invalidate
    invalidate("rules")
    _schedule_rule_eval()  # 恢复 → 浅档全渠道重评(渠道未知, 取并集最稳)
    return ok({})


@router.post("/rules/{rid}/permanent-delete")
@traced
def permanent_delete_rule(rid: int):
    execute("DELETE FROM rules WHERE id=%s", [rid])
    from routes.analysis_cache import invalidate
    invalidate("rules")
    _schedule_rule_eval()  # 永久删 → 浅档重评(顺带清孤儿告警)
    return ok({})


@router.post("/rules/batch")
@traced
async def rules_batch(request: Request):
    """批量操作: {action: active|inactive|delete|restore|purge, ids: []}"""
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('rules', '静默降级', _e)
    action = d.get("action", "")
    ids = d.get("ids") or []
    if not ids:
        return fail("缺少 ids")
    ph = ",".join(["%s"] * len(ids))
    if action == "active":
        execute("UPDATE rules SET is_active=1 WHERE id IN (%s)" % ph, ids)
        # 启用即时评估生成告警(四维: 启用即生效, 不等每日 cron; 幂等去重)
        try:
            from core.rules import evaluate_stock_skus
            from routes.common import ch_codes as _chc0
            for _ch in _chc0():   # 2026-11-02 S1-5: 渠道数据驱动(原硬编码 jd/other)
                evaluate_stock_skus(_ch, limit=100000)
        except Exception as _e:
                try_err('rules', '静默降级', _e)
    elif action == "inactive":
        # 停用联动关闭该类告警(PA 行为)
        _close_alerts_for_rules(ids)
        execute("UPDATE rules SET is_active=0 WHERE id IN (%s)" % ph, ids)
    elif action == "delete":
        _close_alerts_for_rules(ids, "rule_deleted")
        execute("UPDATE rules SET deleted_at=NOW(), is_active=0 WHERE id IN (%s)" % ph, ids)
    elif action == "restore":
        execute("UPDATE rules SET deleted_at='', is_active=1 WHERE id IN (%s)" % ph, ids)
    elif action == "purge":
        _close_alerts_for_rules(ids, "rule_deleted")
        execute("DELETE FROM rules WHERE id IN (%s)" % ph, ids)
    else:
        return fail("未知操作: " + str(action))
    from routes.analysis_cache import invalidate
    invalidate("rules")  # 批量启用/停用/删除/恢复/永久删 → 看板告警计数/规则联动即时失效
    # D4: active 分支已在上方同步评估过, 不再重复调度(否则同一批 SKU 被评两遍)
    if action != "active":
        _schedule_rule_eval()  # 浅档全渠道重评
    return ok({"updated": len(ids)})


@router.get("/rules/eval-status")
@traced
def rules_eval_status(task_id: int = 0):
    """规则评估任务状态(2026-11-03 D5): 保存规则后前端轮询, done 再刷新看板

    不返回 404: 任务过期(保留策略清理)对前端等价于"已结束, 可以刷新了"。
    """
    if task_id:
        _r = one("SELECT id, status, rows_out, duration_ms, error, created_at, "
                 "started_at, finished_at FROM eval_tasks WHERE id=%s", [task_id])
        if not _r:
            return ok({"id": task_id, "status": "expired", "rows_out": 0})
        return ok(_r)
    _r2 = one("SELECT COUNT(*) AS c FROM eval_tasks WHERE status IN ('pending','running')") or {}
    return ok({"running": int(_r2.get("c") or 0)})


@router.post("/rules/evaluate")
@traced
async def rules_evaluate(request: Request):
    """手动触发规则全量评估(规则页'立即运行' —— 规则改动后即时出告警, 不等每日 cron)
    双渠道 evaluate_stock_skus 全量库存行(SKU×仓), 返回各渠道触发规则数"""
    from core.rules import evaluate_stock_skus
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('rules', '静默降级', _e)
    from routes.common import ch_codes as _chc1
    channels = d.get("channels") or _chc1()   # 2026-10-30 渠道数据驱动
    # 2026-10-28 mode 生效: 前端传当前补货模式(bbcc/traditional), 据此过滤规则
    mode = d.get("mode") or ""
    out = {}
    for ch in channels:
        try:
            out[ch] = len(evaluate_stock_skus(ch, limit=100000, mode=mode or None))
        except Exception as e:
            out[ch] = "error: %s" % str(e)[:80]
    from routes.analysis_cache import invalidate
    invalidate("rules")  # 评估可能生成/变更告警 → 看板/接口缓存即时失效
    try:
        from core.rule_eval import LAST_DIAG as _diag
    except Exception:
        _diag = {}
    return ok({"evaluated": out, "diag": _diag})


@router.post("/rules/{rid}/test")
@traced
async def test_rule(rid: int, request: Request):
    """规则引擎可视化调试: 传入模拟库存/订单数据, 真实评估条件是否触发"""
    row = one("SELECT * FROM rules WHERE id=%s", [rid])
    if not row:
        return fail("规则不存在", 404)
    try:
        cond = json.loads(row.get("condition_json") or "{}")
    except Exception as _e:
        try_err('rules', '测试条件解析失败(静默置空)', _e)
        cond = {}
    body = {}
    try:
        body = await request.json()
    except Exception as _e:
            try_err('rules', '静默降级', _e)
    # 规则参数: body.params 覆盖规则 params(测试弹窗可调, 模拟 params.* 引用)
    params = body.get("params")
    if not isinstance(params, dict):
        try:
            params = json.loads(row.get("params") or "{}")
        except Exception as _e:
            try_err('rules', '测试参数解析失败(静默置空)', _e)
            params = {}
    _cj_all = json.dumps(cond, ensure_ascii=False)
    # 2026-11-05 抽出(本文件已超 600 行红线): 模拟 ctx 的构造在 core/rule_sim.py, 与真实评估同形
    from core.rule_sim import build_sim_ctx
    ctx = build_sim_ctx(_cj_all, params, body, row.get("channel") or "jd")
    from core.rules import _check_condition, _resolve_value
    triggered = _check_condition(cond, ctx)
    detail = {}
    try:
        left_raw = cond.get("left", "")
        right_raw = cond.get("right", "")
        detail["left"] = left_raw
        detail["right"] = right_raw
        detail["op"] = cond.get("op", "<")
        detail["left_value"] = _resolve_value(left_raw, ctx)
        if str(right_raw).startswith("max("):
            detail["right_value"] = "max(%s)" % right_raw[4:-1]
        elif str(right_raw).replace(".", "", 1).isdigit():
            detail["right_value"] = float(right_raw)
        elif (str(right_raw) in FIELD_SPEC or str(right_raw).startswith("params.")
              or any(o in str(right_raw) for o in "+-*/")):
            # 2026-11-05: 与 _check_single 同一判定 —— 原只解析带点字段, right 写
            #   daily_sales/sellable 时弹窗回显字符串, 与判定结果对不上
            detail["right_value"] = _resolve_value(right_raw, ctx)
        else:
            detail["right_value"] = str(right_raw).strip("'\"")
        detail["warehouse"] = cond.get("warehouse", "")
        # 2026-11-01 嵌套子条件逐条回显 —— 原只回顶层那一对比值:
        #   `A and B` / `A or [B,C]` 结构下, 用户看到 A 命中会以为整条规则该触发,
        #   实际可能是 B 不命中 → 判"规则坏了"。逐条给 sub_results, 谁拦的清清楚楚。
        def _sub_row(sub):
            _row = {"left": sub.get("left", ""), "op": sub.get("op", "<"),
                    "right": sub.get("right", "")}
            try:
                _row["left_value"] = _resolve_value(sub.get("left", ""), ctx)
                _r = sub.get("right", "")
                _row["right_value"] = (_resolve_value(_r, ctx)
                                       if isinstance(_r, str) and not _r.replace(".", "", 1).isdigit()
                                       else (float(_r) if str(_r).replace(".", "", 1).isdigit() else _r))
            except Exception as _e:
                try_err('rules', '测试子条件取值失败', _e)
            _row["hit"] = bool(_check_condition(sub, ctx))
            return _row

        _and = cond.get("and")
        if isinstance(_and, dict) and _and:
            detail["and"] = _sub_row(_and)
        _or = cond.get("or")
        if isinstance(_or, list) and _or:
            detail["or"] = [_sub_row(s) for s in _or if isinstance(s, dict)]
        # 组合语义: and 全中 && or 任一命中(与 _check_condition 一致), 便于前端一句话结论
        _top = _check_condition({k: v for k, v in cond.items() if k not in ("and", "or")}, ctx)
        detail["base_hit"] = bool(_top)
    except Exception as _e:
            try_err('rules', '静默降级', _e)
    return ok({"triggered": triggered,
               "alert_title": row.get("alert_title", ""),
               "alert_desc": row.get("alert_desc", ""),
               "detail": detail})
