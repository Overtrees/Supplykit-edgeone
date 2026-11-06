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


# ── 租约心跳(2026-11-05) ─────────────────────────────────────────────────
# 为什么不是"把租约调短就完事": 调太短, 正常跑 90s 的段会在中途被人抢走 → 两实例并发写
#   同一段; 调太长, 被网关切断后残影拖住所有人。**固定时长无法同时满足两者**, 因为
#   「请求还活着吗」这件事代码本身不掌握 —— 只有"还在写心跳"才是活着的证据。
#
# 所以: 时长交给心跳维持, TTL 只管"多久没心跳算死"。
LEASE_TTL = 30             # 多久没心跳判定持有者已死(心跳间隔的 3 倍)
LEASE_BEAT = 10            # 心跳间隔(秒)
# 兜底: 单次请求绝不该活过网关 120s。心跳线程多转一会无害(它只写租约),
#   但持有者本人超过这个时长还没结束, 说明已经异常 —— 由上层预算告警负责。
LEASE_BEAT_MAX = 115


def _tt_now():
    import time as _t
    return _t.time()


def _start_heartbeat(task_id, tok):
    """起一个后台线程续租约; 返回 stop 句柄(调用方在 finally 里 stop)

    为什么用线程而不是"每个 await 前续一次": 段内是纯同步 SQL 长跑(可用重算、批次重建
    一条语句几十秒), 没有 await 点可以插。线程是唯一能覆盖整个长查询期间的方案。

    归属校验: 续期语句带 `lease_token=本请求的令牌`, 只有当前持有者能续 ——
    否则上一任的心跳会替下一任续约, 两个实例同时在跑却永远不冲突(比没有锁更危险)。
    """
    import threading as _th
    _stop = {"v": False}

    def _beat():
        while not _stop["v"]:
            for _i in range(int(LEASE_BEAT * 2)):      # 100ms 粒度响应 stop
                if _stop["v"]:
                    return
                _tt_sleep(0.1)
            if _stop["v"]:
                return
            try:
                execute("UPDATE sync_tasks SET lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND) "
                        "WHERE task_id=%s AND lease_token=%s", [LEASE_TTL, task_id, tok])
            except Exception as _e:
                try_err('tasks', '租约心跳写入失败(下一拍重试)', _e)

    try:
        execute("UPDATE sync_tasks SET lease_token=%s WHERE task_id=%s", [tok, task_id])
    except Exception as _e:
        # 列还没加上(老库) → 心跳退化为"不校验归属", 仍然续期。
        # 迁移缺失不该让 seed 直接不可用; 但必须留痕, 否则归属保护静默消失。
        try_err('tasks', 'lease_token 列缺失, 心跳退化为无归属校验(请跑 schema 迁移)', _e)
    _t = _th.Thread(target=_beat, daemon=True)
    _t.start()

    def _stop_hb():
        _stop["v"] = True
        try:
            _t.join(timeout=1.0)
        except Exception:
            pass
    return _stop_hb


def _tt_sleep(s):
    import time as _t
    _t.sleep(s)



