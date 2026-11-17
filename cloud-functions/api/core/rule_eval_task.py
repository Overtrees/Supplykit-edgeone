"""每日规则评估接力任务(§15.211, 2026-11-16)

## 为什么要有这个模块

`run_daily_rules(deep=True)` 单请求实测 83~117s(单渠道 9 万行), **超过 Makers 请求生命周期**:
cron 04:04 触发 → 实例被冻结 → 收尾语句不执行 → `pipeline_runs` 停在 running,
`close_stale_runs` 10 分钟后标 timeout, 而 `maintenance_log` 停在 `#run#<fp>#<token>`
→ 当天判定"已评估过"或"仍在跑", 再也不重跑。线上实证 11-10~11-16 连续 7 天 100% timeout,
表征是看板常驻「Rules are recalculating」+ 告警长期不刷新。

## 怎么做(复用既有机制, 不新建表 / 不新建调度源)

| 需要 | 复用什么 |
|---|---|
| 推进游标 | `sync_tasks`(task_type='rule_eval'), 与导出/导入/seed 同一张表 |
| 防双跑 | `core.task_lease`(令牌 + 心跳 + 45s TTL), 冻结 45s 后别人能接管 |
| 谁来推 | cron 首跑 + `continue_tasks`(60s 请求驱动 / 平台调度 / `/health` 保活) |
| 分片单位 | SKU 游标, 每片 `CHUNK` 个, 调 `evaluate_stock_skus(ch, skus=batch)` **增量入口** |
| 对外状态 | `eval_tasks` 一行(pending→done), 看板「规则重算中」横幅零改动 |

增量入口的关键性质(既有实现, 本模块依赖它): `evaluate_stock_skus(skus=[...])` 里的
关闭逻辑带 `_only` 集合 —— **只关本批 SKU 的 active 告警**, 且不受"单轮关闭≥80% 视为
数据异常"的护栏限制。所以分片不会互相误关, 也不会因分片触发误关护栏。

## 阶段

    prep  孤儿告警清理 + (deep) 旧 SKU 级清理 + 无需求兜底  每渠道一次, 秒级
    eval  按 SKU 游标分片评估                              每片数秒
    post  (deep) 批次四维对账 + 健康快照/风险摘要          每渠道一次
    kick  采购需求告警重判                                 一次性

## 红线对照

- 6  读路径不做全量: 本模块只在后台任务里跑; 请求路径最多 UPDATE 一行游标
- 9  算法不复制: 评估仍调 `core.rule_eval.evaluate_stock_skus`, 公式一行没改
- 15 状态带新鲜度: 每片刷新 `sync_tasks.updated_at`; 租约 45s; 不用 running 表示"在跑"
- 2  替换而非叠加: deep 档原来的"同步全量"路径已删除, 没有开关两套
- 20 跳过要自保证: 任务落在 `sync_tasks`(DB 持久事实), 实例冻结不丢; 下一次
   `continue_tasks` 按 updated_at/租约接手, 不依赖"用户打开某个页面"
"""
import json
import time

from db import execute, execute_id, one, query
from core.clock import now_cn

# routes.* 一律**延迟导入**: core 被 routes 导入, 加载期反向依赖会让"谁先初始化"变成隐式契约。
# 与 core/maintenance.py、core/rule_eval.py 同一纪律。


def _channels():
    from routes.common import ch_codes
    return ch_codes()


def _try_err(*a, **kw):
    from routes.common import try_err
    return try_err(*a, **kw)

TASK_TYPE = "rule_eval"
CHUNK = 4000          # 每片 SKU 数: 必须 <= core.metrics._SCOPE_MAX(5000), 否则 scope 下推失效
ACTIVE_MIN = 30       # 任务"活跃"窗口(分钟): 与 continue_tasks 的超时判据同源

STAGE_PREP = "prep"
STAGE_EVAL = "eval"
STAGE_POST = "post"
STAGE_KICK = "kick"


# ── 任务创建 ────────────────────────────────────────────────────────────

