"""task seed 模块(2026-10-11 拆分——tasks.py 减负; seed 接力/reset/status)
函数: seed_fill_status / seed_fill / seed_reset——依赖 routes.seed_fill 填充逻辑
_new_task_id/_log_task 工具仍留 tasks.py——函数内延迟 import(防循环)
"""
from datetime import datetime, timedelta, timezone
import json

from db import query, one, execute
from routes.common import ok, fail, traced, try_err

from fastapi import APIRouter, Request, Form, UploadFile, File
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
        from datetime import datetime, timezone as _tz
        params = _json.loads(row.get("params") or "{}")
        step = int(params.get("step") or 0)
        from routes.seed_fill import prepare_skus, seed_step
        _t0 = _tt.time()
        skus_data = prepare_skus()
        today = datetime.now(_tz.utc)
        nxt, part = seed_step(step, today, skus_data)
        _sq = round(_tt.time() - _t0, 1)
        # 2026-10-15 每步耗时 steps + 进度中 cur_step(前端 TaskCard 可视化)
        _steps = (params.get("steps") or []) + [{"name": str(part) or ("步骤%d" % step), "elapsed": _sq}]
        _cur = {"name": ("步骤%d" % nxt) if nxt < 10 else "完成", "started": int(_tt.time())}
        new_params = _json.dumps({"step": nxt, "steps": _steps, "cur_step": None if nxt >= 10 else _cur})
        if nxt >= 10:
            # 全部步骤完成 → done(汇总 part 简单展示)
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW() WHERE task_id=%s",
                    (new_params, _json.dumps({"result": {"parts": part, "finished": True}}, ensure_ascii=False), task_id))
            from routes.analysis_cache import invalidate_all
            invalidate_all()  # seed 完成 → 分析缓存即时失效
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
        from routes.analysis_cache import invalidate_all
        invalidate_all()  # 每步数据变化 → 缓存失效
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
        from routes.seed_fill import prepare_skus, seed_step
        from datetime import datetime, timezone as _tz
        execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                "VALUES(%s,'seed','running',%s,'{}',%s)",
                (task_id, _json.dumps({"step": 0}), channel))
        skus_data = prepare_skus()
        today = datetime.now(_tz.utc)
        nxt, part = seed_step(0, today, skus_data)
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (_json.dumps({"step": nxt}), task_id))
        return ok({"task_id": task_id, "started": True})
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception as _e:
                try_err('tasks', '静默降级', _e)
        return fail("种子填充启动失败: %s" % str(e)[:200])

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
        from routes.analysis_cache import invalidate_all
        invalidate_all()
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

    白名单: from_step ∈ {6,7,8}, 连续执行到 8
      · [8]      只重算月汇总(最快, 前提: 流水已带 warehouse_type)
      · [7,8]    重算自有仓可用 + 重建批次, 再重算月汇总
      · [6,7,8]  重建流水(带仓型) + 重算可用 + 月汇总 —— 验证 _seed_records 新逻辑走这个
      · 0-5 会写 products/orders/inventory(含期初), 单跑破坏期初/四维 → 拒绝
      · 6 不可单独执行: 流水变了而可用未重算 → 可用与流水脱节, 反而破坏四维
        (故强制连续到 8, 不接受 [6] 或 [6,7])
      · 9 重建快照/物化(实测 456s, 超平台单请求时限) → 拒绝
      · _seed_config 会 DELETE 并重写 replenishment_config/rules, 线上单跑会冲掉人工配置
        → 不复用 seed_step(8), 只取其中的月汇总部分

    四维保障(返回里直接给出证据):
      · beginning_unchanged: 期初**任何情况下**都必须不变(铁律: 期初以导入为准)
      · available_unchanged: 仅 [8] 时要求为 true(只许写 month_* 两列);
        [7,8]/[6,7,8] 会按新流水重算可用, 允许变化, 由 month_diff 兜底验证自洽
      · month_diff: 月汇总 vs 流水的偏差行数, 前后对比, 正常应为 0

    调用:
      curl -X POST <host>/api/seed/step -H 'X-Auth-Token: <admin>' -d '{"from_step":6}'
    """
    from routes.common import verify_token
    if verify_token(request.headers.get("X-Auth-Token", "")) != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('tasks', '静默降级', _e)
    try:
        from_step = int(d.get("from_step") or d.get("step") or 8)
    except Exception:
        return fail("from_step 必须是整数", 400)
    if from_step not in (6, 7, 8):
        return fail("from_step 只支持 6/7/8, 且必须连续执行到 8 —— "
                    "6 重建流水 / 7 重算可用+批次 / 8 月汇总; "
                    "0-5 会写期初, 6 单跑会让可用与流水脱节", 400)
    from routes.seed_fill import _sync_inv_month
    import time as _t
    month_start = datetime.now(timezone.utc).strftime('%Y-%m-01')
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
        # ── step 6: 重建出入库流水(带 warehouse_type) ──────────────────────
        if from_step <= 6:
            from routes.seed_fill import _seed_records
            _t0 = _t.time()
            _ib, _ob = _seed_records()
            _steps.append({"step": 6, "name": "出入库流水重建",
                           "elapsed_s": round(_t.time() - _t0, 1),
                           "result": {"inbound": _ib, "outbound": _ob}})
        # ── step 7: 按新流水重算自有仓可用 + 重建批次(恢复 Σ批次=可用) ──────
        if from_step <= 7:
            from routes.cleanse_write import _recalc_own_inventory as _rc
            from routes.seed_fill import _seed_batches
            _t0 = _t.time()
            _nrc = 0
            for _ch in ('jd', 'other'):
                _sk = [r.get('sku') for r in query(
                    "SELECT DISTINCT sku FROM inventory WHERE channel=%s AND warehouse_type='own'",
                    [_ch])]
                if _sk:
                    _rc(_ch, [x for x in _sk if x])
                    _nrc += len(_sk)
            _nb = _seed_batches()
            _steps.append({"step": 7, "name": "可用重算+批次重建",
                           "elapsed_s": round(_t.time() - _t0, 1),
                           "result": {"skus": _nrc, "batches": _nb}})
        # ── step 8: 月汇总(刻意跳过 _seed_config, 不碰补货参数/规则) ─────────
        _t0 = _t.time()
        groups = _sync_inv_month()
        _steps.append({"step": 8, "name": "月汇总重算",
                       "elapsed_s": round(_t.time() - _t0, 1), "result": groups})
        after = _inv_fingerprint()
        bad_after = _month_sum_diff(month_start)
        # 诊断: 流水仓型为空 → 聚合组匹配不到 inventory(不会写错值, 但月汇总不更新)
        _empty_wt = int((one("SELECT COUNT(*) AS c FROM inbound_records "
                             "WHERE COALESCE(warehouse_type,'')=''") or {}).get("c") or 0)
        from routes.analysis_cache import invalidate_all
        invalidate_all()   # 月汇总/可用变化 → 分析缓存即时失效
        _hint = "ok"
        if _empty_wt:
            _hint = ("流水 warehouse_type 仍为空(%d 条) → 月汇总匹配不到库存行; "
                     "用 from_step=6 重建流水, 或传 fix_flow_wh_type=true(仅 seed 库)" % _empty_wt)
        elif bad_after:
            _hint = "月汇总仍有 %d 行与流水不一致, 需排查库存行是否缺失或仓型不匹配" % bad_after
        return ok({
            "from_step": from_step, "to_step": 8,
            "total_s": round(_t.time() - _ta, 1),
            "steps": _steps,
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
