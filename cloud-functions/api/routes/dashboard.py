"""原生 dashboard 路由: summary(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone
import time as _time

from fastapi import APIRouter

from db import query, one, execute
from routes.common import ok, PAID_STATUSES, SALES_STATUSES, DEMAND_STATUSES, FUNNEL_STAGES, traced, try_err, INVENTORY_COLS as _INV_COLS
from routes.common import ch_default_mode  # 2026-11-06 P0-2: mode 缺省按渠道默认(不再硬编码 bbcc)
from routes.common import track_run  # 2026-11-02 S1-2: 链路运行流水

router = APIRouter(tags=["dashboard"])

# 2026-11-06: 维护类函数(_track_data/_snapshot_step/_rebuild_day_agg)已由
#   core/maintenance.py 在后台线程调用, 读路径不再直接使用 —— 不留无用导入。
# _grade_risk 虽然本文件已不直接用, 但 local_test 从这里 import 它 —— 模块再导出, 不许删
from routes.dashboard_stock_risk import _stock_risk, _grade_risk
from routes.dashboard_health import _health_index, _sync_health_snapshot_async
from routes.dashboard_bundle import _q_bundle, _split_bundle  # 红线A: 六合取数落独立文件
from routes.analysis_cache import register as _register_cache, cache_get as _cache_get
from core.clock import now_cn
_register_cache(lambda: (_summary_cache.clear(), _aux_cache.clear(), _risk_cache.clear(), _accel_cache.clear()))

_PAID = tuple(PAID_STATUSES)


def _status_cond(col="order_status"):
    """GMV 已支付口径条件"""
    return "%s IN (%s)" % (col, ",".join(["'%s'" % s for s in _PAID]))


def _build_funnel_stages(cnt_by_status: dict, total: int) -> list:
    """把 order_status 原始值计数摊成订单阶段分布。

    - 第 1 档「总订单」= SUM(所有状态) —— 唯一的聚合值, 不来自某一状态;
    - 其余各档 = `FUNNEL_STAGES` 里列出的**原始状态值**, 与订单表格状态列逐字一致;
    - `FUNNEL_STAGES` 之外还存在的状态(历史脏值/未来新增值)不丢弃, 按原值追加到末尾,
      保证「各档合计 == 总订单」。这条是 2026-11-04 修的: 原来写死 4 档且漏「申请退款」,
      4 档合计 105951 vs 总订单 118091, 差额白白消失。

    percentage: 占总数比例; conversion: 与上一档的比值(卡片里显示为转化率)。
    """
    stages = [("总订单", total, 100.0)]
    for n in FUNNEL_STAGES:
        v = int(cnt_by_status.get(n, 0) or 0)
        stages.append((n, v, round(v / total * 100, 1) if total else 0))
    known = set(FUNNEL_STAGES) | {"总订单"}
    for n, v in sorted(cnt_by_status.items(), key=lambda kv: -int(kv[1] or 0)):
        if n in known or not v:
            continue
        stages.append((n, int(v), round(int(v) / total * 100, 1) if total else 0))
    out = []
    for i, (n, v, pct) in enumerate(stages):
        prev = stages[i - 1][1] if i > 0 else total
        out.append({"name": n, "value": v, "percentage": pct,
                    "conversion": round(min(v / prev * 100, 100), 1) if prev else 0})
    return out


_summary_cache = {}
_SUMMARY_TTL = 30





def _daily_maintenance(channel="jd"):
    """应用层每日维护(治本: 不依赖 edgeone schedules——实测未自动触发)
    抢占 maintenance_log(date+task 主键, INSERT IGNORE 多实例安全), 已维护则跳过(1 次轻插入)
    内容: 快照/agg 数据追平(每 miss) + 每日例行(日志治理等)"""
    try:
        _today = now_cn().strftime("%Y-%m-%d")
        _got = execute("INSERT IGNORE INTO maintenance_log(`date`, task) VALUES(%s, 'daily')", [_today])
        if not _got:
            return  # 今天已维护(本实例或其他实例)
        # 2026-11-02 S1-2: 维护耗时/成败留痕 —— 这是"每天第一次打开看板会卡一下"的那条路径
        with track_run("daily_maintenance", channel) as _mrec:
            # ② digest 预填(每日维护——get_sales_digest 填表缓存——健康/低库存/规则/补货/断货消费方首访命中——首冷降频)
            try:
                from biz.sales import get_sales_digest
                # 2026-10-30 渠道数据驱动: 预填所有启用渠道(原硬编码 jd/other 双渠道)
                from routes.common import ch_codes as _chc
                for _c0 in _chc():
                    get_sales_digest(_c0)
            except Exception as _e:
                    try_err('dashboard', 'digest 预填降级', _e)
            # ③ quality_logs 膨胀治理(双保险, 不依赖 cron cleanup-logs)
            #   2026-11-02 S1-3: 与 cron 共用 common.prune_quality_logs(按天保留30天 + 2万条硬上限)。
            #   原实现「超 500 截断至 300」会把当天写的排障证据也一起冲掉(id 曾达 159 万级——
            #   正是这种写删循环造成的), 现按时间保留。
            try:
                from routes.common import prune_quality_logs as _prune
                _mrec.detail = _prune()   # 顺手把清理结果记进 pipeline_runs(rows/detail 可查)
            except Exception as _e:
                    try_err('dashboard', '日志清理降级', _e)
            # ④ export_files/sync_tasks 导出任务清理(2026-10-09: 分片+文件越积越多——7 天保留)
            try:
                execute("DELETE FROM export_files WHERE created_at < DATE_SUB(NOW(), INTERVAL 7 DAY)")
                execute("DELETE FROM sync_tasks WHERE task_type='export' "
                        "AND status IN ('done','error') "
                        "AND updated_at < DATE_SUB(NOW(), INTERVAL 7 DAY)")
            except Exception as _e:
                    try_err('dashboard', '导出文件清理降级', _e)
            # ⑤ 日志归档: 昨天 user/dev/all 生成 md 存 log_archives(历史文件回溯) + 清理 90 天前归档(保留周期)
            try:
                from datetime import timedelta as _td2
                _yest = (now_cn() - _td2(days=1)).strftime("%Y-%m-%d")
                from routes.misc import _export_md as _em
                for _sc in ("user", "dev", "all"):
                    _d3, _cnt3, _md3 = _em(_yest, _sc, "")
                    execute("INSERT INTO log_archives(`date`, `scope`, markdown, `count`, updated_at) "
                            "VALUES(%s,%s,%s,%s,NOW(6)) ON DUPLICATE KEY UPDATE "
                            "markdown=VALUES(markdown), `count`=VALUES(`count`), updated_at=NOW(6)",
                            [_d3, _sc, _md3, _cnt3])
                _cut = (now_cn() - _td2(days=90)).strftime("%Y-%m-%d")
                execute("DELETE FROM log_archives WHERE `date` < %s", [_cut])
            except Exception as _e:
                    try_err('dashboard', '静默降级', _e)
    except Exception as _me:
        # 2026-11-02 S0: 失败必须回滚抢占标记 —— 原实现失败后标记仍在, 当天永久跳过,
        # 而失败只写一条 quality_logs 就再无痕迹(用户表现为"数据不更新"却查不出原因)。
        # 同文件 _daily_rules_guard 一直是有回滚的, 两种写法并存本身就是隐患。
        try:
            _td = now_cn().strftime("%Y-%m-%d")
            execute("DELETE FROM maintenance_log WHERE `date`=%s AND task='daily'", [_td])
        except Exception as _e2:
            try_err('dashboard', '回滚维护标记失败', _e2)
        try:
            execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                    "VALUES('maint','error',%s,%s,'dash')",
                    ("daily_maintenance 失败", str(_me)[:200]))
        except Exception as _e:
                try_err('dashboard', '静默降级', _e)


def _daily_rules_guard():
    """每日规则应用层兜底(不依赖 schedules——平台实测从未触发):
    maintenance_log(date+'daily_rules' 主键) 抢占, 抢到则后台线程执行 run_daily_rules()(不阻塞 summary);
    失败回滚抢占标记, 下次 summary 请求重试(多实例安全)
    2026-10-17 数据就绪检测: seed 完成后首跑时物化表可能空/快照旧 → 空评估(孤儿0/触发0)
    却占位挡后续——先查快照有数据才占位, 未就绪不占位(下次 summary 重试)

    2026-11-01 库存指纹(实测 bug 修复): 原"一天一次"占位, 03:33 评估时库存仅 ~2000 行,
    之后导入到 9000 行, 当天再也不评估 → 看板低库存卡长期显示旧快照(用户看到"无数据")。
    现改为: 占位记录同时存库存指纹(行数+最大变更时间), 指纹变化则允许当天重评估(仍串行,
    靠 UPDATE ... WHERE fingerprint=<旧指纹> 原子抢占, 多实例不重复)。

    2026-11-06 指纹改为"成功后才落"(线上 5.5 小时空窗的根因):
    原写法在**执行之前**就把当前指纹写进 details —— 实例被平台冻结时 `run_daily_rules` 没跑完,
    但 details 已经是"最新依据", 守卫此后每次都判定"今天已按此依据评估过"直接返回。
    线上实证: 11:37 发起 → 实例冻结 → 16:38 被挂起清理标 timeout → 17:22 才靠 daily_rules 兜底,
    中间 5.5 小时告警是旧的, 而库存/订单在这期间早就变了。
    三态占位与 CAS 现在收敛在 `core.maintenance.claim_rules / settle_rules` —— cron 路由与
    本兜底共用同一行、同一套语义(否则 cron 跑完 details 为空, 这里会再跑一遍)。"""
    try:
        # 数据就绪检测: 快照有行才评估(seed/reset 后场景——避免空库空评估占位毒化)
        try:
            _snap = one("SELECT COUNT(*) c FROM daily_sales_snapshot") or {}
            if int(_snap.get("c") or 0) <= 0:
                return  # 数据未就绪——不占位, 下次 summary 重试
        except Exception:
            pass  # 查表失败不阻塞(继续尝试)
        from core.maintenance import inventory_fingerprint, claim_rules, settle_rules
        # 库存指纹: 行数 + 正可用行数 + 可用量总和 —— 任一变化即视为"评估依据已变"
        _fp = inventory_fingerprint()
        if not _fp:
            return  # 指纹查不到就本轮不动(空指纹会与 details='' 撞车, 误判成"已完成")
        _claimed, _token = claim_rules(_fp)
        if not _claimed:
            return  # 今天已按此依据评估完成 / 另一实例正持有 / 已被别人抢到

        def _run():
            try:
                from routes.cron import run_daily_rules
                _r = run_daily_rules()
                # 成功才把指纹落地(失败或冻结时它停留在 #run#, 超时后会被下次请求抢回重跑)
                settle_rules(_token, _fp, ok=True)
                execute("INSERT INTO quality_logs(log_type, level, message, source) "
                        "VALUES('maint','info',%s,'dash')",
                        ("应用层兜底 daily-rules 完成: 孤儿清理 %d, 规则触发 %d"
                         % (_r.get("orphan_cleaned") or 0, len(_r.get("rules_triggered") or [])),))
            except Exception as _e:
                try:
                    # 失败 → 改回"待跑"(不是删行: 保留当日痕迹, 且 CAS 语义统一)
                    settle_rules(_token, _fp, ok=False)
                    execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                            "VALUES('maint','error',%s,%s,'dash')",
                            ("daily_rules 应用层兜底失败", str(_e)[:200]))
                except Exception as _e:
                        try_err('dashboard', '静默降级', _e)

        import threading
        threading.Thread(target=_run, daemon=True).start()
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)




@router.get("/dashboard/health-trend")
@traced
def health_trend(channel: str = "jd", days: int = 14):
    """健康分数趋势(近 N 天每日 score/own/platform/bc, 旧→新; 数据源 health_snapshot)"""
    try:
        rows = query("SELECT `date`, score, own_score, platform_score, bc_score "
                     "FROM health_snapshot WHERE channel=%s ORDER BY `date` DESC LIMIT %s",
                     [channel, min(int(days), 60)])
    except Exception:
        rows = []
    _out = []
    for r in reversed(rows or []):
        _d = str(r.get("date") or "")[:10]
        if not _d or str(r.get("score") or "") in ("-1", ""):
            continue
        _out.append({"date": _d, "score": int(r.get("score") or 0),
                     "own": int(r.get("own_score") or -1), "platform": int(r.get("platform_score") or -1),
                     "bc": int(r.get("bc_score") or -1)})
    return ok(_out)


@router.get("/dashboard/unit-state")
@traced
def dashboard_unit_state(channel: str = "jd", mode: str = "", detail: str = ""):
    """判定读模型诊断口(只读) —— 四维逐项可查, 不靠猜

    字段含义见 `core/unit_store.status()`: gen/fp/units/alerts/stale/fp_match/available。
    `detail=1` 附带异常明细前 20 行, 用于核对「卡片计数 == 列表行数」。
    """
    from core import unit_store as _US
    _st = _US.status(channel, mode)
    if detail:
        _st["alerts_sample"] = _US.alerts_of(channel, mode, limit=20) or []
    return ok(_st)


@router.get("/dashboard/summary")
@traced
def dashboard_summary(channel: str = "jd", start_date: str = "", end_date: str = "", mode: str = "",
                      part: str = ""):
    """看板汇总(30s 共享表缓存——TiDB 表跨实例一致; 写操作按域失效(2026-11-02 S2-1), 数据变化最多 30s 可见)

    2026-11-06 **冷算根治**: 把"维护"从**用户请求路径**里搬出去

    ## 原来的问题(为什么反复退化)

    这个缓存的 miss 分支里塞了四件与"回答用户问题"无关的事:

    | 副作用 | 每次 miss 都跑 | 性质 |
    |---|---|---|
    | `_track_data` 数据追平(含 60 天窗口完整性自愈、必要时 `_rebuild_day_agg(90)`) | 是 | 维护 |
    | `_daily_maintenance` digest 预填 / 日志治理 / 导出文件清理 / 日志归档 | 每天 1 次 | 维护 |
    | `_daily_rules_guard` 库存指纹 → 可能触发全量规则评估 | 指纹变化时 | 维护 |
    | `_sync_health_snapshot` 健康分快照写入 | 每次 miss | 写入 |

    它们被记进"summary 这个请求"的耗时里 —— 线上实测: **查询 1.21s + 组装 0.21s = 1.42s,
    而接口 5.17s**。也就是说 **73% 的响应时间不在业务计算里**。
    更糟的是 `_rebuild_day_agg(90)` 这类重活会在**用户点开看板的那一刻**同步跑。

    这就是"每一轮性能优化都做过、又反复退化"的机制: 优化的是**计算**(往返次数/投影列数,
    见 §15.120), 而**维护量**会随功能增加而重新长回请求路径里 —— 计算优化得再好,
    也抵不住一条 `_rebuild_day_agg(90)` 随机插在冷算上。
    **计算的账和维护的账必须分开放, 否则永远在追一个移动靶。**

    ## 现在的口径

    · 请求路径只做两件事: 取数(命中缓存就直接返回) + 组装。**零维护、零写入**。
    · 维护挪到 `core.maintenance.kick()` —— 后台线程 + 全局限频 + 已有原子抢占
      (`maintenance_log` 主键 / 库存指纹), 多实例安全, 失败不影响本次响应。
    · 健康分快照改为**跟随评估完成**写入(它本来就只依赖 health_index), 不再挂在读路径上。

    `part=main/charts` 拆视野、缓存键、TTL、失效口径**一律不变** —— 这是纯粹的
    "把不该在这里的东西挪走", 不改任何业务数值。
    """
    # 2026-11-06 P0-2: mode 缺省**按渠道默认**走, 不再硬编码 bbcc。
    #   前端 DashboardPage 曾有三处手拼 summary URL 漏传 mode, 而后端缺省是 "bbcc" ——
    #   非京东渠道(other/tb/pdd)的 summary 于是一直按 BBCC 周期算: 健康分、低库存口径
    #   全错, 而且同一屏 store.loadAll 那份带了正确 mode, 两者交替覆盖 dashboard 与
    #   health_snapshot(同日同渠道两个口径来回写), 趋势图出现锯齿。
    #   前端已收敛为 dashUrl() 单一构造点; 这里是后端兜底, 防其它调用方(脚本/第三方)再漏。
    mode = mode or ch_default_mode(channel)
    _key = "dash_summary|%s|%s|%s|%s" % (channel, start_date, end_date, mode)

    def _b():
        # 只算业务值。维护/守卫/快照写入全部移出(见 docstring)。
        _r = _build_summary(channel, start_date, end_date, mode)
        # 健康快照: 写库动作, 但**依赖本次刚算出的值**且极轻(UPDATE/INSERT 一行)。
        #   仍在这里会污染耗时(且每次 miss 一次), 所以挪到后台线程异步补。
        _sync_health_snapshot_async(channel, (_r or {}).get("health_index")
                                    if isinstance(_r, dict) else None)
        return _r

    _result = _cache_get(_key, _SUMMARY_TTL, _b)
    # 维护触发: **无论命中与否**都只做一次"要不要起后台线程"的轻判断(限频在 kick 内部),
    #   不再阻塞本次响应。放在缓存之后 → 命中路径也不会因为维护而变慢。
    try:
        from core.maintenance import kick as _kick_maint
        _kick_maint(channel)
    except Exception as _e:
        try_err('dashboard', '后台维护触发降级(不影响本次响应)', _e)
    # 2026-10-28 首屏提速: 同一缓存键派生两份视野(同源同快照——四维一致性天然保证, 不跨时刻)
    # part=main → 主项(卡片数字, 组装轻, 首屏快); part=charts → 图表项(趋势/店铺/品牌/漏斗, 组装重);
    # 不带 part → 全量(向后兼容, 老客户端/其他调用方不受影响)
    if part and isinstance(_result, dict):
        # 实测顶层 10 键: summary/health_index/periods(含 *_trend 趋势) = 主项; 图表 7 键 = charts
        _MAIN = ("summary", "health_index", "periods")
        _CHART = ("trend", "stores", "brands", "period_stores", "period_brands",
                  "funnel", "period_funnel")
        _keep = _MAIN if part == "main" else _CHART if part == "charts" else None
        if _keep is not None:
            return ok({k: v for k, v in _result.items() if k in _keep})
    return ok(_result)






def _q_agg(channel, days60, d1):
    return query("SELECT `date` AS d, order_status, store, SUM(gmv) AS g, SUM(subsidy) AS sub, SUM(cnt) AS cnt "
                 "FROM orders_day_agg WHERE channel=%s AND `date` >= %s AND `date` < %s "
                 "GROUP BY `date`, order_status, store", [channel, days60, d1])


def _q_recent(channel, d1):
    return query(
        "SELECT DATE(ordered_at) AS d, order_status, store, "
        "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
        "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)) AS sub, COUNT(*) AS cnt "
        "FROM orders WHERE channel=%%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %%s "
        "GROUP BY DATE(ordered_at), order_status, store" % (_status_cond(), _status_cond()),
        (channel, d1 + " 00:00:00"))


def _q_inventory(channel):
    # 全量行(health out_of_stock 需 avail<=0 缺货行; 低库存消费方自行过滤 avail>0)
    # 2026-10-29 按码: 补 barcode + warehouse(原只有 sku/仓型/数量, 健康与低库存无法按码/按仓判定)
    # 2026-11-02 冷算: 只取**实际被消费的 5 列**。原 INVENTORY_COLS 是 12 列(含 product_name
    #   VARCHAR(128)、beginning_stock、c_transit、locked_qty…), 实测同一份 9003 行:
    #   12 列 1.44s / 6 列 0.45s / 仅 COUNT 0.41s —— 差的是**传输量**, 不是扫描。
    #   而这里的两处消费方(_health_index 分档、低库存计数)只用 sku/仓型/可用量,
    #   barcode/warehouse 供 _check_cols 断言保留。缺货列表(dashboard.py 另一处)仍用全列。
    # 2026-11-06 S3: 补 in_transit_qty / safety_qty —— 判定口径是"覆盖量(可用+在途) vs
    #   安全线(手工优先, 否则 ds×周期)", 没有这两列就只能拿 avail 单打独斗(旧的错口径)。
    #   两列 int 在 9 万行上约 +1MB, 换来与告警规则同源 —— 值得。
    _rows = query("SELECT sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, "
                  "available_qty, in_transit_qty, safety_qty FROM inventory WHERE channel=%s",
                  [channel])
    try:
        from routes.common import check_cols as _cc
        _cc(_rows, ("barcode", "warehouse"), "dash", "看板物料")
    except Exception:
        pass
    return _rows


def _q_digest(channel, mode=None):
    """日销切片(`core.metrics.load`) —— 判定类口径的唯一取数入口

    原返回 `{sku: fused}`(`get_sales_digest`): 只有 `__C__` 一个维度的 SKU 级一个数。
    那一个数被拿去判 7 个仓 × 每个 69码 —— 逐仓分虚低、单元不一致都是从这里来的。
    现在按**实际出现的仓型**取对应维度(见 `metrics.dims_needed`), 且只取 ds28 一列。
    """
    try:
        from core import metrics as _M
        return _M.load(channel, dims=_M.dims_needed(None, channel, mode), fields=("ds28",))
    except Exception:
        return None


# ── 2026-11-02 冷算收口: 把"同源重复往返"合成一条 ──────────────────────────
# 线上 perf_debug 实测(build_summary_agg): "查询 0.3s / 组装 1.15s" —— 而组装里 Python 只占 0.015s
# (cProfile: _assemble tottime 0.015s / cumtime 6.15s), 其余全是逐条 SQL 的往返固定开销
# (TiDB 与函数不同区, 单次 ~57ms)。所以降耗 = 降**往返次数**, 不是降计算量。
# 三条合并都保持原口径逐字不变(同 _status_cond / 同 CASE 净额表达式 / 同 JOIN / 同 deleted_at 过滤)。

_FLOW_NET = ("CASE WHEN flow_type IN ('货款到账','交易收入','结算入账') THEN amount "
             "WHEN flow_type IN ('退款扣回','扣点扣款','技术服务费','调价补差') THEN -ABS(amount) "
             "ELSE amount END")


def _q_counts(channel):
    """概览三个计数 → 一条(原 alerts/products/suppliers 各一次 COUNT, 3 次往返)

    标量子查询在同一条语句、同一快照内取值 —— 与原逐条 COUNT 的口径逐字一致。
    """
    # 2026-11-06 供应商计数补 channel: alerts/products 都按渠道过滤, 唯独 suppliers 漏了,
    #   于是切到任一渠道都显示 20(=10 供应商 ×2 渠道), 与商品/告警的渠道口径不一致。
    #   seed 的 supplier_code 带 -JD/-OTHER 且写入侧注入 channel, 这里按行过滤即可。
    r = one("SELECT "
            "(SELECT COUNT(*) FROM alerts WHERE channel=%s AND status='active') AS alerts_c, "
            "(SELECT COUNT(*) FROM products WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')) AS products_c, "
            "(SELECT COUNT(*) FROM suppliers WHERE channel=%s) AS suppliers_c",
            [channel, channel, channel]) or {}
    return {"alerts": int(r.get("alerts_c") or 0),
            "products": int(r.get("products_c") or 0),
            "suppliers": int(r.get("suppliers_c") or 0)}


def _q_cfg(channel):
    """补货参数配置(健康分与低库存动态安全线共用一份 —— 原两处各查一次)"""
    try:
        return {r.get("key"): r.get("value") for r in
                query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])}
    except Exception as _e:
        try_err('dashboard', '补货配置加载降级', _e)
        return {}


def _q_flows_month(channel, d30, d31):
    """月度回款: 总额 + 按日 + 按店 → 一条(原 3 次扫描同一时间窗)

    口径不变: 同一个 `_FLOW_NET` 净额表达式、同一个 arrived_time 区间。
    返回 (总额|None, {日: 净额}, {店: 净额}); 无流水返回 (None, None, None) —— 与原
    "COUNT(*)>0 才算有流水" 等价(空结果即无流水)。
    """
    rows = query("SELECT DATE(arrived_time) AS d, shop, COALESCE(SUM(" + _FLOW_NET + "),0) AS v "
                 "FROM gmv_flows WHERE channel=%s AND arrived_time>=%s AND arrived_time<=%s "
                 "GROUP BY DATE(arrived_time), shop",
                 [channel, d30 + " 00:00:00", d31 + " 23:59:59"])
    if not rows:
        return None, None, None
    _tot = 0.0
    _by_day = {}
    _by_shop = {}
    for r in rows:
        _v = float(r.get("v") or 0)
        _dd = str(r.get("d") or "")[:10]
        _sh = str(r.get("shop") or "")
        _tot += _v
        _by_day[_dd] = _by_day.get(_dd, 0.0) + _v
        _by_shop[_sh] = _by_shop.get(_sh, 0.0) + _v
    return (round(_tot, 2),
            {k: round(v, 2) for k, v in _by_day.items()},
            {k: round(v, 2) for k, v in _by_shop.items()})


def _q_brands(channel, d_from, d_to=""):
    """品牌 GMV: 按(日, 品牌)一次取回, 内存切周期 —— 原 today/week/month(/custom) 各查一次

    SQL 与 `_brands_range` 逐字同源(`_status_cond` 两处、LEFT JOIN products、deleted_at 过滤),
    只多分了 `DATE(ordered_at)` 这一维; 周期切片在内存做, 结果与原逐周期查询一致。
    """
    _sql = ("SELECT DATE(o.ordered_at) AS d, COALESCE(p.brand,'') AS brand, "
            "SUM(IF(%s, o.total_amount - COALESCE(o.discount_amount,0) "
            "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS g, "
            "SUM(IF(%s, COALESCE(o.subsidy_amount,0), 0)) AS sb, "
            "SUM(IF(o.order_status = '申请退款', o.total_amount - COALESCE(o.discount_amount,0) "
            "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS rf "
            "FROM orders o LEFT JOIN (SELECT DISTINCT sku, brand, channel FROM products "
            "WHERE brand IS NOT NULL AND brand != '') p ON o.sku=p.sku AND o.channel=p.channel "
            "WHERE o.channel=%%s AND (o.deleted_at IS NULL OR o.deleted_at='') "
            "AND o.ordered_at>=%%s" % (_status_cond(), _status_cond()))
    _p = [channel, d_from + " 00:00:00"]
    if d_to:
        _sql += " AND o.ordered_at<=%s"
        _p.append(d_to + " 23:59:59")
    _sql += " GROUP BY DATE(o.ordered_at), COALESCE(p.brand,'')"
    try:
        return query(_sql, _p)
    except Exception as _e:
        try_err('dashboard', '品牌按日聚合降级', _e)
        return []


def _build_summary_agg(channel, days60, now, mode="bbcc"):
    """agg 路径(强实时): 前天及以前读 orders_day_agg, 昨天+今天实时直查 orders(补录/新增当日立即反映)

    2026-11-02 冷算收口: 原 4 条并行 + 组装阶段 13 条**串行**(计数 3 / 回款流水 3 / 品牌 5 /
    配置 2) —— 线上实测 22 次往返、单次 ~57ms, 光往返就 1.25s。现在把同源的合成 3 条
    (计数 1 / 流水 1 / 品牌 1)并全部前置到并行池, 组装阶段不再发 SQL。
    """
    today = now.strftime("%Y-%m-%d")
    d1 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _st30 = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    import time as _t
    from db import stats_reset as _sreset, stats_snapshot as _ssnap
    _sreset()
    _lap = {}

    def _wrap(_name, _fn, *_a):
        """每条查询单独计时 —— 定位"2.5s 查询"到底是哪一条、是建连还是执行"""
        def _run():
            _ts = _t.time()
            try:
                return _fn(*_a)
            finally:
                _lap[_name] = round(_t.time() - _ts, 3)
        return _run

    _t0 = _t.time()
    from concurrent.futures import ThreadPoolExecutor
    # 2026-11-07 P1: 判定读模型可用时, 「库存 9 万行」与「日销切片 6 万行」**根本不取** ——
    #   这两条是 summary 3.8s 里唯一的大头(线上 stmt max=2.07s / bundle 仅 0.88s),
    #   取回来只为了算 10 个整数。命中时并行池只剩 bundle 一路。
    _t_sum = _store_tally(channel, mode)
    _store_on = _t_sum is not None
    # 2026-11-06: 8 条查询/6~8 条连接 → 3 条查询/3 条连接。
    #   六个小结果集(agg/recent/brands/flows/counts/cfg)合成 _q_bundle 一条 UNION ALL;
    #   两个大结果集(库存 9 万行 / 日销切片 6 万行)各自独立并行 —— 它们才是传输大头, 不便合并。
    #   合并后: 建连次数从'6 次各自握手'变'3 次并行握手', 且任一手慢不再拖住别的结果集。
    with ThreadPoolExecutor(max_workers=3 if not _store_on else 1) as _ex:
        _fb = _ex.submit(_wrap("bundle", _q_bundle, channel, days60, d1, _st30, today))
        _f3 = None if _store_on else _ex.submit(_wrap("inv", _q_inventory, channel))
        _f4 = None if _store_on else _ex.submit(_wrap("digest", _q_digest, channel, mode))
        # bundle 失败即整体降级到直查路径(外层 try 捕) —— 原 6 条里只有 agg 是硬依赖,
        #   合并后它们同一条 SQL, 要么全有要么全无; 少 5 次建连后失败概率本身也降了。
        agg, recent, _brands_raw, _flows, _counts, _cfg = _split_bundle(_fb.result())
        _inv_rows = _f3.result() if _f3 is not None else None
        _ds_map = _f4.result() if _f4 is not None else None
    _t1 = _t.time()
    _ass = _assemble(list(agg) + list(recent), channel, _st30, today, mode,
                     _inv_rows=_inv_rows, _series=_ds_map,
                     _counts=_counts, _flows=_flows, _brands_raw=_brands_raw, _cfg=_cfg,
                     _t_sum=_t_sum)
    _t2 = _t.time()
    # 冷算计时(2026-11-02 S2-6 收口后保留): perf_debug 是线上冷算观测的唯一手段
    #   (本地往返数不能外推线上), 部署验收/回归都读它。写 quality_logs 限每屏一条, 成本可忽略。
    try:
        from db import execute as _et
        _st = _ssnap()
        _parts = " ".join("%s=%.2f" % (k, v) for k, v in
                          sorted(_lap.items(), key=lambda kv: -kv[1]))
        _et("INSERT INTO quality_logs(log_type, level, message, details, source) "
            "VALUES('perf_debug','info',%s,%s,'dash')",
            ("summary agg 计时: 查询 %.2fs 组装 %.2fs" % (_t1 - _t0, _t2 - _t1),
             "parallel8 conn=%d/%.2fs stmt=%d/%.2fs max=%.2fs | %s | %s"
             % (_st.get("conn_n", 0), _st.get("conn_s", 0.0),
                _st.get("stmt_n", 0), _st.get("stmt_s", 0.0),
                _st.get("stmt_max", 0.0), _parts, channel)))
    except Exception:
        pass
    return _ass


def _store_tally(channel, mode):
    """判定读模型: 命中返回 tally, 未命中返回 None 并起后台补建

    两条路径共用 —— `_build_summary_agg`(agg 可用)与 `_build_summary` 的直查降级分支。
    2026-11-07 教训: 只在 agg 路径接它是不够的。`agg_dirty` 脏标记/重建期间会整体降级直查,
    那条分支原来没有判定快照 → 一次 sweep 之后卡片又回到 8~10s(线上实测 09:46 后 4.6s,
    且 `summary agg 计时` 一条日志都不再出现)。判定快照与 agg 无关, 没有理由跟着一起降级。
    """
    try:
        from core import unit_store as _US
        _t = _US.tally_of(channel, mode)
        if _t is None:
            _US.ensure_async()
        return _t
    except Exception as _e:
        try_err('dashboard', '判定读模型不可用, 退回现算', _e)
        return None


def _build_summary(channel, start_date, end_date, mode="bbcc"):
    """summary 计算体(共享缓存 builder; 原函数体迁移)"""
    now = now_cn()
    today = now.strftime("%Y-%m-%d")

    if start_date and end_date:
        rows = query(
            "SELECT DATE(ordered_at) AS d, order_status, store, "
            "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
            "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)) AS sub, COUNT(*) AS cnt "
            "FROM orders WHERE channel=%%s AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at >= %%s AND ordered_at < %%s "
            "GROUP BY DATE(ordered_at), order_status, store" % (_status_cond(), _status_cond()),
            (channel, start_date + " 00:00:00", (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00:00"))
        return _assemble(rows, channel, start_date, end_date, mode)

    # 2026-10-30 取数窗口: **不写死天数, 下界对齐"上上月 1 号"**(自然完整月口径的环比基准)
    #   环比 = 完整上月 vs 完整上上月; 若窗口用滚动的 now-59, 跨月那天会把上上月最后一天切掉
    #   (实测: 数据始于 8-31, 10-29 时 now-59=8-31 可算, 10-30 时 now-59=9-01 → 上上月归零)。
    #   同时仍保留 60 天趋势所需范围: 取 min(上上月1号, now-59) —— 只提前下界, 不改任何口径。
    _m_start = now.replace(day=1)                                    # 本月1号
    _prev_m_start = (_m_start - timedelta(days=1)).replace(day=1)    # 上月1号
    _prev2_m_start = (_prev_m_start - timedelta(days=1)).replace(day=1)  # 上上月1号
    _win60 = (now - timedelta(days=59)).strftime("%Y-%m-%d")
    days60 = min(_prev2_m_start.strftime("%Y-%m-%d"), _win60)
    # 物化表优先(历史区间读 orders_day_agg 万级, 当天实时直查 orders): agg 不可用降级直查
    try:
        _aggc = one("SELECT COUNT(*) AS c, COALESCE(MAX(`date`),'') AS m FROM orders_day_agg WHERE channel=%s", [channel]) or {}
        _agg_ok = int(_aggc.get("c") or 0) > 0
        # 陈旧检测(数据驱动, 非时间驱动): 基准=orders 最新日期——无新数据时 agg 停留不算陈旧
        # (曾用 now-3天: 数据停更 3 天后误判陈旧 → 每次降级直查 60 天, 看板 0.88s→3-13s 体感退化)
        _aggmx = str(_aggc.get("m") or "")[:10]
        # 2026-11-06 审计 P0-2: 原来无条件查 `MAX(DATE(ordered_at))` —— 全表扫 41 万行/次。
        #   实测: 仅把 MAX(DATE()) 改成 MAX(ordered_at) **不够**, `(deleted_at IS NULL OR
        #   deleted_at='')` 这个 OR 过滤把索引挡住了, EXPLAIN 仍是 TableFullScan。
        #   真正的解法是**短路**: agg 已经覆盖到今天 ⇒ aggmx ≥ today > agg_cut(-3 天),
        #   陈旧判据必然不成立, 这次 orders 扫描对结果没有任何影响。只有 agg 落后于今天
        #   (导入延迟/重建失败)才去查, 那时一次全表扫是可接受的低频代价。
        _today = now.strftime("%Y-%m-%d")
        if not _agg_ok:
            # agg 不可用 → 下面两个陈旧判据都要求 _agg_ok, 这次扫描对结果零影响, 直接跳过
            _omd = ""
        elif _aggmx and _aggmx >= _today:
            _omd = _aggmx  # 已覆盖到今天 → 跳过 orders 全表扫(常态零扫描)
        else:
            _omx = one("SELECT COALESCE(MAX(ordered_at),'') AS m FROM orders "
                       "WHERE (deleted_at IS NULL OR deleted_at='')") or {}
            _omd = str(_omx.get("m") or "")[:10]
        _agg_cut = ""
        if _omd:
            _agg_cut = (datetime.strptime(_omd, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
        if _agg_ok and _aggmx and _agg_cut and _aggmx < _agg_cut:
            _agg_ok = False
        # 重建失败脏标记 → 降级直查(封死"补录历史+重建失败"边界——补录不改 orders MAX,
        # 数据驱动检测不到, 标记兜底; 表不存在视为未置位, 安全)
        try:
            if _agg_ok and one("SELECT 1 FROM agg_dirty WHERE id=1"):
                _agg_ok = False
        except Exception:
            pass
    except Exception:
        _agg_ok = False
    if _agg_ok:
        try:
            return _build_summary_agg(channel, days60, now, mode)  # agg 路径(强实时); 异常整体降级下方直查
        except Exception as _e:
                try_err('dashboard', '静默降级', _e)  # agg 路径失败 → 降级直查 60 天(不阻塞)
    rows = query(
        "SELECT DATE(ordered_at) AS d, order_status, store, "
        "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
        "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)) AS sub, COUNT(*) AS cnt "
        "FROM orders WHERE channel=%%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %%s "
        "GROUP BY DATE(ordered_at), order_status, store" % (_status_cond(), _status_cond()),
        (channel, days60 + " 00:00:00"))
    return _assemble(rows, channel, (now - timedelta(days=29)).strftime("%Y-%m-%d"), today, mode,
                     _t_sum=_store_tally(channel, mode))


def _assemble(rows, channel, start_date, end_date, mode="bbcc", _inv_rows=None, _ds_map=None,
              _counts=None, _flows=None, _brands_raw=None, _cfg=None, _series=None, _t_sum=None):
    """把聚合行组装成看板结构

    2026-11-02 冷算收口: `_counts` / `_flows` / `_brands_raw` / `_cfg` 由调用方预取并注入
    (agg 路径已在并行池里取好), 组装阶段不再发 SQL。为 None 时自己查(自定义区间直查路径)。

    2026-11-06 S3: `_series` 是 `core.metrics.Series`(按维度切片); `_ds_map` 只为兼容旧调用方
    保留 —— 它只有 SKU 级一个数, 会退化成"全挂 `__C__` 维"(见 Series.from_sku_map)。
    """
    if _series is None and _ds_map:
        try:
            from core import metrics as _M
            _series = _M.Series.from_sku_map(channel, _ds_map)
        except Exception:
            _series = None
    # 行形态归一(兼容 DictCursor list[dict] 与非 DictCursor tuple rows——列序=SELECT 顺序)
    _cols = ("d", "order_status", "store", "g", "sub", "cnt")
    _norm = []
    for _r in (rows or []):
        _norm.append(_r if isinstance(_r, dict) else dict(zip(_cols, list(_r))))
    rows = _norm
    gmv = pending = refund = refund_amt = subsidy = total_orders = paid_orders = 0
    trend = {}
    store_gmv = {}
    store_refund = {}
    store_subsidy = {}
    funnel = {}
    day_rows = {}
    for r in rows:
        d = str(r.get("d") or "")[:10]  # TiDB DATE() → datetime.date, 统一转 str
        st = r.get("order_status") or "未知"
        store = r.get("store") or ""
        g = float(r.get("g") or 0)
        sub = float(r.get("sub") or 0)
        cnt = int(r.get("cnt") or 0)
        day_rows[(d, st, store)] = (g, sub, cnt)
        total_orders += cnt
        if st in _PAID:
            gmv += g
            subsidy += sub
            paid_orders += cnt
            store_gmv[store] = store_gmv.get(store, 0) + g
            store_subsidy[store] = store_subsidy.get(store, 0) + sub
            if st == "待发货":
                pending += cnt
            elif st == "申请退款":
                refund += cnt
                refund_amt += g
                store_refund[store] = store_refund.get(store, 0) + g
        t = trend.setdefault(d, {"GMV": 0, "订单数": 0})
        if st in _PAID:
            t["订单数"] += cnt
            t["GMV"] += g
        funnel[st] = funnel.get(st, 0) + cnt

    # 每日退款/补贴预聚合(一次遍历 day_rows——原每点 O(天×day_rows) 遍历提速)
    _rf_by_day = {}
    _sb_by_day = {}
    _gmv_by_day = {}
    _cnt_by_day = {}
    for (_d2, _st, _s2), (_g, _sb, _cn) in day_rows.items():
        if _st == "申请退款":
            _rf_by_day[_d2] = _rf_by_day.get(_d2, 0) + _g
        if _st in _PAID:
            _sb_by_day[_d2] = _sb_by_day.get(_d2, 0) + _sb
            _gmv_by_day[_d2] = _gmv_by_day.get(_d2, 0) + _g
            _cnt_by_day[_d2] = _cnt_by_day.get(_d2, 0) + _cn

    trend_data = []
    for _k, _v in sorted(trend.items()):
        _d = str(_k)[:10]
        _rf = _rf_by_day.get(_d, 0)
        _sb_all = _sb_by_day.get(_d, 0)
        trend_data.append({"日期": _k, "GMV": _v["GMV"], "订单数": _v["订单数"],
                           "net_gmv": round(float(_v["GMV"]) - _rf, 2),
                           "payout": round(float(_v["GMV"]) - _rf - _sb_all, 2)})
    stores = [{"name": k, "gmv": round(v, 2),
               "refund_amount": round(store_refund.get(k, 0), 2),
               "subsidy_amount": round(store_subsidy.get(k, 0), 2),
               "net_gmv": round(v - store_refund.get(k, 0), 2),
               "payout": round(v - store_refund.get(k, 0) - store_subsidy.get(k, 0), 2)}
              for k, v in sorted(store_gmv.items(), key=lambda x: -x[1])]

    ftotal = total_orders
    funnel_res = _build_funnel_stages(funnel, ftotal)

    now = now_cn()
    today_s = now.strftime("%Y-%m-%d")
    d1 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    # 2026-10-16 自然口径(v1.0 GMV 矩阵): 上周=上周一~上周日完整自然周 / 上月=上月1号~月末完整自然月(环比=上上周/上上月)
    _wk = now.isoweekday()  # 1=周一..7=周日
    _last_mon = (now - timedelta(days=_wk + 6)).strftime("%Y-%m-%d")  # 上周一
    _last_sun = (now - timedelta(days=_wk)).strftime("%Y-%m-%d")      # 上周日
    _prev_mon = (now - timedelta(days=_wk + 13)).strftime("%Y-%m-%d") # 上上周一
    _prev_sun = (now - timedelta(days=_wk + 7)).strftime("%Y-%m-%d")  # 上上周日
    _this_m1 = now.replace(day=1).strftime("%Y-%m-%d")  # 本月1号
    _last_m1 = (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")        # 上月1号
    _last_m_end = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")                     # 上月最后一天
    # 2026-11-06 P3-4: 删除 `_prev_m1 = (...) if False else None` —— 死表达式, 下一行就被
    #   重新赋值。留着只会让读代码的人以为这里有个开关。
    _tmp = now.replace(day=1) - timedelta(days=1)  # 上月最后一天
    _prev_m1 = _tmp.replace(day=1) - timedelta(days=1)
    _prev_m1 = _prev_m1.replace(day=1).strftime("%Y-%m-%d")           # 上上月1号
    _prev_m_end = (_tmp.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")  # 上上月最后一天
    d7, d8 = _last_mon, _last_sun
    d14 = _prev_mon
    d30 = _last_m1
    d31 = _last_m_end
    d60 = _prev_m1

    def _agg(d0, d1):
        # 区间按天索引求和(等价: 原全遍历窗口内 PAID 累加——O(天) 替代 O(day_rows))
        g = o = 0
        _d0k = str(d0)
        for _dk, _gv in _gmv_by_day.items():
            if _d0k <= _dk <= str(d1):
                g += _gv
                o += _cnt_by_day.get(_dk, 0)
        return round(g, 2), o

    def _refund(d0, d1):
        r = 0.0
        _d0k = str(d0)
        for _dk, _rv in _rf_by_day.items():
            if _d0k <= _dk <= str(d1):
                r += _rv
        return r

    def _sub(d0, d1):
        s = 0.0
        _d0k = str(d0)
        for _dk, _sv in _sb_by_day.items():
            if _d0k <= _dk <= str(d1):
                s += _sv
        return s

    # 2026-11-02 冷算: 原 _payout_flows()(每次一次 SUM+COUNT 扫同窗)已并入 _q_flows_month 的
    #   一次 GROUP BY DATE,shop, 此处不再单发查询。
    tg, to = _agg(today_s, today_s)
    pg, po = _agg(d1, d1)
    wg, wo = _agg(d7, d8)  # d7=上周一 d8=上周日——完整自然周(2026-10-16 曾 _agg(d7,today_s) 终点错含今天)
    pwg, pwo = _agg(d14, _prev_sun)
    mg, mo = _agg(d30, d31)
    pmg, pmo = _agg(_prev_m1, _prev_m_end)

    periods = {
        "today": {"gmv": tg, "orders": to, "days": 1, "prev_gmv": pg, "prev_orders": po,
                  "prev_net_gmv": round(pg - _refund(d1, d1), 2),
                  "prev_payout": round(pg - _refund(d1, d1) - _sub(d1, d1), 2),
                  "net_gmv": round(tg - _refund(today_s, today_s), 2), "subsidy_amount": round(_sub(today_s, today_s), 2),
                  "payout": round(tg - _refund(today_s, today_s) - _sub(today_s, today_s), 2)},
        "week": {"gmv": wg, "orders": wo, "days": (_last_sun and 7), "prev_gmv": pwg, "prev_orders": pwo,
                 "prev_net_gmv": round(pwg - _refund(d14, _prev_sun), 2),
                 "prev_payout": round(pwg - _refund(d14, _prev_sun) - _sub(d14, _prev_sun), 2),
                 "net_gmv": round(wg - _refund(d7, d8), 2), "subsidy_amount": round(_sub(d7, d8), 2),
                 "payout": round(wg - _refund(d7, d8) - _sub(d7, d8), 2)},
        "month": {"gmv": mg, "orders": mo, "days": _last_m1 and (_tmp_ld := ((now.replace(day=1) - timedelta(days=1)).day)) or 30, "prev_gmv": pmg, "prev_orders": pmo,
                  "prev_net_gmv": round(pmg - _refund(_prev_m1, _prev_m_end), 2),
                  "prev_payout": round(pmg - _refund(_prev_m1, _prev_m_end) - _sub(_prev_m1, _prev_m_end), 2),
                  "net_gmv": round(mg - _refund(d30, d31), 2), "subsidy_amount": round(_sub(d30, d31), 2),
                  "payout": round(mg - _refund(d30, d31) - _sub(d30, d31), 2)},
    }

    # 一次判定, 两处消费(2026-11-06 第二刀: 组装 3.14s → 见 docs 15.152)
    #
    # 在这之前: 健康分(`_health_index`)建一遍 units+judge, 低库存下面再建一遍 —— 同一批
    #   8.5 万行、同一份日销、同一个 cfg, 只有"取哪些桶"不同, 却付了两遍全量代价。
    # 现在: 一次 units(with_bc=True, 同时含 bc/wh/own 三种桶), 一次 tally(只计数不造
    #   verdict dict), 两处各自 roll 出自己那几个数。桶集合与原来**逐个相等**:
    #     · 健康四个 tab: own / wt=platform / wt=platform_b / bc / (own+wh)
    #     · 低库存:       bbcc = bc + 非平台仓的 wh(platform 行已并入 bc, 不能再数一次)
    #                     traditional = 逐仓 wh(B 仓已被 drop_platform_b 剔除)
    from core import metrics as _M
    _mode_sum = _M.mode_of(channel, mode)
    if _t_sum is None:
        try:
            _cfg_sum = _cfg if _cfg is not None else _q_cfg(channel)
            _p_sum = _M.periods(_cfg_sum)
            _rows_sum = (_inv_rows if _inv_rows is not None else
                         query("SELECT sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, "
                               "available_qty, in_transit_qty, safety_qty FROM inventory WHERE channel=%s",
                               [channel]))
            _ser_sum = _series
            if _ser_sum is None:
                _ser_sum = _M.load(channel, dims=_M.dims_needed(_rows_sum, channel, _mode_sum),
                                   fields=("ds28",))
            _us_sum = _M.units(_rows_sum, merge_bc=False, with_bc=True,
                               grain=_M.grain_of(channel, _mode_sum, _cfg_sum),
                               drop_platform_b=(_mode_sum != "bbcc"))
            _t_sum = _M.tally(_us_sum, _ser_sum, channel, _mode_sum, metric="ds28",
                              include_transit=_M.include_transit_default(), p=_p_sum)
        except Exception as _e:
            try_err('dashboard', '看板判定降级(健康/低库存各自重算)', _e)
    # 命中物化快照时, health 不能再收到 _inv_rows/_series —— 否则它照样会把 9 万行拉一遍
    health = _health_index(channel, mode,
                           None if _t_sum is not None else _inv_rows, _ds_map,
                           _cfg=_cfg,
                           _series=None if _t_sum is not None else _series,
                           _tally=_t_sum)
    # 低库存计数 —— 与健康卡/告警规则同一套判定(core.metrics), 不再自己拼安全线
    #
    # 2026-11-06 S3: 原实现有四处各写一遍, 且都拿 **SKU 级 `__C__` 日销** 去比每个仓的可用量:
    #   7 个仓各比"整盘需求×12 天" → 越小的仓越"缺货"; own 仓还要乘采购周期(更严)。
    #   这里的数字和健康卡、告警列表本来就是三份不同的答案 —— 用户看到的是三张脸。
    #   现在只有一个来源: 桶由 metrics.units 归并, ds 按桶维度取, 安全线由 metrics 算。
    #   `out` 单列(可用<=0), 不混进低库存 —— 断货和"快没了"是两件事, 卡上要分开。
    low_stock = {"c": 0, "out": 0, "total": 0}
    if _t_sum is not None:
        try:
            # 与 aux 卡同口径: 只数销售仓(自有仓的缺口归「自有仓健康」, 不在这里重复计)
            if _mode_sum == "bbcc":
                _c_ls = _M.roll(_t_sum, kinds=(_M.DIM_BC, _M.DIM_WH),
                                wt_not_in=("platform", "platform_b"))
            else:
                _c_ls = _M.roll(_t_sum, kinds=(_M.DIM_WH,))
            low_stock = {"c": int(_c_ls.get("low") or 0), "out": int(_c_ls.get("out") or 0),
                         "total": int(_c_ls.get("total") or 0)}
        except Exception as _e:
            try_err('dashboard', '低库存计数降级', _e)
    # 三个概览计数: 原 3 次往返 → 一条(标量子查询, 同快照同口径); 调用方已预取则直接用
    _cnts = _counts if _counts is not None else _q_counts(channel)
    alert_count = {"c": _cnts.get("alerts", 0)}
    product_count = {"c": _cnts.get("products", 0)}
    supplier_count = {"c": _cnts.get("suppliers", 0)}

    summary = {
        "gmv": round(gmv, 2), "net_gmv": round(gmv - refund_amt, 2),
        "refund_amount": round(refund_amt, 2), "subsidy_amount": round(subsidy, 2),
        "payout": round(gmv - refund_amt - subsidy, 2),
        "total_orders": total_orders, "pending_count": pending, "refund_count": refund,
        # 两个数同一批单元(销售仓 SKU 粒度), 互斥可加 —— 2026-11-06 前 low 含自有仓、
        #   out 数库存行, 单看都对、放一起没有任何关系
        "low_stock_count": int(low_stock.get("c") or 0),
        "out_of_stock_count": int(low_stock.get("out") or 0),
        "active_alerts": int(alert_count.get("c") or 0),
        "total_products": int(product_count.get("c") or 0), "total_suppliers": int(supplier_count.get("c") or 0),
    }
    # 周期趋势(前端 gmv 小卡图表按今日/本周/本月联动)
    def _trend_range(d0, d1):
        # 2026-10-16 回款口径: 月度且有真实流水 → 按日真值(无到账日=0, 不打洞);
        # 无真实流水 → payout=null(前端待接入, 不显示推算)——有就有没有就没有
        _is_month = d0 == d30 and d1 == d31
        out = []
        for x in trend_data:
            _d = str(x.get("日期") or "")[:10]
            if d0 <= _d <= d1:
                _pd = x.get("payout")
                if _is_month and _flow_day is not None:
                    _pd = _flow_day.get(_d, 0.0)
                elif _is_month:
                    _pd = None
                out.append({"日期": x.get("日期"), "GMV": x.get("GMV"), "订单数": x.get("订单数"),
                            "net_gmv": x.get("net_gmv"), "payout": _pd})
        return out

    # 周期店铺(前端店铺 GMV 卡周期联动)
    def _stores_range(d0, d1):
        sg = {}; srf = {}; ssb = {}
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1:
                if st in _PAID:
                    sg[s2] = sg.get(s2, 0) + gv
                    ssb[s2] = ssb.get(s2, 0) + sb
                if st == "申请退款":
                    srf[s2] = srf.get(s2, 0) + gv
        # 2026-10-16 回款口径: 月度且有真实流水 → 按 shop 真值; 无 → null(不显示推算)——
        # 与 GMV 卡回款维度"有就有没有就没有"同源一致
        _is_month_st = d0 == d30 and d1 == d31
        out = []
        for k, v in sorted(sg.items(), key=lambda x: -x[1]):
            _pd = round(v - srf.get(k, 0) - ssb.get(k, 0), 2)
            if _is_month_st and _flow_shop is not None:
                _pd = _flow_shop.get(k, 0.0)
            elif _is_month_st:
                _pd = None
            out.append({"name": k, "gmv": round(v, 2),
                        "net_gmv": round(v - srf.get(k, 0), 2),
                        "payout": _pd})
        return out

    # 2026-10-16 GMV 回款: 优先真实资金流水(gmv_flows——按到账时间月度归属); 无数据降级 orders 推算
    _po_m = None
    _flow_day = None   # 回款趋势: 真实流水按日净额 {日期: 金额}(有流水才查)
    _flow_shop = None  # 店铺回款: 真实流水按 shop 聚合(月度有流水才查)
    # 2026-11-02 冷算: 原三次扫同一时间窗(总额/按日/按店) → 一条 GROUP BY DATE,shop 内存派生。
    #   口径不变: 同一个 _FLOW_NET 净额表达式 + 同一个 arrived_time 区间; 无流水时返回 (None,None,None)
    #   —— 与原 "COUNT(*)>0 才算有流水" 等价。
    try:
        if _flows is not None:
            _po_m, _flow_day, _flow_shop = _flows
        else:
            _po_m, _flow_day, _flow_shop = _q_flows_month(channel, d30, d31)
    except Exception as _e:
        try_err('dashboard', '月度回款读取降级', _e)
    if _po_m is not None:
        periods["month"]["payout"] = _po_m
        periods["month"]["payout_source"] = "flow"   # 真实资金流水
    else:
        periods["month"]["payout"] = None            # 无真实流水——前端弱化提示(不显示推算——v1.0 算不准宁可不给)
        periods["month"]["payout_source"] = "pending"

    periods["today_trend"] = _trend_range(today_s, today_s)
    periods["week_trend"] = _trend_range(d7, d8)  # 完整上周趋势(曾终点 today)
    periods["month_trend"] = _trend_range(d30, d31)  # 完整上月趋势(曾终点 today)

    # 品牌维度(店铺 GMV 卡切品牌: 近 60 天 paid 订单按品牌聚合, join products)
    # v4-paidset: g/sb 必须用 _status_cond()(PAID 4 状态含申请退款) —— 历史线上曾误用非支付状态集(待确认+已退货)导致品牌 GMV 低估/净额为负
    brands_map = {}
    # 2026-11-02 冷算: 品牌原来 5 次往返(60天 + today/week/month/custom 各一次, 同一段 SQL 只差日期界);
    #   改为一次按(日,品牌)取回, 各区间的聚合在内存做。口径逐字不变。
    _brands_rows = _brands_raw if _brands_raw is not None else _q_brands(channel, d60)

    def _brands_agg(d0, d1=None):
        """从(日,品牌)行按区间累加; d1=None 表示无上界(与原 60 天查询同)"""
        _out = {}
        for _r in _brands_rows:
            _dd = str(_r.get("d") or "")[:10]
            if not _dd or _dd < d0:
                continue
            if d1 is not None and _dd > d1:
                continue
            _b = str(_r.get("brand") or "")
            if not _b or _b == "未分类":
                continue
            _v = _out.setdefault(_b, {"g": 0.0, "rf": 0.0, "sb": 0.0})
            _v["g"] += float(_r.get("g") or 0)
            _v["rf"] += float(_r.get("rf") or 0)
            _v["sb"] += float(_r.get("sb") or 0)
        return {k: {"g": round(v["g"], 2), "rf": round(v["rf"], 2), "sb": round(v["sb"], 2)}
                for k, v in _out.items()}

    try:
        brands_map = _brands_agg(d60)
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)
    # 次级键 = 品牌名: GMV 并列(常见于退款抵消后为 0)时原实现依赖 SQL 返回顺序,
    #   不同请求/不同实现间会抖动; 加名称定序后结果可复现(数值不受影响)
    brands = [{"name": k, "gmv": v["g"], "net_gmv": round(v["g"] - v["rf"], 2),
              "payout": round(v["g"] - v["rf"] - v["sb"], 2)}
              for k, v in sorted(brands_map.items(), key=lambda x: (-x[1]["g"], x[0]))]
    def _brands_range(d0, d1=""):
        """按起始日聚合品牌 GMV(周期联动)——2026-10-16 双参(d1 终点——完整自然周/月; 空=至今)"""
        try:
            _bm = _brands_agg(d0, d1 or d0)
            # 2026-10-16 回款口径: 品牌维度无真实流水归属(gmv_flows 仅 shop 维度)→ month 回款=null
            # (不显示推算——与 GMV 卡"有就有没有就没有"同源); 非 month 周期保留 orders 推算
            _is_month_b = d0 == d30 and (not d1 or d1 == d31)
            out = []
            for k, v in sorted(_bm.items(), key=lambda x: (-x[1]["g"], x[0])):
                out.append({"name": k, "gmv": v["g"], "net_gmv": round(v["g"] - v["rf"], 2),
                            "payout": None if _is_month_b else round(v["g"] - v["rf"] - v["sb"], 2)})
            return out
        except Exception:
            return []
    period_brands = {"today": _brands_range(today_s, today_s),
                     "week": _brands_range(d7, d8),
                     "month": _brands_range(d30, d31)}
    period_stores = {"today": _stores_range(today_s, today_s),
                     "week": _stores_range(d7, d8),
                     "month": _stores_range(d30, d31)}
    if start_date and end_date:
        # 自定义区间: 补齐 periods.custom(GMV 卡周期联动), 无环比(前端 prev=0 跳过显示)
        _cg, _co = _agg(start_date, end_date)
        periods["custom"] = {"gmv": _cg, "orders": _co,
                             "days": (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days + 1,
                             "prev_gmv": 0, "prev_orders": 0,
                             "net_gmv": round(_cg - _refund(start_date, end_date), 2),
                             "subsidy_amount": round(_sub(start_date, end_date), 2),
                             "payout": round(_cg - _refund(start_date, end_date) - _sub(start_date, end_date), 2)}
        period_stores["custom"] = _stores_range(start_date, end_date)
        period_brands["custom"] = _brands_range(start_date)
    def _funnel_range(d0, d1):
        _ft = {}
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1:
                _ft[st] = _ft.get(st, 0) + cn
        # 与全局 funnel 同一套摊法(区间内自身求和为总数)
        return _build_funnel_stages(_ft, sum(_ft.values()))
    period_funnel = {"today": _funnel_range(today_s, today_s),
                     "week": _funnel_range(d7, d8),
                     "month": _funnel_range(d30, d31)}
    if start_date and end_date:
        period_funnel["custom"] = _funnel_range(start_date, end_date)

    # 2026-10-30 口径保护: 趋势图固定最近 60 点(原口径); 取数窗口提前到"上上月1号"只为环比基准,
    #   不应顺带把趋势点数从 60 拉长到 90
    trend_data = trend_data[-60:]
    return {"summary": summary, "periods": periods, "trend": trend_data,
            "funnel": funnel_res, "period_funnel": period_funnel, "health_index": health, "stores": stores,
            "brands": brands, "period_stores": period_stores, "period_brands": period_brands}




_aux_cache = {}
_AUX_TTL = 60
_risk_cache = {}
_RISK_TTL = 30


@router.get("/dashboard/aux")
@traced
def dashboard_aux(channel: str = "jd", mode: str = ""):
    """看板辅助聚合(60s 共享表缓存——TiDB 表跨实例一致): alerts(分组配额, 低库存组按补货模式过滤维度) + alertCounts
    + stockOverview + bcOutOfStock + stockRisk

    模式跟随: bbcc → 低库存显示 BC 合计(bc, 已含 platform+platform_b)+own(集货仓=B仓调拨源);
    traditional → own + platform 逐仓(不含 B 仓: B 属 BBCC 调拨体系);
    traditional → 低库存显示 C 仓(platform)+own(自有/三方仓)
    """
    # 2026-11-06 P0-2: 同 summary —— 缺省按渠道默认, 不再硬编码 bbcc(见 dashboard_summary)
    mode = mode or ch_default_mode(channel)
    _key = "dash_aux|%s|%s" % (channel, mode)
    # 1MB 上限: aux 是"卡片聚合", 正常 30~80KB。超 1MB 说明又有明细列表被误塞进聚合响应,
    #   宁可这次不落缓存(也不至于把 TiDB 单条 6MB 事务打爆 / 让每个请求算两遍)。
    # 2026-11-07 S0 防雪崩: 重计算名额(全局 3) —— 拿不到就返回旧快照, 不再跟别人抢连接。
    from db import heavy as _heavy, HeavyBusy as _HeavyBusy
    from routes.analysis_cache import cache_stale as _cache_stale
    try:
        with _heavy("aux", timeout=15):
            _data = _cache_get(_key, _AUX_TTL, lambda: _build_aux(channel, mode),
                               max_bytes=1024 * 1024)
    except _HeavyBusy as _eh:
        try_err('dashboard', 'aux 并发繁忙, 返回旧快照', _eh)
        _data = _cache_stale(_key) or {}
    # 2026-11-03 D5: "规则重算中"标记 —— 保存规则后后端是异步评估(可能 10~30s), 前端若不等
    #   就会"切回看板看到旧告警 → 以为没生效 → 再保存一次"。必须在缓存之外读:
    #   进缓存会出现"一直 true"或"评估完了还显示重算中"。
    # 2026-11-07 §15.158: 计数改带新鲜度(与状态接口/任务合并共用 eval_live_cond)。
    #   实例被平台冻结时 daemon 线程的 done/error 回写不会发生, 裸 `status='running'`
    #   会永远成立 → 线上出现过看板「规则重算中」挂 3 小时。
    try:
        from core.maintenance import eval_live_cond as _live
        _pend = one("SELECT COUNT(*) AS c FROM eval_tasks WHERE " + _live()) or {}
        _data = dict(_data or {})
        _data["evaluating"] = bool(int(_pend.get("c") or 0))
    except Exception as _e:
        try_err('dashboard', '重算标记读取降级(不影响主数据)', _e)
    return ok(_data)


def _build_aux(channel, mode):
    """aux 计算体(共享缓存 builder)

    2026-11-06 并行化(第二项排查结论): 这里原来 24 条 SQL **全串行**, 其中四段彼此
    **无任何数据依赖**(告警组 / 库存概览 / 低库存判定 / 自有仓健康), 只是代码顺序排下来。
    线上单次往返 ~57ms → 光往返就 1.4s; 再叠上自有仓健康那 4 条大查询(5.6 万行)
    排在最后串行等 → 冷算 6~9s。改成 4 路并行(与 _build_summary_agg 同一套写法,
    线程在 EdgeOne 运行时已验证可用)：峰值 4 条连接 < DB_POOL_LIMIT=10, 不挤占其它请求。
    降级口径不变 —— 每块失败只退化成自己的空结构, 不拖垮整包。
    """
    from routes.alerts import _FIELDS as _AF
    # 低库存维度过滤(跟随补货模式): bbcc → BC+own; traditional → C+own(白名单拼接)
    # 2026-10-30 修复"低库存卡无数据": 原 Python 判定写成 `wt in "(...)"` —— **字符串子串匹配**,
    #   新增的 warehouse_type='bc'(BBCC 合计行)不在字面串里 → 140 条低库存告警被静默过滤(列表空);
    #   同时空 warehouse_type('') 因是任何字符串的子串反而"意外通过"。
    #   改为**元组**白名单 + 显式包含 bc 与 ''。
    # 实测低库存告警 100% 带合法仓型(bc/own/platform), 空仓型 0 条 → 白名单不含 ''(空仓型属数据异常,
    #   应暴露而非静默混入); oversell/purchase_need 等 SKU 级告警走 other 分支, 不受此白名单影响
    # 2026-10-30 白名单严格对应各模式的评估产物与业务口径:
    #   bbcc → own 逐仓 + BC 合计(bc)      (platform/platform_b 已合并成 bc)
    #   traditional → own + platform 逐仓  —— **不含 platform_b**:
    #     B 仓是 BBCC 体系(供应商→B→C 调拨), 传统多仓模式不看它(与 purchase_calc:127 同口径)
    _wh_types = (("own", "bc")
                 if mode == "bbcc" else ("own", "platform"))
    _wh_in = "(" + ",".join("'%s'" % _t for _t in _wh_types) + ")"
    _EMPTY_OWN = {"items": [], "total": 0, "transferGap": 0, "overstock": 0,
                  "expiryRisk": 0, "critical": 0, "warning": 0, "limits": {}}

    def _block_alerts():
        """告警列表 + 计数(两条列表查询 + 一次分组计数 + 仓库名回填)"""
        # alerts 分组配额(2026-10-11 合并: 原 3 次串行查询 → 一次全量 + Python 分组——降查询数)
        # 2026-11-02 拆回两条: low_stock 必须单独查 —— 实测 bbcc 模式下头 300 条 id DESC 全是
        #   platform(逐仓,C 仓) 行, bc(B+C 合计) 行 id 比它小 6 万, 被整体挤出 300 条窗口;
        #   白名单 (own, bc) 再一过滤 → 卡面 0 条, 而 alertCounts 走独立 COUNT 显示 160 条
        #   ("标题有 N、卡面空白"的根因)。分开查 + 白名单后, bbcc 稳定拿到 bc 的 160 条。
        alerts = []
        _sel = "SELECT %s FROM alerts WHERE channel=%%s AND status='active' " % _AF
        _ls_rows = query(_sel + "AND alert_type='low_stock' AND warehouse_type IN " + _wh_in +
                         " ORDER BY id DESC LIMIT 200", [channel])
        _ar = query(_sel + "AND alert_type <> 'low_stock' ORDER BY id DESC LIMIT 300", [channel])
        alerts.extend(_ls_rows)
        _rp_c = _ot_c = 0
        # 2026-11-01 自有仓三条不进 aux 的 alerts 列表(其他组) ——
        #   它们由 ownHealth 专属块承载(看板「自有仓健康」卡), 再混进"其他告警"会双重计数且语义相悖
        _OWN_AT = ("own_transfer_gap", "own_overstock", "own_expiry_risk")
        for r in _ar:
            at = r.get("alert_type") or ""
            if at in _OWN_AT:
                continue
            if at == "replenish":
                if _rp_c < 100:
                    alerts.append(r)
                    _rp_c += 1
            elif _ot_c < 100:
                alerts.append(r)
                _ot_c += 1
        from routes.alerts import _attach_warehouse as _att
        _att(alerts, channel)
        counts = query("SELECT alert_type, severity, "
                       "IFNULL(NULLIF(warehouse_type,''),'') AS wt, COUNT(*) AS c "
                       "FROM alerts WHERE channel=%s AND status='active' "
                       "AND (alert_type != 'low_stock' OR warehouse_type IN " + _wh_in + ") "
                       "GROUP BY alert_type, severity, wt", [channel])
        by_type, by_sev, by_wh, total = {}, {}, {}, 0
        by_wh_ls, by_wh_slow, by_wh_rp = {}, {}, {}
        for r in counts:
            at = r.get("alert_type") or "other"
            sev = r.get("severity") or "info"
            wt = r.get("wt") or ""
            c = int(r.get("c") or 0)
            total += c
            by_type[at] = by_type.get(at, 0) + c
            by_sev[sev] = by_sev.get(sev, 0) + c
            by_wh[wt] = by_wh.get(wt, 0) + c
            tgt = by_wh_rp if at == "replenish" else (by_wh_slow if at == "slow_moving" else by_wh_ls)
            tgt[wt] = tgt.get(wt, 0) + c

        def _wmap(m):
            b = m.get("platform_b", 0)
            c = m.get("platform", 0)
            o = m.get("own", 0)
            return {"b": b, "c": c, "own": o, "bc": b + c, "unknown": m.get("", 0)}

        _rp = by_type.get("replenish", 0)
        full = {"total": total, "by_type": by_type, "by_severity": by_sev,
                "replenish": _rp, "non_replenish": total - _rp,
                "by_warehouse": _wmap(by_wh), "ls_warehouse": _wmap(by_wh_ls),
                "slow_warehouse": _wmap(by_wh_slow), "rp_warehouse": _wmap(by_wh_rp)}
        return alerts, full

    def _block_stock_low():
        """缺货 / 低库存 / 缺货清单 / BC 合计缺货 —— **同一批判定单元**上的四种呈现

        2026-11-06 合并(原来 `_block_stock` 与 `_block_low` 是两个线程、两份查询):
          · 各查一遍 inventory(170k 行)、各加载一次日销
          · 断货数是 `COUNT(*) FROM inventory WHERE available_qty=0` —— **库存行**粒度
            (含自有仓、含每个 69码、含空仓); 低库存数却是**判定单元**粒度(SKU × 渠道模式)
          · 卡上再把两者相加 `total = out + low` —— 两个不同粒度相加, 得出来的数没有含义
        现在一次取数、一次归并、一次判定, out/low 是同一批单元的互斥两档, 相加才有意义。
        粒度跟随渠道补货模式: bbcc → BC 盘 + 自有仓; traditional → 逐仓 + 自有仓。
        """
        from core import metrics as _M
        # ── 2026-11-07 P0: 优先读判定物化(unit_summary) ────────────────────────
        # 这段原来每次冷算都 `SELECT 7 列 × 9 万行` + 日销切片 + units + judge_units,
        # 只为得到 6 个整数; 而**同批判定** summary 早已改读物化(组装 3.14s→0.03s)。
        # 线上 aux 冷算因此仍是 4.1~5.5s(slow_request 自报), 是首屏 11s 的一半。
        # 命中条件与 summary 完全一致: fp 相等 + gen 非 0 + 未 stale; 任一不满足就落到原路径。
        from core import unit_store as _US
        _ov = _US.stock_overview(channel, mode)
        if _ov is not None:
            so_items = query("SELECT " + _INV_COLS + " FROM inventory "
                             "WHERE channel=%s AND available_qty=0 ORDER BY id DESC LIMIT 100", [channel])
            return {"out": _ov["out"], "low": _ov["low"], "total": _ov["total"],
                    "items": so_items, "bc_out": _ov["bc_out"],
                    "ok": _ov["ok"], "idle": _ov["idle"], "code_out": _ov["code_out"]}
        _rows = query("SELECT sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, "
                      "available_qty, in_transit_qty, safety_qty, product_name FROM inventory "
                      "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!=''", [channel])
        _cfg_sl = _q_cfg(channel)
        _pp = _M.periods(_cfg_sl)
        _ser = _M.load(channel, dims=_M.dims_needed(_rows, channel, mode), fields=("ds28",))
        _us = _M.units(_rows, merge_bc=(mode == "bbcc"),
                       grain=_M.grain_of(channel, mode, _cfg_sl),
                       drop_platform_b=(mode != "bbcc"))
        _pr = _M.judge_units(_us, _ser, channel, mode, metric="ds28",
                             include_transit=_M.include_transit_default(), p=_pp)
        # 只数**销售仓**(bbcc=BC 盘 / traditional=逐仓)。自有仓(集货仓)的"可用=0"是常态
        #   —— 货本来就在平台仓; 把它算成"缺货"就是纯噪音(线上实测 jd 201 个单元全来自集货仓,
        #   而 BC 盘整盘断货 0 个)。自有仓的库存问题由「自有仓健康」卡承载(2026-11-01 用户定案),
        #   这里混进来等于同一件事计两次。
        #   限定后 jd: out=0(BC 盘没有整盘断货的 SKU) / low=509 —— 与告警表 507 条 low_stock 同源。
        _c = _M.counts(_pr, (_M.DIM_BC, _M.DIM_WH))
        # 码级复核(2026-11-06): grain=sku 合并多码后, "部分码断货"被 SKU 合计掩盖
        #   (B1=0/B2=100 → 合计 100 判 ok, 卖 B1 的链接实际发不出)。合并不静默 ——
        #   这里单列计数, 判定口径不动(否则又回到同渠道两种粒度)。
        _code_out = 0
        for (u, v) in _pr:
            if u.get("kind") not in (_M.DIM_BC, _M.DIM_WH):
                continue
            if _M.code_risk(u)["code_out"] and v["state"] in (_M.ST_OK, _M.ST_LOW):
                _code_out += 1
        so_items = query("SELECT " + _INV_COLS + " FROM inventory "
                         "WHERE channel=%s AND available_qty=0 ORDER BY id DESC LIMIT 100", [channel])
        # BC 合计缺货直接从判定结果里取 —— 不再单独扫一遍库存表算 `SUM(available_qty)<=0`
        #   (那份 SQL 把不同 69码 的可用量相加了, 与"SKU 级缺货"本来就不是一回事)
        bc_out = [{"sku": u["sku"], "product_name": u.get("name") or u["sku"], "warehouse_type": "bc"}
                  for (u, v) in _pr if u["kind"] == _M.DIM_BC and v["state"] == _M.ST_OUT][:100]
        return {"out": int(_c.get("out") or 0), "low": int(_c.get("low") or 0),
                "total": int(_c.get("total") or 0), "items": so_items, "bc_out": bc_out,
                "ok": int(_c.get("ok") or 0), "idle": int(_c.get("idle") or 0),
                "code_out": _code_out}

    def _block_own():
        """自有仓健康(2026-11-01 新增): 自有仓不产生销售, 低库存/断货口径结构性不适用,
        改用供给侧口径(调拨覆盖/积压/效期)。独立成块, 不进 alerts 表也不进"其他告警",
        避免与低库存卡语义互串(用户明确: 补货卡会形成噪音, 需专属卡)。
        失败降级为空结构 —— 不给 aux 整体添故障点。

        2026-11-06 结构改造: 原来调用时传 full=1 —— 把**全部**条目塞进 aux。
          线上实测 13,615 条 / 5.9MB → 响应 5.6MB、缓存超 6MB 上限永久写不进去(8025)、
          每个请求都重算。聚合接口只发"够卡片渲染"的一段(三类各 3 条), 全量改由
          /dashboard/own-health 分页拉(弹窗里翻到底即全量, 一条不少)。
        """
        try:
            from routes.dashboard_own_health import own_health
            return own_health(channel, full=0, per_type=3)
        except Exception as _e:
            try_err("dash", "自有仓健康计算降级", _e)
            return _EMPTY_OWN

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as _ex:
        _f1 = _ex.submit(_block_alerts)
        _f2 = _ex.submit(_block_stock_low)
        _f3 = _ex.submit(_block_own)
        try:
            alerts, alert_counts_full = _f1.result()
        except Exception as _e:
            try_err('dash', '告警块降级', _e)
            alerts = []
            alert_counts_full = {"total": 0, "by_type": {}, "by_severity": {}, "replenish": 0,
                                 "non_replenish": 0, "by_warehouse": {}, "ls_warehouse": {},
                                 "slow_warehouse": {}, "rp_warehouse": {}}
        try:
            _sl = _f2.result()
        except Exception as _e:
            try_err('dash', '库存概览降级', _e)
            _sl = {"out": 0, "low": 0, "total": 0, "items": [], "bc_out": [], "code_out": 0}
        try:
            _own = _f3.result()
        except Exception:
            _own = _EMPTY_OWN

    return {
        "alerts": alerts,
        "alertCounts": alert_counts_full,
        # 三个数**同一批判定单元**(out 与 low 互斥, 相加才有意义); 粒度跟随渠道补货模式。
        #   2026-11-06 前: out 数"库存行"、low 数"判定单元", 两者相加后既不是行数也不是 SKU 数。
        "stockOverview": {"items": _sl["items"],
                          "out_of_stock_count": _sl["out"],
                          "low_stock_count": _sl["low"],
                          # 码级断货(部分码=0 但 SKU 合计掩盖): 只在多码单元出现,
                          #   是 out/low 之外的**补充可见性**, 不混进互斥两档
                          "code_out_count": _sl.get("code_out", 0),
                          "unit_total": _sl.get("total", 0),
                          "unit_ok": _sl.get("ok", 0),
                          "total": _sl["out"] + _sl["low"]},
        "bcOutOfStock": _sl.get("bc_out") or [],
        "ownHealth": _own,
    }


@router.get("/dashboard/own-health")
@traced
def dashboard_own_health(channel: str = "jd", offset: int = 0, limit: int = 200,
                         only: str = "", per_type: int = 0, debug: int = 0):
    """自有仓健康**明细**(分页) —— 聚合接口 /dashboard/aux 只发前若干条卡面行

    2026-11-06 为什么要单独一个口: 这段明细随规模线性增长(线上实测 13,615 条 / 5.9MB),
    放进 aux 会把"看板首屏"变成"下载一个 5.6MB 的清单", 并且写不进共享缓存 ——
    首屏与明细是两种读法, 本就该两个口。弹窗按页拉, 翻到底即全量, 一条不少。
    """
    from routes.dashboard_own_health import own_health as _oh
    _lim = max(1, min(int(limit or 200), 500))
    return ok(_oh(channel, full=1, limit=_lim, offset=max(0, int(offset or 0)),
                  only=only, per_type=int(per_type or 0), debug=int(debug or 0)))


@router.get("/dashboard/own-health/state")
@traced
def own_health_state(channel: str = "jd", force: int = 0):
    """自有仓健康读模型诊断口 —— fp/gen/行数/计数/分段耗时逐项可查, 不靠猜

    force=1: 立即重建一次(同步, 约 2~3s) —— 排查"为什么还在现算"时用。
    `OWN_HEALTH_READ=0` 可整体回退到现算(不需发版)。
    """
    from core import own_store as _OS
    if force:
        _r = _OS.build(channel, force=True)
    else:
        _r = {"skipped": "no-force"}
    return ok({"build": _r, "state": _OS.status(channel)})


@router.get("/dashboard/stock-risk")
@traced
def stock_risk(channel: str = "jd", full: int = 0, legacy: int = 0, compare: int = 0,
               limit: int = 0, offset: int = 0, level: str = ""):
    """濒临断货(30s 共享表缓存)+单独无锁路径(2026-10-11: 行锁对 stock-risk 16s/无锁 2s——无锁保留;
    瞬时 84s 环境波动(无锁同)——非锁非代码——TiDB serverless 偶发)
    2026-10-17 修复: 曾裸 REPLACE + except 静默吞异常 → 缓存永不落库 → 每次全量冷算 2.5-14s;
    改为无锁缓存(命中秒回/未算写回/写入失败 try_err 留痕——不再静默)
    2026-11-02 S2-1: 手写缓存换成统一 cache_get —— 键族归一到首位(否则域级失效匹配不到),
    并复用"写入令牌"守卫; full=1 体积大(曾 11.6MB)仍用 max_bytes 挡住不落表。
    2026-11-07 P0: 主体改读 risk_snapshot（重建时算好）；`legacy=1` 强制现算用于对账，
    `compare=1` 返回两路逐字段差异（不落缓存）。"""
    if compare:
        from routes.dashboard_stock_risk import _risk_compare
        return ok(_risk_compare(channel, full=full))
    from routes.analysis_cache import _CACHE_V as _CV
    # limit/offset 必须进键: 否则第 1 页的响应被第 3 页命中, 弹窗翻页会看到重复行。
    _lim = max(0, min(int(limit or 0), 2000))
    _off = max(0, int(offset or 0))
    _key = "stock_risk|%s|%s|%s|%s|%s|%s|%s" % (_CV, channel, full, legacy, _lim, _off, level)

    def _build():
        try:
            return _stock_risk(channel, full=full, legacy=bool(legacy), limit=_lim, offset=_off,
                               level=level or "")
        except Exception as _e:
            try_err('dash', 'stock_risk 计算失败(重试一次)', _e)
            # 2026-11-06 P3-5: 原来这里 `return _stock_risk(...)` —— 原样再调一次就算"降级",
            #   第二次异常会直接冒到 cache_get 的 except, 整个接口 500; 而确定性错误
            #   (列名/口径写错)会稳定跑两遍、留双份日志, 排障时看到两条一样的异常像两起故障。
            #   现在明确两段: ① 一次重试 → 治 TiDB 瞬时抖动; ② 仍失败就返回**空结构** →
            #   断货卡数字归零(而不是整页报错), 且 try_err 明确留痕, 不会被当成"真的没有断货"。
            try:
                return _stock_risk(channel, full=full, legacy=bool(legacy), limit=_lim, offset=_off,
                               level=level or "")
            except Exception as _e2:
                try_err('dash', 'stock_risk 二次失败(返回空结构, 30s 后重算)', _e2)
                return {"items": [], "total": 0, "critical": 0, "warning": 0,
                        "bcItems": [], "bcTotal": 0, "bcCritical": 0, "bcWarning": 0,
                        "cItems": [], "cTotal": 0, "cCritical": 0, "cWarning": 0,
                        "ownItems": [], "ownTotal": 0, "ownCritical": 0, "ownWarning": 0}

    # 2026-11-07 S0 防雪崩: 与 aux 同一道名额(全局 3), 拿不到返回旧快照 —— 断货卡保持"有数"
    from db import heavy as _heavy, HeavyBusy as _HeavyBusy
    from routes.analysis_cache import cache_stale as _cache_stale
    try:
        with _heavy("stock-risk", timeout=15):
            return ok(_cache_get(_key, _RISK_TTL, _build, max_bytes=6 * 1024 * 1024))
    except _HeavyBusy as _eh:
        try_err('dash', 'stock-risk 并发繁忙, 返回旧快照', _eh)
        _st = _cache_stale(_key)
        if _st is not None:
            return ok(_st)
        return ok({"items": [], "total": 0, "critical": 0, "warning": 0,
                   "bcItems": [], "bcTotal": 0, "bcCritical": 0, "bcWarning": 0,
                   "cItems": [], "cTotal": 0, "cCritical": 0, "cWarning": 0,
                   "ownItems": [], "ownTotal": 0, "ownCritical": 0, "ownWarning": 0})


_accel_cache = {}
_ACCEL_TTL = 60




def _hourly_accel(channel, now, ratio=1.3, min_qty=10.0):
    """P1 加速消耗判定: 当天累计销量 vs 前3天同时刻(当前小时前)累计平均

    比值 ≥ ratio(默认1.3) 且样本量足够(≥min_qty 单) → 视为加速(大促/秒杀), 返回 {sku: 倍率}
    (需求按当前流速放大 → adj_dos 缩短 → 更易判濒临)
    60s 共享表缓存: 当天订单近 1 小时基本不变, 避免 stock-risk 每次重算重复查 orders
    """
    _key = "accel|%s|%s|%s" % (channel, ratio, min_qty)
    return _cache_get(_key, _ACCEL_TTL,
                      lambda: _compute_accel(channel, now, ratio, min_qty))


def _compute_accel(channel, now, ratio, min_qty):
    from datetime import timedelta
    # 2026-11-06 审计 P0-15: 原用 SALES_STATUSES(仅"已完成") → 今天的单当天不可能完成,
    #   today_q 恒空 → 加速判定恒空、功能等于没开。改需求口径(待发货+已发货+已完成)。
    paid = tuple(DEMAND_STATUSES)  # 需求口径(排除申请退款/已退货/待确认)
    today = now.strftime("%Y-%m-%d")
    since = (now - timedelta(days=3)).strftime("%Y-%m-%d 00:00:00")
    cur_h = now.hour
    # IN 参数化(曾 % 预格式化整条 SQL 只给 IN 参数 → channel/ordered_at %s 无参数 → not enough arguments)
    _in = ",".join(["%s"] * len(paid))
    rows = query(
        "SELECT sku, DATE(ordered_at) AS d, HOUR(ordered_at) AS h, SUM(quantity) AS q "
        "FROM orders WHERE channel=%s AND ordered_at>=%s "
        "AND order_status IN (" + _in + ") AND (deleted_at IS NULL OR deleted_at='') "
        "GROUP BY sku, DATE(ordered_at), HOUR(ordered_at)",
        [channel, since] + list(paid))
    hist = {}
    today_q = {}
    for r in rows:
        sku = str(r.get("sku") or "")
        d = str(r.get("d") or "")[:10]
        h = int(r.get("h") or 0)
        q = int(r.get("q") or 0)
        if d == today:
            today_q[sku] = today_q.get(sku, 0) + q
        elif h < cur_h:
            hist[sku] = hist.get(sku, 0) + q / 3.0
    out = {}
    for sku, tq in today_q.items():
        if tq < min_qty:
            continue
        hq = hist.get(sku, 0)
        if hq >= min_qty and tq / hq >= ratio:
            out[sku] = round(tq / hq, 2)
    return out