def active_task():
    """当前活跃的评估任务 id; '' 表示没有。

    判据与 continue_tasks 的超时口径一致(30 分钟无更新视为死), 另外要求租约未过期 ——
    只看 status='running' 会把被冻结的僵户当成"在跑"(红线 15)。
    """
    try:
        r = one("SELECT task_id, updated_at, lease_until FROM sync_tasks "
                "WHERE task_type=%s AND status='running' "
                "AND updated_at > DATE_SUB(NOW(), INTERVAL %s MINUTE) "
                "ORDER BY id DESC LIMIT 1", [TASK_TYPE, ACTIVE_MIN])
        return str((r or {}).get("task_id") or "")
    except Exception:
        return ""


def read_claim(task="daily_rules"):
    """读当天占位里的持有令牌与指纹 —— 返回 (token, fp), 没有则 ("", "")。

    为什么由本模块自己读: `run_daily_rules(channels, deep, alert_types)` 的签名是门禁
    断言的一部分(local_test), 不能加 token/fp 参数; 而调用方(cron / 看板兜底)刚 claim
    过, 占位行就是"当前持有者"的唯一真源, 直接读它比新开一条传参通道更少漂移。
    """
    try:
        from core import maintenance as _mt
        _mark = getattr(_mt, "RUN_MARK", None) or getattr(_mt, "_RUN_MARK", "#run#")
        r = one("SELECT IFNULL(details,'') AS d FROM maintenance_log "
                "WHERE `date`=%s AND task=%s", [now_cn().strftime("%Y-%m-%d"), task])
        d = str((r or {}).get("d") or "")
        if not d.startswith(_mark):
            return "", ""
        body = d[len(_mark):]
        if "#" not in body:
            return "", ""
        _fp, _suffix = body.rsplit("#", 1)
        return d, _fp
    except Exception:
        return "", ""


def enqueue(channels=None, deep=True, ats=None, mode=None, token="", fp="", source="cron"):
    """建任务(单飞)。返回 (task_id, created)。

    已存在活跃任务 → 直接复用, 不新建: cron 与看板兜底可能在同一时刻都判定"该跑了",
    建两个任务等于把同一份 9 万行评两遍(红线 2: 一处真源)。
    """
    _act = active_task()
    if _act:
        return _act, False
    if not token:
        token, _fp0 = read_claim()
        fp = fp or _fp0
    _chs = [str(c) for c in (channels or _channels()) if c]
    if not _chs:
        _chs = [str(c) for c in _channels()]
    _ats = [str(a) for a in (ats or []) if a]
    from routes.tasks import _new_task_id
    tid = _new_task_id("ev")
    # eval_tasks: 对外状态(看板横幅读它), scope 存完整计划便于排障。
    #   复用已在 running 的那一条: `rules._drain_eval_tasks` 抢占 pending 行后才调本模块,
    #   若这里再 INSERT 一条, 同一个评估会有两行状态(一行永远停在 running)。
    eid = 0
    try:
        _run_row = one("SELECT id FROM eval_tasks WHERE status='running' "
                       "AND started_at > DATE_SUB(NOW(), INTERVAL 10 MINUTE) "
                       "ORDER BY id DESC LIMIT 1")
        eid = int((_run_row or {}).get("id") or 0)
        if not eid:
            # 2026-11-16 §15.216: 显式写 status='running' + started_at=NOW()。
            #   原来只写 scope, 靠表默认值落到 **pending 且 started_at=NULL** —— 而
            #   `enqueue` 的行**从不属于"排队"**: 建完这一行, 调用方(run_daily_rules)
            #   同一次请求里就推首片。pending 会带来两个后果:
            #     ① `close_stale_runs` 只收 `running`, 收不到它 → 任务彻底停住时这行
            #        永远 pending → `eval_live_cond()` 恒真 → 看板「规则重算中」永久挂
            #        (§15.174 堵过同一类"永久挂着", 但只堵了 running、漏了 pending)
            #     ② `started_at=NULL` → 状态接口的 age_min 恒空, 排障看不到跑了多久
            eid = int(execute_id("INSERT INTO eval_tasks(scope, status, started_at) "
                                 "VALUES(%s,'running',NOW())",
                                 [json.dumps({"channels": _chs, "ats": _ats, "deep": bool(deep),
                                              "mode": str(mode or ""), "task_id": tid},
                                             ensure_ascii=False)]) or 0)
    except Exception as _e:
        _try_err("rules", "接力任务建 eval_tasks 行失败(横幅不显示, 不影响推进)", _e)
    params = {"channels": _chs, "ats": _ats, "deep": bool(deep), "mode": str(mode or ""),
              "stage": STAGE_PREP, "ch": 0, "cursor": "", "chunks": 0,
              "rows": 0, "hits": 0, "cleaned": 0, "ch_started": int(time.time()),
              "token": token or "", "fp": fp or "", "source": source,
              "eval_id": eid, "started": int(time.time())}
    ch0 = _chs[0]
    execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
            "VALUES(%s,%s,'running',%s,'{}',%s)",
            [tid, TASK_TYPE, json.dumps(params, ensure_ascii=False), ch0])
    return tid, True


