"""task seed 模块(2026-10-11 拆分——tasks.py 减负; seed 接力/reset/status)
函数: seed_fill_status / seed_fill / seed_reset——依赖 routes.seed_fill 填充逻辑
_new_task_id/_log_task 工具仍留 tasks.py——函数内延迟 import(防循环)
"""
from datetime import datetime, timedelta, timezone
import json

from db import query, one, execute
from routes.common import ok, fail, traced, try_err

from fastapi import APIRouter, Request, Form, UploadFile, File
from core.clock import now_cn
router = APIRouter(tags=["task-seed"])



@router.get("/seed/fill/status")
@traced
def seed_fill_status(task_id: str = "", driver: str = "frontend"):
    from routes.tasks import _new_task_id, _log_task  # 延迟(防循环)
    from routes.tasks import _TABLES_RESET  # 延迟(表清单)
    from routes.common import mark_driver as _mkdrv
    _mkdrv(task_id, driver)
    """任务状态查询(Makers 异步适配: running 时续跑下一步, 客户端轮询推进直到 done)"""
    if not task_id:
        return {"data": {"status": "not_found"}}
    row = one("SELECT status, params FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"data": {"status": "not_found"}}
    status = row.get("status") or "not_found"
    if status in ("done", "error"):
        return {"data": {"status": status}}
    # 防并发续跑(前端 App+Settings 双轮询/多实例): 原子抢占 updated_at 锁
    # 3 秒内已有请求续跑过 → 本次跳过(步骤执行中轮询不再重复触发, 防步骤并发冲突)
    try:
        _locked = execute("UPDATE sync_tasks SET updated_at=NOW() WHERE task_id=%s "
                          "AND (updated_at IS NULL OR updated_at < DATE_SUB(NOW(), INTERVAL 3 SECOND))",
                          [task_id])
        if not _locked:
            return {"data": {"status": "running"}}  # 已有请求在续跑, 跳过本次(防并发)
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    # running → 续跑下一步(每步 ≤90s, 函数 120s 上限内)
    try:
        import json as _json
        import time as _tt
        params = _json.loads(row.get("params") or "{}")
        step = int(params.get("step") or 0)
        from routes.seed_fill import prepare_skus, seed_step, SEED_DONE
        _t0 = _tt.time()
        skus_data = prepare_skus()
        today = now_cn()
        # 2026-11-05 带 run_id: 每步写入前登记 id 水位 → 可精确回滚本轮新增行 + 行数对账
        nxt, part = seed_step(step, today, skus_data, run_id=task_id)
        _sq = round(_tt.time() - _t0, 1)
        # 2026-10-15 每步耗时 steps + 进度中 cur_step(前端 TaskCard 可视化)
        _steps = (params.get("steps") or []) + [{"name": str(part) or ("步骤%d" % step), "elapsed": _sq}]
        # 2026-11-05 完成判据改从 seed_fill.SEED_DONE 取 —— 原写死 10, 而段号重排后完成值是 12
        #   → 任务越过 10 后永不落 done, status 轮询会一直续跑(空转烧 RU)
        # total_step 一并下发: 前端进度条此前也写死 10 段, step>10 时顶在 99% 不动
        _cur = {"name": ("步骤%d" % nxt) if nxt < SEED_DONE else "完成", "started": int(_tt.time())}
        new_params = _json.dumps({"step": nxt, "steps": _steps, "total_step": SEED_DONE,
                                  "cur_step": None if nxt >= SEED_DONE else _cur})
        if nxt >= SEED_DONE:
            # 全部步骤完成 → done(汇总 part 简单展示)
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW() WHERE task_id=%s",
                    (new_params, _json.dumps({"result": {"parts": part, "finished": True}}, ensure_ascii=False), task_id))
            from routes.analysis_cache import invalidate
            invalidate("all")  # seed 完成 → 分析缓存即时失效
            # 重置后 agg 物化表需重建(防首屏走直查慢): 后台线程重建 90 天, 完成前 summary 陈旧检测降级直查
            try:
                import threading as _th2
                from routes.dashboard import _rebuild_day_agg as _rda
                _th2.Thread(target=_rda, args=(90,), daemon=True).start()
            except Exception as _e:
                    try_err('tasks', '静默降级', _e)
            return {"data": {"status": "done"}}
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (new_params, task_id))
        from routes.analysis_cache import invalidate
        invalidate("all")  # 每步数据变化 → 缓存失效
        return {"data": {"status": "running", "step": nxt, "part": part}}
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception as _e:
                try_err('tasks', '静默降级', _e)
        return {"data": {"status": "error", "error": str(e)[:200]}}