@router.get("/seed/fill/status")
@traced
def seed_fill_status(task_id: str = "", driver: str = "frontend", probe: int = 0):
    """任务状态(Makers 异步适配: running 时续跑下一步, 客户端轮询推进直到 done)

    probe=1 → **纯读**: 只 SELECT 一次返回进度快照, 不写 driver / 不抢锁 / 不推进。
      2026-11-05 竞态根治: 本端点此前是「查一次 = 推进一步 + 抢锁 + 写库」的副作用端点,
      却被 App 层 4 个轮询 + 看守 + DevMode/任务页同时打 —— 多页面并发时全部抢同一把锁,
      只有一个推进, 其余空转仍各写一次 params; 且 background 看守每 3s 必有请求在途,
      用户可见徽标恒为「同步中」。现在「看状态」一律走 probe(纯读), 只有 taskWatch 看守
      保留 drive 语义(全仓单一驱动源), 竞态与空转从根上消失。
      纯读分支不触碰 updated_at / lease / params, 对真实推进链路零影响。
    """
    # ① 纯读优先: 在任何写操作(mark_driver)之前返回, 保证零副作用
    if probe:
        if not task_id:
            return {"data": {"status": "not_found"}}
        _pr = one("SELECT status, params, created_at, updated_at, "
                  "TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS stalled_s, "
                  "UNIX_TIMESTAMP(NOW()) AS now_ts FROM sync_tasks WHERE task_id=%s", [task_id])
        if not _pr:
            return {"data": {"status": "not_found"}}
        import json as _jsonp
        try:
            _pjp = _jsonp.loads(_pr.get("params") or "{}")
        except Exception:
            _pjp = {}
        _steps_p = _pjp.get("steps") or []
        return {"data": {
            "status": _pr.get("status") or "not_found",
            "step": int(_pjp.get("step") or 0),
            "total_step": int(_pjp.get("total_step") or 0),
            "offset": int(_pjp.get("offset") or 0),
            "stalled_s": max(0, int(_pr.get("stalled_s") or 0)),
            "now_ts": int(_pr.get("now_ts") or 0),
            "last_step": str((_steps_p[-1] or {}).get("name") or "")[:80] if _steps_p else "",
            "created_at": str(_pr.get("created_at") or ""),
            "updated_at": str(_pr.get("updated_at") or ""),
            "probe": True,
        }}
    # ② 驱动模式(仅 taskWatch 看守调用): 保持原有 抢锁 → 续跑 语义, 一字未改
    from routes.tasks import _new_task_id, _log_task  # 延迟(防循环)
    from routes.tasks import _TABLES_RESET  # 延迟(表清单)
    from routes.common import mark_driver as _mkdrv
    _mkdrv(task_id, driver)
    if not task_id:
        return {"data": {"status": "not_found"}}
    row = one("SELECT status, params, TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS stalled_s, "
              "UNIX_TIMESTAMP(NOW()) AS now_ts FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"data": {"status": "not_found"}}
    status = row.get("status") or "not_found"
    if status in ("done", "error"):
        return {"data": {"status": status}}
    # 进度快照: **任何**返回(含抢不到锁)都带它, 前端不再需要靠"这一秒刚好推进了"猜状态
    import json as _json0
    try:
        _pj0 = _json0.loads(row.get("params") or "{}")
    except Exception:
        _pj0 = {}
    _step0 = int(_pj0.get("step") or 0)
    _total0 = int(_pj0.get("total_step") or 0)
    _last0 = (_pj0.get("steps") or [])
    _last_name0 = str((_last0[-1] or {}).get("name") or "")[:80] if _last0 else ""
    def _prog(extra=None):
        d = {"status": "running", "step": _step0, "total_step": _total0,
             "offset": int(_pj0.get("offset") or 0),
             "stalled_s": max(0, int(row.get("stalled_s") or 0)),
             "now_ts": int(row.get("now_ts") or 0),
             "last_step": _last_name0,
             "lease_held": bool(extra and extra.get("lease"))}
        if extra:
            d.update(extra)
        return {"data": d}
    # 防并发续跑: 原子抢占短租约 + **响应丢失可自愈**
    #
    # 2026-11-05 第二次线上事故(用户看到「卡在 14 几百秒」): 600s 长租约是上一轮为
    #   「同一步被两个轮询并发跑两遍」加的护栏, 但它引入了更坏的失效模式 ——
    #   单段一旦 >120s 被网关**硬杀进程**, `lease_until=NULL` 那行根本没执行到,
    #   租约就挂满 600s; 期间所有页面/轮询/cron 全被挡回 `running`, 进度心跳停跳,
    #   前端只能显示「刚刚」; 租约一过期又被多个驱动抢到, 从 DELETE 全表重跑同一个
    #   必超时的段 → 循环。用户看到的「几百秒」不是一次慢查询, 是重跑+锁死+再重跑。
    #
    # 现在两条一起改(不靠"再拆一段"碰运气):
    #   ① 租约 = 单段预算(90s) + 网关余量, 取 150s。进程被杀最多静止 2.5 分钟,
    #      而不是 10 分钟; 且每一段都被拆到 ≤90s(seed_fill 的 _FLOW_SEGS/_SETTLE_SEGS/
    #      _TAIL_SEGS), 正常路径根本不会被杀。
    #   ② **每段幂等**(流水按「渠道 × 表」精确 DELETE 后重写, 落账按「渠道 × 仓型」
    #      DELETE 后重建; 订单/库存走业务唯一键 upsert), 所以租约过期后重跑同一段
    #      不会写重数据 —— 这是短租约能成立的前提, 也是原来最怕并发的原因已消失。
    #   ③ 抢不到锁时不再返回裸 `running`, 而是带上 step / stalled_s / lease 信息,
    #      让前端能明确显示「第 N 段处理中, 已等待 Xs」, 而不是假装还在加载。
    # 2026-11-05 第三次事故(用户连问三次「为什么跑了 1 百多还在跑」): 150s 租约仍然会
    #   「死等」。复现路径完全可测 —— 三个并发请求打同一任务, 只有一个真拿到锁(实测
    #   A0/A2 在 1.4s 返回 lease_held=True), 拿锁那个跑满 85s 后被网关切断, 它写的
    #   `lease_until` 还留着 65s; 这 65s 里所有轮询都只能看到 step=11 / lease=True,
    #   stalled_s 一路涨到 149, 前端只能显示「🏃 已等待 149s」—— 而台上根本没人。
    #   **租约时长是猜的, 而"台上有没有人"是可观测的。**
    #
    # 改法: 租约持有者带**心跳**。执行方每 10s 把 `lease_until` 推到 NOW()+LEASE_TTL,
    #   于是 TTL 可以缩到 30s —— 被切断的请求最多留下 30s 残影(而不是 150s),
    #   正常路径的心跳又能让一段跑 90s 也不掉锁。三个数一起定, 缺一不可:
    #     · LEASE_TTL   = 30s  租约时长(心跳间隔的 3 倍, 容忍两次抖动不失锁)
    #     · LEASE_BEAT  = 10s  心跳间隔
    #     · 抢锁语义不变(原子 UPDATE + lease_until < NOW()), 只是把时长换成 TTL。
    #   心跳写入不碰 updated_at —— 那是「进度」时间轴(stalled_s 由它算), 混进心跳会让
    #   「卡住」永远显示成 0s, 反而看不出问题。
    _tok = "%s-%s" % (task_id, int(_tt_now() * 1000) % 100000000)
    try:
        _locked = execute(
            "UPDATE sync_tasks SET lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND) "
            "WHERE task_id=%s AND (lease_until IS NULL OR lease_until < NOW())",
            [LEASE_TTL, task_id])
        if not _locked:
            return _prog({"lease": True})  # 已有请求持有租约: 带进度返回, 不再裸 running
    except Exception as _e:
        try_err('tasks', '静默降级', _e)
        # 抢锁失败(DB 抖动)时**不能**继续执行 —— 否则就是无锁并发, 等于回到事故现场
        return _prog({"lease": True, "lock_error": str(_e)[:80]})
    _hb = _start_heartbeat(task_id, _tok)
    # running → 续跑下一步(每步 ≤90s, 函数 120s 上限内)
    try:
        import json as _json
        import time as _tt
        params = _pj0
        step = _step0
        from routes.seed_fill import (prepare_skus, seed_step, SEED_DONE,
                                      SEED_WINDOW_BUDGET_S, stage_label)
        _t0 = _tt.time()
        skus_data = prepare_skus()
        today = now_cn()
        # 2026-11-05 窗口接力: 游标 / 上窗天数 / 上窗耗时从 params 透传。
        #   seed_step 未跑完会返回同一个 step + part['_offset'], 前端看守看到 offset 前进
        #   就立刻发下一个请求接着跑; 段号(业务阶段)恒定, 数据范围只由游标决定。
        _off = int(_pj0.get('offset') or 0)
        _span = int(_pj0.get('span') or 0)
        _last_el = float(_pj0.get('last_elapsed') or 0)
        nxt, part = seed_step(step, today, skus_data, run_id=task_id,
                              offset=_off, span_hint=_span, last_elapsed=_last_el)
        _nxt_off = int((part or {}).get('_offset') or 0)
        _nxt_span = int((part or {}).get('_span') or 0)
        _sq = round(_tt.time() - _t0, 1)
        # 超预算留痕: 目标每窗口 ≤75s(网关 120s 留 45s 余量), 贴线就要看得见
        if _sq > SEED_WINDOW_BUDGET_S:
            try_err('tasks', 'seed 窗口超预算 step=%d off=%d %.1fs > %ds'
                    % (step, _off, _sq, SEED_WINDOW_BUDGET_S), '')
        # 2026-10-15 阶段耗时 steps + 进度中 cur_step(前端 TaskCard 可视化)
        #   内部游标字段(_offset/_span)不进展示名
        # 2026-11-06 结构改造: steps 的粒度 = **业务阶段**, 不是窗口。
        #   同一 step 的多个窗口合并成一条(elapsed/rows 累加, wins 记窗口数) ——
        #   seed 跑几十个窗口, 卡片也只会出现"阶段数"这么多行, 不随窗口膨胀。
        #   这比"只留最近 N 条 + 前端折叠"更彻底: 数据本身就没有冗余明细,
        #   params 大小也不再随窗口数线性增长。
        _win_total = int(params.get("win_total") or 0) + 1
        _rows_now = sum(v for v in (part or {}).values()
                        if isinstance(v, int) and not isinstance(v, bool))
        _steps = list(params.get("steps") or [])
        if _steps and int(_steps[-1].get("step", -1)) == step:
            _agg = dict(_steps[-1])
            _agg["elapsed"] = round(float(_agg.get("elapsed") or 0) + _sq, 1)
            _agg["wins"] = int(_agg.get("wins") or 1) + 1
            _agg["rows"] = int(_agg.get("rows") or 0) + _rows_now
            _steps[-1] = _agg
        else:
            _steps.append({"step": step, "name": stage_label(step),
                           "elapsed": _sq, "wins": 1, "rows": _rows_now})
        # 2026-11-05 完成判据改从 seed_fill.SEED_DONE 取 —— 段号重排后完成值多次变化,
        #   写死会让任务越过那个数后永不落 done, 空转烧 RU。
        # total_step 一并下发: 前端进度条此前也写死段数, step 超过就顶在 99% 不动
        _cur = {"name": stage_label(nxt) if nxt < SEED_DONE else "完成",
                "started": int(_tt.time())}
        new_params = _json.dumps({"step": nxt, "offset": _nxt_off, "span": _nxt_span,
                                  "last_elapsed": _sq, "steps": _steps,
                                  "win_total": _win_total, "total_step": SEED_DONE,
                                  "cur_step": None if nxt >= SEED_DONE else _cur,
                                  "step_started_ts": int(_tt.time())})
        if nxt >= SEED_DONE:
            # 全部步骤完成 → done(汇总 part 简单展示); 同时释放租约
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, "
                    "updated_at=NOW(), lease_until=NULL, lease_token='' WHERE task_id=%s",
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
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW(), lease_until=NULL, "
                "lease_token='' WHERE task_id=%s",
                (new_params, task_id))
        from routes.analysis_cache import invalidate
        invalidate("all")  # 每步数据变化 → 缓存失效
        return {"data": {"status": "running", "step": nxt, "offset": _nxt_off, "part": part,
                         "total_step": SEED_DONE, "elapsed": _sq, "stalled_s": 0,
                         "now_ts": int(_tt.time()), "lease_held": False}}
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW(), "
                    "lease_until=NULL, lease_token='' WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception as _e:
                try_err('tasks', '静默降级', _e)
        return {"data": {"status": "error", "error": str(e)[:200]}}
    finally:
        # 心跳必须**无条件**停: 早停一拍都无害(只是租约少续一次), 晚停则会让下一任
        #   持有者的租约被上一任替它续期 —— 归属校验已挡住这种写入, 但不能依赖它兜底。
        try:
            _hb()
        except Exception:
            pass

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
    from routes.seed_fill import (_SEED_MONTH, _SEED_INV_SEGS, _FLOW_SEGS,
                                  _SETTLE_SEGS, _TAIL_SEGS, SEED_DONE,
                                  _seed_inventory, prepare_skus, _seed_records,
                                  seed_settle, _seed_tail_piece)  # noqa: E402
    # 2026-11-05 段表扩到 27 段后, 运维口「从 from_step 连续跑到末步」会一个请求
    #   跑十几段, 必然踩穿 120s —— 正是这次事故的形状。改成**默认只跑指定那一段**,
    #   需要连续时显式传 single=false(且调用方自己承担超时风险)。
    _INV_NOS = tuple(x[0] for x in _SEED_INV_SEGS)
    _FLOW_NOS = tuple(x[0] for x in _FLOW_SEGS)
    _SETTLE_NOS = tuple(x[0] for x in _SETTLE_SEGS)
    _TAIL_NOS = tuple(x[0] for x in _TAIL_SEGS)
    _ALLOWED = _INV_NOS + _FLOW_NOS + _SETTLE_NOS + (_SEED_MONTH,) + _TAIL_NOS
    _LAST = _TAIL_NOS[-1]
    _SETTLE_FIRST = _SETTLE_NOS[0]
    try:
        from_step = int(d.get("from_step") or d.get("step") or _SETTLE_FIRST)
    except Exception:
        return fail("from_step 必须是整数", 400)
    if from_step not in _ALLOWED:
        return fail("from_step 只支持 %d~%d 的派生段(库存/流水/落账/月汇总/尾段); "
                    "更早的段会写商品与订单, 不在运维口范围"
                    % (_ALLOWED[0], _ALLOWED[-1]), 400)
    single = bool(d.get("single", True))
    to_step = int(d.get("to_step") or (from_step if single else _LAST))
    # 库存段会向 inventory **插入**演示库存行(含双码拆行) —— 只允许在 seed 库执行,
    #   否则等于往真实客户库里塞演示库存。orders.data_source='seed' 是"这是 seed 库"的可靠标记。
    if from_step <= _INV_NOS[-1]:
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
        # ── 统一段表驱动: 一段 = 一个可独立重跑的片, 逐段计时上报 ──────────
        #   与 seed_fill.seed_step 完全同一张表, 不再各抄一份段号/参数(历史上两处各自
        #   演化出过「批次基于重算前旧库存」的顺序错)。每段独立 random.seed(42):
        #   各段生成的是不同渠道/仓型/出-入类型的子集, 共用随机流会让单跑与连跑漂移。
        import random as _rnd
        from routes.seed_fill import _sync_inv_month as _sim
        _plan = {}
        for _no, _ch, _wg in _SEED_INV_SEGS:
            _plan[_no] = ("库存导入 %s/%s" % (_ch, _wg),
                          lambda ch=_ch, wg=_wg: _seed_inventory(prepare_skus(), channel=ch, wh_group=wg))
        for _no, _ch, _ft in _FLOW_SEGS:
            _plan[_no] = ("流水 %s/%s" % (_ch, _ft),
                          lambda ch=_ch, ft=_ft: _seed_records(channel=ch, flow_type=ft))
        for _no, _ch, _wg in _SETTLE_SEGS:
            _plan[_no] = ("落账 %s/%s" % (_ch, _wg),
                          lambda ch=_ch, wg=_wg: seed_settle(channel=ch, wh_group=wg))
        _plan[_SEED_MONTH] = ("月汇总重算", lambda: _sim())
        for _no, _kind in _TAIL_SEGS:
            _plan[_no] = ("尾段 %s" % _kind, lambda k=_kind: _seed_tail_piece(k))
        for _no in sorted(k for k in _plan if from_step <= k <= to_step):
            _name, _fn = _plan[_no]
            _rnd.seed(42)
            _t0 = _t.time()
            _res = _fn()
            if isinstance(_res, dict):
                _res = {k: v for k, v in _res.items() if k != 'barcode_aligned'}
            _steps.append({"step": _no, "name": _name,
                           "elapsed_s": round(_t.time() - _t0, 1), "result": _res})
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
                     "重跑流水段(%d~%d), 或传 fix_flow_wh_type=true(仅 seed 库)"
                     % (_empty_wt, _FLOW_NOS[0], _FLOW_NOS[-1]))
        elif bad_after:
            _hint = "月汇总仍有 %d 行与流水不一致, 需排查库存行是否缺失或仓型不匹配" % bad_after
        return ok({
            "from_step": from_step, "to_step": to_step, "single": single,
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