# ── 单片推进 ────────────────────────────────────────────────────────────

def step(task_id, budget=40.0, driver="backend"):
    """把任务往前推, 最多占用 budget 秒。返回 {status, stage, progress...}

    budget 的意义: 单次 HTTP 请求(或 continue_tasks 的一轮)有生命周期, 推到 budget
    就必须返回 —— 剩下的交给下一次。这是"接力"的全部: 每片有界, 总进度有界推进。
    """
    from core import task_lease as _lease
    row = one("SELECT status, params, result FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"ok": True, "task_id": task_id, "status": "not_found"}
    if str(row.get("status") or "") in ("done", "error"):
        return _done_payload(task_id, row)
    _tok, _got = _lease.acquire(task_id)
    if not _got:
        return {"ok": True, "task_id": task_id, "status": "running", "busy": True}
    _stop = _lease.start_beat(task_id, _tok)
    _t0 = time.time()
    try:
        params = json.loads(row.get("params") or "{}")
        params.setdefault("driver", driver)
        while True:
            _advance(params)
            _save(task_id, params)
            if _finished(params) or (time.time() - _t0) >= budget:
                break
        if _finished(params):
            _finish(task_id, params)
        return {"ok": True, "task_id": task_id,
                "status": "done" if _finished(params) else "running",
                "stage": params.get("stage"), "progress": _progress(params)}
    except Exception as e:
        import traceback as _tb
        # 失败不静默: 任务置 error(前端/运维能看到), 占位回滚成"待跑"(下次还能重试)
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW(), "
                    "lease_until=NULL, lease_token='' WHERE task_id=%s",
                    [json.dumps({"result": {"error": str(e)[:300],
                                            "tb": _tb.format_exc()[-800:]}}, ensure_ascii=False),
                     task_id])
            _settle(params, ok=False)
            _close_eval(params, ok=False, err=str(e)[:200])
        except Exception as _e2:
            _try_err("rules", "接力任务失败收尾降级", _e2)
        return {"ok": False, "task_id": task_id, "status": "error", "error": str(e)[:200]}
    finally:
        _stop()
        _lease.release(task_id, _tok)


def _advance(p):
    """推进一个最小单元(prep/kick 一步到底; eval 一片)"""
    st = p.get("stage")
    if st == STAGE_PREP:
        p["cleaned"] = int(p.get("cleaned") or 0) + _prep(p)
        p["stage"] = STAGE_EVAL
        return
    if st == STAGE_EVAL:
        _chs = p.get("channels") or []
        while int(p.get("ch")) < len(_chs):
            ch = _chs[int(p["ch"])]
            batch = _next_skus(ch, str(p.get("cursor") or ""))
            if batch:
                hits = _eval_batch(ch, batch, p.get("ats"), p.get("mode"))
                p["cursor"] = batch[-1]
                p["chunks"] = int(p.get("chunks") or 0) + 1
                p["hits"] = int(p.get("hits") or 0) + len(hits or [])
                p["rows"] = int(p.get("rows") or 0) + len(batch)
                return
            # 本渠道 SKU 取完 → 结算渠道运行流水, 进下一渠道
            _channel_done(ch, p)
            p["ch"] = int(p["ch"]) + 1
            p["cursor"] = ""
            p["ch_started"] = int(time.time())
        p["stage"] = STAGE_POST if p.get("deep") else STAGE_KICK
        p["ch"] = 0
        return
    if st == STAGE_POST:
        _chs = p.get("channels") or []
        if int(p.get("ch")) < len(_chs):
            _post_channel(_chs[int(p["ch"])])
            p["ch"] = int(p["ch"]) + 1
            return
        p["stage"] = STAGE_KICK
        p["ch"] = 0
        return
    if st == STAGE_KICK:
        _kick(p.get("channels") or [])
        p["stage"] = "done"
        return