@router.post("/seed/fill")
@traced
async def seed_fill(request: Request):
    from routes.tasks import _new_task_id, _log_task  # 延迟(防循环)
    """空库生成完整演示数据(PA 原版逻辑移植, 异步分步: 任务表驱动, status 轮询续跑)"""
    channel = "jd"
    try:
        d = await request.json()
        channel = d.get("channel", "jd")
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    cnt = one("SELECT COUNT(*) AS c FROM orders") or {}
    pct = one("SELECT COUNT(*) AS c FROM products") or {}
    alt = one("SELECT (SELECT COUNT(*) FROM alerts)+(SELECT COUNT(*) FROM inventory) AS c") or {}
    if int(cnt.get("c") or 0) > 0 or int(pct.get("c") or 0) > 0 or int(alt.get("c") or 0) > 0:
        return ok({"requires_reset": True})
    task_id = _new_task_id("seed")
    try:
        # 创建 running 任务 + 执行步骤 0(商品/供应商, 秒级)
        import json as _json
        from routes.seed_fill import prepare_skus, seed_step, SEED_DONE
        execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                "VALUES(%s,'seed','running',%s,'{}',%s)",
                (task_id, _json.dumps({"step": 0, "total_step": SEED_DONE}), channel))
        skus_data = prepare_skus()
        today = now_cn()
        nxt, part = seed_step(0, today, skus_data, run_id=task_id)
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (_json.dumps({"step": nxt, "total_step": SEED_DONE}), task_id))
        return ok({"task_id": task_id, "started": True})
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception as _e:
                try_err('tasks', '静默降级', _e)
        return fail("种子填充启动失败: %s" % str(e)[:200])

@router.post("/seed/watermark")
@traced
async def seed_watermark_report(request: Request):
    """seed 水位与行数对账(只读): 看某轮 seed 每步"预期多少行 / 实际落了多少行"

    为什么要有这个口: seed 一次全量 90 万行级, "生成侧说写了 N 行" 和 "库里真有 N 行" 是两件事 ——
    唯一键冲突被 INSERT IGNORE 吃掉、校验拒写, 都会让实际少于预期, 而旧实现全程静默。
    差一行就是一行, 这里直接给出来, 不再靠"跑完了"推断。

    调用: POST /api/seed/watermark  {"run_id": "<seed 任务 id>"}
    """
    from routes.common import verify_token
    if verify_token(request.headers.get("X-Auth-Token", "")) != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('tasks', '静默降级', _e)
    run_id = str(d.get("run_id") or "").strip()
    if not run_id:
        return fail("需要 run_id(seed 任务的 task_id)", 400)
    from routes.seed_fill import wm_report
    return ok(wm_report(run_id))


@router.post("/seed/rollback")
@traced
async def seed_rollback(request: Request):
    """按水位回滚某轮 seed 的**新增行**(只删 id > hwm_before, 历史行不动)

    使用场景: seed 跑到第 8 步撞 90s 上限/网关 504 失败 —— 旧做法只能整库 reset 重来,
    把前 7 步已经正确的数据一起丢掉, 还要再付一次几十万行 DELETE 的 RU。
    这里只删"这一轮新增的", 已经存在的历史数据(哪怕也是 seed 生成的)一行不碰。

    安全约束:
      · target 只可能是 orders / inventory / inbound_records / outbound_records ——
        水位的语义是"插入型目标按 id 递增", upsert 型目标(products/suppliers)不登记水位, 无从回滚
      · 不带 steps → 回滚该轮已登记的全部步骤; 带 steps → 只回滚指定步骤
      · 回滚前要求该轮任务不是 running(避免和正在续跑的那一步打架)

    调用: POST /api/seed/rollback  {"run_id": "...", "steps": [7,8]}   (steps 可省)
    """
    from routes.common import verify_token
    if verify_token(request.headers.get("X-Auth-Token", "")) != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('tasks', '静默降级', _e)
    run_id = str(d.get("run_id") or "").strip()
    if not run_id:
        return fail("需要 run_id(seed 任务的 task_id)", 400)
    _steps = d.get("steps")
    if _steps is not None:
        try:
            _steps = [int(x) for x in _steps]
        except Exception:
            return fail("steps 必须是整数数组", 400)
    # 与正在续跑的任务互斥: running 状态下回滚会删掉本步刚写入的行, 白跑
    _t = one("SELECT status FROM sync_tasks WHERE task_id=%s", [run_id]) or {}
    if str(_t.get("status") or "") == "running":
        return fail("该任务仍在 running(正在续跑), 先等它结束或标为 error 再回滚", 409)
    from routes.seed_fill import wm_rollback
    _res = wm_rollback(run_id, _steps)
    # 回滚改了库存/订单 → 分析缓存必须失效, 否则页面还显示已删掉的数据
    from routes.analysis_cache import invalidate
    invalidate("all")
    return ok(_res)


