"""原生任务路由(方案 B): 任务列表 + 种子数据填充/重置 + 导出/下载

契约要点(与前端直连 fetch 对齐):
- POST /exports → 平铺 {ok, task_id}(HammerInsights 期待 d.task_id)
- POST /seed/fill → {ok, data:{task_id}} 或 {ok, data:{requires_reset:true}}
- GET  /seed/fill/status → {data:{status: pending|running|done|error|not_found}}
- GET  /tasks → {ok, data:[...]}(TaskPage 期待 d.data 数组)
- 数据文件无文件系统 → 存 export_files 表(惰性建表), download 从 DB 读出
"""
import csv
import io
import json
import random
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import Response

from db import query, one, execute
from routes.common import ok, fail, traced, try_err
from routes.task_export import create_export, export_status, _save_export_part, export_download, _build_export_rows, now_stamp, router as export_router
from routes.task_seed import seed_fill, seed_reset, seed_fill_status, router as seed_router  # tasks_seed_imported
from routes.ops import router as ops_router

router = APIRouter(tags=["tasks"])
router.include_router(export_router)  # 导出口/分队(拆分至 task_export 后 include)
router.include_router(seed_router)  # seed 分队(拆分至 task_seed 后 include)
router.include_router(ops_router)  # 运维单次数据治理(admin only, 2026-11-01)

# 2026-10-29 全排查(用户: 要排查就全排查根治): 原清单漏 6 表 ——
#   · sku_sales_stats = 日销物化(**补货/采购/断货/健康的数据源**) → 漏清 = 重置后卡片仍有数据
#   · data_freshness(新鲜度) · export_files(上传缓存) · eval_pending/agg_dirty(脏标记) · replenishment_config_history
# 保留: users(账号) · migration_log(迁移记录) —— 非业务数据, 不应重置
_TABLES_RESET = ["orders", "inventory", "products", "daily_sales_snapshot", "sku_sales_stats",
                 "alerts", "suppliers", "rules", "replenishment_config",
                 "replenishment_config_history", "purchase_orders",
                 "disposal_records", "batches", "quality_logs", "cleansing_errors",
                 "cleansing_templates", "custom_fields", "events", "inbound_records",
                 "outbound_records", "daily_stats", "warehouse_registry", "sync_tasks",
                 "orders_day_agg", "health_snapshot", "maintenance_log", "log_archives",
                 "gmv_flows", "data_freshness", "export_files", "eval_pending", "agg_dirty",
                 "eval_tasks",
                 # 2026-11-05 账期五表(原缺失 → 重置后账期残留, 演示库"看着重置了, 其实带着上轮账"):
                 #   · ledger_state 残留 `closed/2026-10` → 下轮 seed 的库存导入被判成
                 #     「已建账 + 窗口外」→ 期初全 0(线上实测 jd 10,100 行期初 0、other 211 万,
                 #     同一份数据两种命运, 就是这张表一行残留造成的);
                 #   · ledger_begin_adj / batch_opening / inventory_month 残留 → 期初校准明细、
                 #     批次月初余额、月结切片都是上轮的数, 与新一轮的库存完全对不上;
                 #   · seed_watermark 残留 → 水位对账拿旧 run_id 的预期值比新一轮的实际值。
                 # 重置的语义是"回到干净的空库", 账期状态属于数据的一部分, 不能例外。
                 "ledger_state", "ledger_begin_adj", "batch_opening", "inventory_month",
                 "seed_watermark",
                 # 2026-11-06 S5: 导入分片真源 + 评估清单(残留在表里=重置后旧任务"复活")
                 "import_stage", "eval_queue"]

_BRANDS_FOOD = ["禾味", "山泉", "椒香", "酱乡", "醋乡", "味源", "禾田", "青禾", "禾风",
                "谷香", "醇味", "鲜禾", "禾记"]
_BRANDS_SNACK = ["薯乐", "果脆", "禾果", "咔脆", "香脆"]
_BRANDS_DAILY = ["净洁", "柔白", "净香", "洁舒", "柔洁", "净白"]
_CATS = ["酱油", "醋", "酱料", "调味汁", "食用油", "饮料", "零食", "休闲食品", "日化清洁"]
_STORES = ["自营旗舰店", "直营店", "调味品专营店", "食品专营店", "日化专营店"]


def _new_task_id(prefix):
    return "%s_%d_%04d" % (prefix, int(time.time()), random.randint(0, 9999))


def _log_task(task_id, task_type, status, channel, result=""):
    execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
            "VALUES(%s,%s,%s,'{}',%s,%s)",
            (task_id, task_type, status, json.dumps(result, ensure_ascii=False), channel))


# ── 任务列表 ──────────────────────────────────────────────────────────────
# ── 维护任务单飞(§15.177) ────────────────────────────────────────────────
# 任务页连点「重建聚合」/「预热」时, 原来每次提交都起一个后台线程各算一遍:
#   线上实测连提 3 次 → 90 天聚合并发 → 全站读 p95 3,404ms(连 /api/health 都 3.2s),
#   因为同一实例的 Python 重计算共享一个 GIL。这两个 action 本身幂等,
#   重复提交没有任何额外收益, 只是把 CPU 再烧一遍。
#
# **必须用 DB 锁, 不能用模块级 dict**: 云函数会为并发请求开多个实例, 每个实例各有
#   一份内存状态 —— 线上实测内存锁下连提 3 次仍全部 started(3 个实例各抢到自己的锁)。
#   故改为 maint_lock 表: INSERT 成功=抢到; 已存在则条件 UPDATE(ts 过期才抢),
#   rowcount=1 才算抢到。租约 600s(90 天聚合线上单次最长 110s+), 实例被冻结时到期自动放行。
# 锁失败一律**放行**(返回 holder): 治理手段不能变成新的不可用来源。
_MAINT_LEASE = 600