def _finished(p):
    return str(p.get("stage")) == "done"


def _progress(p):
    return {"stage": p.get("stage"), "channel_index": p.get("ch"),
            "chunks": int(p.get("chunks") or 0), "skus": int(p.get("rows") or 0),
            "hits": int(p.get("hits") or 0)}


def _save(task_id, p):
    """落游标 + 续占位(claim_rules 的超时判据是 created_at, 不续会被看板兜底抢走重跑)"""
    execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
            [json.dumps(p, ensure_ascii=False), task_id])
    if p.get("token"):
        touch_claim(str(p["token"]))


def _next_skus(channel, after):
    """SKU 游标分页(一次 CHUNK 个)。

    为什么按 SKU 而不是按行 id: 分片单位必须与 `evaluate_stock_skus(skus=...)` 的语义一致 ——
    它按 SKU 取该 SKU 的全部仓行, 一个 SKU 的多个仓行必须落在同一片, 否则单元聚合
    (BC 盘 = 平台+B 合计)会被切开。
    """
    try:
        rows = query("SELECT DISTINCT sku FROM inventory "
                     "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!='' "
                     "AND sku > %s ORDER BY sku LIMIT %s",
                     [channel, after, CHUNK])
    except Exception as _e:
        _try_err("rules", "接力任务取 SKU 片失败(%s)" % channel, _e)
        raise
    return [str(r.get("sku")) for r in (rows or []) if r.get("sku")]


# ── 四个阶段 ────────────────────────────────────────────────────────────

def _prep(p):
    """孤儿清理 + (deep) 旧 SKU 级清理 + 无需求兜底。

    逐字沿用 `run_daily_rules` 原实现(理由见各段注释): 这三段是"规则没了/需求没了,
    旧告警该下线", 与评估本身无关, 但必须在评估前做 —— 否则评估会把它们重新算成命中。
    """
    from core.alert_lifecycle import close as _close
    _chs = p.get("channels") or []
    deep = bool(p.get("deep"))
    _in_ch = "(" + ",".join(["%s"] * len(_chs)) + ")"
    cleaned = 0
    _orphan_rows = query(
        "SELECT DISTINCT alert_type, channel FROM alerts "
        "WHERE status='active' AND source IN ('rules_engine','event_bus')"
        + ("" if deep else " AND channel IN " + _in_ch),
        [] if deep else list(_chs))
    for r in _orphan_rows:
        at = r.get("alert_type")
        ch = r.get("channel") or "jd"
        has = one("SELECT COUNT(*) AS c FROM rules WHERE alert_type=%s AND channel=%s AND is_active=1 "
                  "AND (deleted_at IS NULL OR deleted_at='')", [at, ch]) or {}
        if not int(has.get("c") or 0):
            cleaned += int(_close(
                "alert_type=%s AND channel=%s AND source IN ('rules_engine','event_bus')",
                [at, ch], "orphan_rule_missing") or 0)
    if deep:
        # 旧 SKU 级告警(warehouse 为空)清理 —— 逐仓化后旧聚合告警会让计数翻倍
        try:
            cleaned += int(_close(
                "source IN ('rules_engine','event_bus') AND (warehouse IS NULL OR warehouse='')",
                [], "legacy_sku_level") or 0)
        except Exception as _e:
            _try_err("rules", "接力 prep 旧 SKU 级清理降级", _e)
        # 无需求兜底: 无未删订单 且 无在档 → 告警孤儿
        try:
            cleaned += int(_close(
                "source='rules_engine' "
                "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.sku=alerts.related_sku AND o.channel=alerts.channel "
                "AND (o.deleted_at IS NULL OR o.deleted_at='')) "
                "AND NOT EXISTS (SELECT 1 FROM products p WHERE p.sku=alerts.related_sku AND p.channel=alerts.channel "
                "AND (p.deleted_at IS NULL OR p.deleted_at='')) "
                "AND NOT EXISTS (SELECT 1 FROM inventory i WHERE i.sku=alerts.related_sku AND i.channel=alerts.channel)",
                [], "no_demand") or 0)
        except Exception as _e:
            _try_err("rules", "接力 prep 无需求兜底降级", _e)
    return cleaned