@router.post("/seed/reset")
@traced
async def seed_reset(request: Request):
    from routes.tasks import _new_task_id, _log_task, _TABLES_RESET  # 延迟(防循环)
    """清空全部业务数据(恢复初始状态)"""
    channel = "jd"
    try:
        d = await request.json()
        channel = d.get("channel", "jd")
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    task_id = _new_task_id("reset")
    try:
        for t in _TABLES_RESET:
            # 分批删除(TiDB serverless 内存限制: 单条大 DELETE 全表会被取消)
            while True:
                n = execute("DELETE FROM `%s` LIMIT 10000" % t)
                if not n or int(n or 0) < 10000:
                    break
        _log_task(task_id, "reset", "done", channel, {"result": {"reset": "all"}})
        from routes.analysis_cache import invalidate
        invalidate("all")
        return ok({"task_id": task_id})
    except Exception as e:
        import traceback as _tb
        _log_task(task_id, "reset", "error", channel,
                  {"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]})
        return fail("重置失败: %s" % str(e)[:200])


# ── 单步运维口(2026-11-01) ──────────────────────────────────────────────────
def _inv_fingerprint():
    """inventory 指纹 —— 单步执行前后的不变量校验(比"算对了"更容易验证: 只要求没变)"""
    r = one("SELECT COUNT(*) AS n_rows, COALESCE(SUM(available_qty),0) AS avail, "
            "COALESCE(SUM(beginning_stock),0) AS bgn, "
            "COALESCE(SUM(month_inbound),0) AS mi, COALESCE(SUM(month_outbound),0) AS mo "
            "FROM inventory") or {}
    return {"rows": int(r.get("n_rows") or 0), "available": int(r.get("avail") or 0),
            "beginning": int(r.get("bgn") or 0),
            "month_inbound": int(r.get("mi") or 0), "month_outbound": int(r.get("mo") or 0)}


def _month_sum_diff(month_start):
    """月汇总与当月流水不一致的库存行数(按 仓型×SKU×69码×仓×渠道 严格对齐)
    改完应为 0; 若流水 warehouse_type 为空串 → 必然 >0, 让缺口显性化而非静默
    """
    _bad = 0
    for table, date_col, col in (("inbound_records", "inbound_date", "month_inbound"),
                                 ("outbound_records", "outbound_date", "month_outbound")):
        r = one("SELECT COUNT(*) AS c FROM inventory i "
                "LEFT JOIN (SELECT warehouse_type, sku, COALESCE(barcode,'') AS bc, warehouse, channel, "
                "                  SUM(quantity) AS q FROM `%s` WHERE `%s` >= %%s "
                "           GROUP BY warehouse_type, sku, COALESCE(barcode,''), warehouse, channel) f "
                "  ON f.sku=i.sku AND f.bc=COALESCE(i.barcode,'') AND f.warehouse=i.warehouse "
                " AND f.channel=i.channel AND f.warehouse_type=i.warehouse_type "
                "WHERE i.channel IN ('jd','other') AND i.`%s` <> COALESCE(f.q,0)"
                % (table, date_col, col), [month_start]) or {}
        _bad += int(r.get("c") or 0)
    return _bad


@router.post("/seed/step")
@traced
async def seed_step_run(request: Request):
    """单步重跑(admin 运维端口): 只重算派生量 —— 不 reset、不重灌、不动前端

    背景: /seed/fill 要求空库且从 step 0 全跑(实测 36.7 分钟), 只为验证一处派生逻辑
      也要清空重来, RU 开销过大。此口让运维只重算指定步骤。

    白名单: from_step ∈ {局部重跑段}，见下方 `_ALLOWED`，连续执行到末步
      · [月汇总]        只重算月汇总(最快, 前提: 流水已带 warehouse_type)
      · [落账,月汇总]   重算自有仓可用 + 重建批次, 再重算月汇总
      · [流水,落账,月汇总] 重建流水(带仓型) + 重算可用 + 月汇总
      · [库存,流水,落账,月汇总] 先补库存行(含多码拆行) 再走上面三步
      · 更早的段会写 products/orders → 拒绝; 库存段只限 seed 库(orders.data_source='seed' 守卫)
      · 流水段单跑不可: 流水变了而可用未重算 → 可用与流水脱节, 反而破坏四维
        (故强制连续到月汇总, 不接受只跑流水或 [流水,落账])
      · 告警/快照/物化段(实测 456s, 超平台单请求时限) → 拒绝
      · _seed_config 会 DELETE 并重写 replenishment_config/rules, 线上单跑会冲掉人工配置
        → 不复用 seed_step(月汇总), 只取其中的月汇总部分

    2026-11-05 段号重排: 订单从 4 段扩到 6 段(90 天), 库存/流水/落账/月汇总整体后移。
      原白名单硬编码 (5,6,7,8) 与段号耦合 → 现在从 seed_fill 的段常量推导, 再扩段也不用改这里。

    四维保障(返回里直接给出证据):
      · beginning_unchanged: 期初**任何情况下**都必须不变(铁律: 期初以导入为准)
      · available_unchanged: 只跑月汇总段时要求为 true(只许写 month_* 两列);
        落账段会按新流水重算可用, 允许变化, 由 month_diff 兜底验证自洽
      · month_diff: 月汇总 vs 流水的偏差行数, 前后对比, 正常应为 0

    调用:
      curl -X POST <host>/api/seed/step -H 'X-Auth-Token: <admin>' -d '{"from_step":8}'
    """
    from routes.common import verify_token
    if verify_token(request.headers.get("X-Auth-Token", "")) != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('tasks', '静默降级', _e)
    from routes.seed_fill import (_SEED_INV, _SEED_FLOW, _SEED_SETTLE, _SEED_MONTH)  # noqa: E402
    _LAST = _SEED_MONTH
    _ALLOWED = (_SEED_INV, _SEED_FLOW, _SEED_SETTLE, _SEED_MONTH)
    try:
        from_step = int(d.get("from_step") or d.get("step") or _SEED_SETTLE)
    except Exception:
        return fail("from_step 必须是整数", 400)
    if from_step not in _ALLOWED:
        return fail("from_step 只支持 %s, 且必须连续执行到 %d —— "
                    "%d 生成库存行(含多码拆行) / %d 重建流水 / %d 重算可用+批次 / %d 月汇总; "
                    "更早的段会写商品与订单, 流水段单跑会让可用与流水脱节"
                    % ("/".join(str(x) for x in _ALLOWED), _LAST,
                       _SEED_INV, _SEED_FLOW, _SEED_SETTLE, _SEED_MONTH), 400)
    # 库存段会向 inventory **插入**演示库存行(含双码拆行) —— 只允许在 seed 库执行,
    #   否则等于往真实客户库里塞演示库存。orders.data_source='seed' 是"这是 seed 库"的可靠标记。
    if from_step <= _SEED_INV:
        _sd = one("SELECT COUNT(*) AS c FROM orders WHERE data_source='seed'") or {}
        if int(_sd.get("c") or 0) <= 0:
            return fail("库存段会写入演示库存行, 仅限 seed 库"
                        "(orders 中 data_source='seed' 为空 → 判定为真实库)", 400)
    from routes.seed_fill import _sync_inv_month
    import time as _t
    month_start = now_cn().strftime('%Y-%m-01')
    _fixed = {}
    # 可选: 就地回填流水仓型(seed 库专用, 幂等)
    #   历史 seed 数据 inbound/outbound_records.warehouse_type 为空串(建表默认值, 生成时未写),
    #   不修则新聚合(按真实仓型)匹配不到 inventory → 月汇总不更新。
    #   业务定案见 seed_fill.py:427「出入库只属于自有仓」→ 空串视为 'own' 是**显式声明**,
    #   不是代码兜底: 必须由调用方传 fix_flow_wh_type=true 才执行, 且仅对 demo 数据成立。
    #   真实客户数据(可能存在 B 仓调拨流水)禁止使用, 应走导入端补仓型。
    if bool(d.get("fix_flow_wh_type")):
        for _tbl in ("inbound_records", "outbound_records"):
            try:
                _fixed[_tbl] = execute("UPDATE `%s` SET warehouse_type='own' "
                                       "WHERE COALESCE(warehouse_type,'')=''" % _tbl)
            except Exception as _ef:
                _fixed[_tbl] = "error: %s" % str(_ef)[:120]
    try:
        _ta = _t.time()
        before = _inv_fingerprint()
        bad_before = _month_sum_diff(month_start)
        _steps = []
        # ── 库存段: 生成库存行(新 SKU 才真正插入; 已存在行 INSERT IGNORE 跳过) ──
        #   这是"让 seed 生成器的新逻辑(多码拆行)真正落地"的唯一途径 —— 不跑它,
        #   inventory 里永远只有旧的单码行, 双码链路无从验证。
        if from_step <= _SEED_INV:
            import random as _rnd
            from routes.seed_fill import _seed_inventory, prepare_skus
            _rnd.seed(42)
            _t0 = _t.time()
            _nin = _seed_inventory(prepare_skus())
            _steps.append({"step": _SEED_INV, "name": "库存行生成",
                           "elapsed_s": round(_t.time() - _t0, 1),
                           "result": {"rows": _nin}})
        # ── 流水段: 重建出入库流水(带 warehouse_type) ──────────────────────
        if from_step <= _SEED_FLOW:
            import random as _rnd
            from routes.seed_fill import _seed_records
            _rnd.seed(42)   # 与 seed_step(流水段) 同语义: 每步独立确定性(否则依赖进程随机态)
            _t0 = _t.time()
            _ib, _ob = _seed_records()
            _steps.append({"step": _SEED_FLOW, "name": "出入库流水重建",
                           "elapsed_s": round(_t.time() - _t0, 1),
                           "result": {"inbound": _ib, "outbound": _ob}})
        # ── 落账段: 按新流水重算自有仓可用 + 重建批次(恢复 Σ批次=可用) ──────
        if from_step <= _SEED_SETTLE:
            from routes.seed_fill import seed_settle
            _t0 = _t.time()
            _st = seed_settle()
            _steps.append({"step": _SEED_SETTLE, "name": "可用重算+批次重建",
                           "elapsed_s": round(_t.time() - _t0, 1),
                           "result": _st})
        # ── 月汇总段: 月汇总(刻意跳过 _seed_config, 不碰补货参数/规则) ─────────
        _t0 = _t.time()
        groups = _sync_inv_month()
        _steps.append({"step": _SEED_MONTH, "name": "月汇总重算",
                       "elapsed_s": round(_t.time() - _t0, 1), "result": groups})
        after = _inv_fingerprint()
        bad_after = _month_sum_diff(month_start)
        # 诊断: 流水仓型为空 → 聚合组匹配不到 inventory(不会写错值, 但月汇总不更新)
        _empty_wt = int((one("SELECT COUNT(*) AS c FROM inbound_records "
                             "WHERE COALESCE(warehouse_type,'')=''") or {}).get("c") or 0)
        from routes.analysis_cache import invalidate
        invalidate("all")   # 月汇总/可用变化 → 分析缓存即时失效
        _hint = "ok"
        if _empty_wt:
            _hint = ("流水 warehouse_type 仍为空(%d 条) → 月汇总匹配不到库存行; "
                     "用 from_step=6 重建流水, 或传 fix_flow_wh_type=true(仅 seed 库)" % _empty_wt)
        elif bad_after:
            _hint = "月汇总仍有 %d 行与流水不一致, 需排查库存行是否缺失或仓型不匹配" % bad_after
        return ok({
            "from_step": from_step, "to_step": _LAST,
            "total_s": round(_t.time() - _ta, 1),
            "steps": _steps,
            "rows_delta": after["rows"] - before["rows"],
            "before": before, "after": after,
            "beginning_unchanged": before["beginning"] == after["beginning"],
            "available_unchanged": before["available"] == after["available"],
            "month_diff": {"before": bad_before, "after": bad_after},
            "flow_wh_type_empty": _empty_wt,
            "flow_wh_type_fixed": _fixed,
            "hint": _hint,
        })
    except Exception as e:
        import traceback as _tb
        return fail("单步执行失败: %s | %s" % (str(e)[:200], _tb.format_exc()[-400:]))