def _maint_claim(key):
    """返回 holder 字符串(抢到) 或 None(已在跑)。"""
    import uuid
    h = uuid.uuid4().hex[:12]
    try:
        from db import execute as _e
        try:
            _e("INSERT INTO maint_lock(name, ts, holder) VALUES(%s, NOW(6), %s)", [key, h])
            return h
        except Exception:
            pass                      # 1062: 已存在 → 走条件更新
        n = _e("UPDATE maint_lock SET ts=NOW(6), holder=%s WHERE name=%s "
               "AND ts < DATE_SUB(NOW(6), INTERVAL " + str(int(_MAINT_LEASE)) + " SECOND)",
               [h, key])
        return h if int(n or 0) == 1 else None
    except Exception as _e2:
        try:
            try_err("tasks", "维护锁降级(放行)", _e2)
        except Exception:
            pass
        return h                      # 锁表不可用 → 不阻断功能


def _maint_release(key, holder):
    try:
        from db import execute as _e
        _e("DELETE FROM maint_lock WHERE name=%s AND holder=%s", [key, holder])
    except Exception:
        pass


@router.get("/tasks")
@traced   # 2026-11-09 缺口二: 异常必须走统一 traced 出口(200 + request_id), 否则冒泡到
          #   平台返回 EdgeOne HTML 500 页, 与其他接口形态不一致(§15.185 稳定复现 3 次)
def list_tasks(channel: str = "jd", limit: int = 50):
    # 2026-11-05 带 updated_at: 卡片右上角时间要表达"这个任务最近一次动是什么时候"。
    #   只返回 created_at 的话, seed 这种分步长跑(10~30 分钟)会一直显示"刚刚" ——
    #   因为 created_at 是**任务创建那一刻**, 后面每步推进都不改它。
    #   updated_at 在每一步续跑时被 NOW() 刷新(见 task_seed.seed_fill_status), 正好是"进度心跳"。
    rows = query("SELECT task_id, task_type, status, params, result, channel, created_at, updated_at "
                 "FROM sync_tasks WHERE channel=%s OR channel='' ORDER BY id DESC LIMIT %s",
                 [channel, limit])
    return ok(rows)




# ── 种子数据填充(异步分步) ───────────────────────────────────────────────


# ── 数据维护(admin) ────────────────────────────────────────────────────────
@router.post("/tasks/maintain")
async def tasks_maintain(request: Request):
    """数据维护入口(admin): action=rebuild_agg → 后台重建 orders_day_agg 90 天(重置后/陈旧修复)"""
    from routes.common import verify_token
    if verify_token(request.headers.get("X-Auth-Token", "")) != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    action = d.get("action") or "rebuild_agg"
    channel = d.get("channel") or "jd"
    mode = d.get("mode") or "bbcc"
    if action == "rebuild_agg":
        from routes.dashboard_maintain import _rebuild_day_agg as _rda3
        _k = "rebuild_agg|%s" % channel
        _h = _maint_claim(_k)
        if not _h:
            return ok({"started": False, "already_running": True, "action": "rebuild_agg"})

        def _bg_agg():
            try:
                _rda3(90)
            except Exception as _e:
                try_err("tasks", "重建聚合失败", _e)
            finally:
                _maint_release(_k, _h)

        threading.Thread(target=_bg_agg, daemon=True).start()
        from routes.analysis_cache import invalidate
        invalidate("orders")
        return ok({"started": True, "action": "rebuild_agg"})
    if action == "warmup":
        _kw = "warmup|%s" % channel
        _hw = _maint_claim(_kw)
        if not _hw:
            return ok({"started": False, "already_running": True, "action": "warmup"})
        # 预热各缓存(后台顺序调用 builder, 填 300s 共享表缓存)——部署/重置后调用一次, 首屏不撞重算
        def _w():
            try:
                from routes.dashboard import dashboard_summary, dashboard_aux, stock_risk
                from routes.replenishment import get_replenishment_suggestions
                from routes.purchase import purchase_suggestions
                dashboard_summary(channel, "", "", mode)
                dashboard_aux(channel, mode)
                stock_risk(channel)
                get_replenishment_suggestions(days=28, source="", mode=mode, channel=channel)
                purchase_suggestions(days=28, mode=mode, channel=channel)
            except Exception as _e:
                    try_err('tasks', '预热降级', _e)
            finally:
                _maint_release(_kw, _hw)

        threading.Thread(target=_w, daemon=True).start()
        return ok({"started": True, "action": "warmup"})
    return fail("未知 action: %s" % action)


# ── 数据重置 ──────────────────────────────────────────────────────────────
_EXPORT_DDL = ("CREATE TABLE IF NOT EXISTS export_files ("
               "id BIGINT PRIMARY KEY AUTO_INCREMENT, filename VARCHAR(128) DEFAULT '', "
               "content LONGTEXT, channel VARCHAR(20) DEFAULT 'jd', "
               "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
               "UNIQUE KEY uk_filename (filename))")