def _eval_batch(channel, skus, ats, mode=None):
    """评一片 SKU。算法/关闭语义全部在 `evaluate_stock_skus` 里, 这里只是调用点。

    mode: 前端当前补货模式(None/''=渠道默认); 透传给 evaluate_stock_skus,
    与同步入口的 mode 语义一致(规则过滤与单元归并按该模式算)。
    """
    from core.rule_eval import evaluate_stock_skus
    return evaluate_stock_skus(channel, skus=skus, only_ats=(ats or None), mode=(mode or None))


def _channel_done(channel, p):
    """一个渠道的 SKU 全部评完 → 写一条运行流水(不是 running 态: 写完即 finished)

    为什么不用 track_run: 它是同步上下文管理器, 而接力的"渠道开始"发生在几次请求之前。
    这里直接 INSERT 一条已完成的记录, `close_stale_runs` 因此永远不会把它误标 timeout。
    """
    _st = int(p.get("ch_started") or p.get("started") or time.time())
    try:
        execute("INSERT INTO pipeline_runs(pipeline, scope, started_at, finished_at, status, "
                "duration_ms, rows_affected, detail) VALUES('rule_eval',%s,%s,NOW(),'ok',%s,%s,%s)",
                [channel, now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                 int((time.time() - _st) * 1000), 0,
                 json.dumps({"chunks": p.get("chunks"), "skus": p.get("rows"),
                             "task": "relay"}, ensure_ascii=False)[:1000]])
    except Exception as _e:
        _try_err("rules", "接力任务写运行流水失败(%s)" % channel, _e)


def _post_channel(channel):
    """(deep) 批次四维对账 + 健康快照/风险摘要 —— 逐字沿用原 run_daily_rules 的段实现"""
    # 批次对账(四维逐一): 偏差只告警不自动修(自动修会掩盖导入遗漏/仓名错/批次未填的根因)
    try:
        from routes.cleanse_write import SELF_WAREHOUSE_TYPES as _SELF
        _ph = ",".join(["%s"] * len(_SELF))
        _bad = query(
            "SELECT i.sku, COALESCE(i.barcode,'') AS bc, i.warehouse, "
            "  i.available_qty AS qty, COALESCE(b.bq, 0) AS bq "
            "FROM inventory i "
            "LEFT JOIN (SELECT sku, COALESCE(barcode,'') AS bc, warehouse, warehouse_type, "
            "                  SUM(qty) AS bq FROM batches WHERE channel=%%s "
            "           GROUP BY sku, COALESCE(barcode,''), warehouse, warehouse_type) b "
            "  ON b.sku=i.sku AND b.bc=COALESCE(i.barcode,'') "
            " AND b.warehouse=i.warehouse AND b.warehouse_type=i.warehouse_type "
            "WHERE i.channel=%%s AND i.warehouse_type IN (%s) "
            "  AND ABS(i.available_qty - COALESCE(b.bq, 0)) > 1" % _ph,
            [channel, channel] + list(_SELF))
        if _bad:
            _s0 = _bad[0]
            execute("INSERT INTO quality_logs(log_type, level, message, source) "
                    "VALUES('quiet_error','warning',%s,'cron')",
                    ("批次对账 %s: %d 项偏差, 例 sku=%s 69码=%s 仓=%s 可用=%s 批次=%s "
                     "(需补全入库/出库的批次信息后重建)"
                     % (channel, len(_bad), _s0.get("sku"), _s0.get("bc") or "-",
                        _s0.get("warehouse"), _s0.get("qty"), _s0.get("bq")),))
    except Exception as _e:
        _try_err("rules", "接力任务批次对账降级", _e)
    # 健康快照(按天唯一) + 濒临断货摘要
    try:
        from routes.dashboard import _stock_risk, _health_index
        from routes.common import ch_default_mode as _cdm
        _rk = _stock_risk(channel)
        _hi = _health_index(channel, _cdm(channel))
        _t = (_rk.get("bcTotal", 0) or 0) + (_rk.get("cTotal", 0) or 0) + (_rk.get("ownTotal", 0) or 0)
        _r = ((_rk.get("bcCritical", 0) or 0) + (_rk.get("cCritical", 0) or 0)
              + (_rk.get("ownCritical", 0) or 0))
        _o = ((_rk.get("bcWarning", 0) or 0) + (_rk.get("cWarning", 0) or 0)
              + (_rk.get("ownWarning", 0) or 0))
        _sc2 = (_hi or {}).get("score")
        execute("INSERT INTO health_snapshot(date, channel, score, own_score, platform_score, bc_score) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE score=VALUES(score), "
                "own_score=VALUES(own_score), platform_score=VALUES(platform_score), "
                "bc_score=VALUES(bc_score)",
                [now_cn().strftime("%Y-%m-%d"), channel,
                 int(_sc2) if _sc2 is not None else -1,
                 int((_hi.get("own") or {}).get("score") or -1),
                 int((_hi.get("platform") or {}).get("score") or -1),
                 int((_hi.get("bc") or {}).get("score") or -1)])
        _msg = "[%s] 濒临断货: 共%d(红%d/橙%d/黄%d), BC=%d C=%d OWN=%d | 健康: %s分" % (
            channel, _t, _r, _o, max(_t - _r - _o, 0),
            _rk.get("bcTotal", 0) or 0, _rk.get("cTotal", 0) or 0, _rk.get("ownTotal", 0) or 0,
            str(_sc2 if _sc2 is not None else "-"))
        execute("INSERT INTO quality_logs(log_type, level, message, source) "
                "VALUES('risk_summary','info',%s,'cron')", (_msg,))
    except Exception as _e:
        _try_err("rules", "接力任务健康快照降级", _e)


def _kick(chs):
    """评估关了/开了一批告警 → 采购需求必须跟着重判(异步 + 每渠道限频), 不拖慢任务"""
    try:
        from core.alert_jobs import kick_purchase_alerts as _kp
        for ch in chs:
            _kp(ch)
    except Exception as _e:
        _try_err("rules", "接力任务采购告警重判降级", _e)


# ── 收尾 ───────────────────────────────────────────────────────────────

def touch_claim(token, task="daily_rules"):
    """续 `maintenance_log` 占位的 created_at。

    不续会怎样: `claim_rules` 的超时判据是 `created_at < NOW()-20min`(maintenance._RUN_STALE_MIN)
    —— 接力任务跑 20 分钟, 到点后看板兜底就判定"持有者被冻结", 抢走后**再建一个任务重跑全量**。
    每次推进顺手刷一次, 等价于心跳(与租约同一时刻, 不额外增加请求)。
    """
    if not token:
        return
    try:
        execute("UPDATE maintenance_log SET created_at=NOW() WHERE `date`=%s AND task=%s "
                "AND IFNULL(details,'')=%s",
                [now_cn().strftime("%Y-%m-%d"), task, token])
    except Exception as _e:
        _try_err("rules", "接力任务续占位失败(TTL 由 claim 超时兜底)", _e)


def _settle(p, ok=True):
    if not p.get("token") or not p.get("fp"):
        return
    from core.maintenance import settle_rules
    settle_rules(str(p["token"]), str(p["fp"]), ok=ok)


def _close_eval(p, ok=True, err=""):
    """把 eval_tasks 那一行收尾 —— 看板「规则重算中」靠它退出"""
    eid = int(p.get("eval_id") or 0)
    if not eid:
        return
    try:
        if ok:
            execute("UPDATE eval_tasks SET status='done', rows_out=%s, duration_ms=%s, "
                    "finished_at=NOW() WHERE id=%s AND status<>'done'",
                    [int(p.get("hits") or 0),
                     int((time.time() - int(p.get("started") or time.time())) * 1000), eid])
        else:
            execute("UPDATE eval_tasks SET status='error', error=%s, duration_ms=%s, "
                    "finished_at=NOW() WHERE id=%s",
                    [err[:200], int((time.time() - int(p.get("started") or time.time())) * 1000), eid])
    except Exception as _e:
        _try_err("rules", "接力任务收尾 eval_tasks 失败", _e)


def abandon(task_id, reason="任务超时(30 分钟无更新)"):
    """任务被外部判死(continue_tasks 的 30 分钟超时)时, 补做本模块的收尾。

    为什么必须有: `continue_tasks` 判定超时只做了一件事 —— 把 `sync_tasks` 置 error。
    它不知道这条任务还挂着一行 `eval_tasks`(看板横幅)和一个 `#run#` 占位:
      · eval_tasks 停在 running → `eval_live_cond()` 恒真 → 「规则重算中」永久挂
      · `#run#<fp>#<token>` 占位不退 → 当天只能靠 `_RUN_STALE_MIN`(20min) 自愈
    进程内异常那条路径(step 的 except)本来就会收尾, 所以这里补的是**进程直接消失**
    那条路径 —— 冻结/掐断时 except 不会执行。

    幂等: `_close_eval` 与 `settle_rules` 都按状态条件写, 重复调用无副作用。
    """
    try:
        row = one("SELECT params FROM sync_tasks WHERE task_id=%s", [task_id])
        p = json.loads((row or {}).get("params") or "{}")
        if not p:
            return False
        _close_eval(p, ok=False, err=reason)
        _settle(p, ok=False)
        return True
    except Exception as _e:
        _try_err("rules", "接力任务超时收尾降级(%s)" % task_id, _e)
        return False


def _finish(task_id, p):
    """全部阶段完成: 落指纹(下次同依据直接跳过) + 收尾 eval_tasks + 写汇总"""
    _settle(p, ok=True)
    _close_eval(p, ok=True)
    _res = {"result": {"scope": "deep" if p.get("deep") else "light",
                       "channels": p.get("channels"), "ats": p.get("ats"),
                       "orphan_cleaned": int(p.get("cleaned") or 0),
                       "rules_triggered": int(p.get("hits") or 0),
                       "chunks": int(p.get("chunks") or 0),
                       "skus": int(p.get("rows") or 0),
                       "elapsed": int(time.time() - int(p.get("started") or time.time()))}}
    execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW(), "
            "lease_until=NULL, lease_token='' WHERE task_id=%s",
            [json.dumps(p, ensure_ascii=False), json.dumps(_res, ensure_ascii=False), task_id])
    try:
        from routes.analysis_cache import invalidate as _inv
        # 评估写完 alerts 必须失效一次: 调用方那次 invalidate 发生在**调度之前**,
        # 缓存很可能在评估完成前就被重建 → "告警变了, 看板还是旧计数"(原 _drain_eval_tasks 同款)
        _inv("rules")
    except Exception as _e:
        _try_err("rules", "接力任务完成后缓存失效降级", _e)


def _done_payload(task_id, row):
    _ri = (json.loads(row.get("result") or "{}") or {}).get("result") or {}
    return {"ok": True, "task_id": task_id, "status": str(row.get("status") or ""),
            "result": _ri}


def stat(task_id):
    """只读进度(不抢租约、不推进)"""
    row = one("SELECT status, params, result, updated_at FROM sync_tasks WHERE task_id=%s",
              [task_id])
    if not row:
        return {"ok": True, "task_id": task_id, "status": "not_found"}
    if str(row.get("status") or "") in ("done", "error"):
        return _done_payload(task_id, row)
    p = json.loads(row.get("params") or "{}")
    return {"ok": True, "task_id": task_id, "status": str(row.get("status") or ""),
            "stage": p.get("stage"), "progress": _progress(p)}
