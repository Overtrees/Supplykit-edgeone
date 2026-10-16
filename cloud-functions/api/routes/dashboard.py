"""原生 dashboard 路由: summary(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone
import time as _time

from fastapi import APIRouter

from db import query, one, execute
from routes.common import ok, PAID_STATUSES, SALES_STATUSES, traced, try_err

router = APIRouter(tags=["dashboard"])

from routes.dashboard_maintain import _track_data, _snapshot_step, _rebuild_day_agg
from routes.dashboard_stock_risk import _stock_risk, _grade_risk, _rule_params
from routes.dashboard_health import _health_index, _sync_health_snapshot
from routes.analysis_cache import register as _register_cache, cache_get as _cache_get
_register_cache(lambda: (_summary_cache.clear(), _aux_cache.clear(), _risk_cache.clear(), _accel_cache.clear()))

_PAID = tuple(PAID_STATUSES)


def _status_cond(col="order_status"):
    """GMV 已支付口径条件"""
    return "%s IN (%s)" % (col, ",".join(["'%s'" % s for s in _PAID]))


_summary_cache = {}
_SUMMARY_TTL = 30





def _daily_maintenance(channel="jd"):
    """应用层每日维护(治本: 不依赖 edgeone schedules——实测未自动触发)
    抢占 maintenance_log(date+task 主键, INSERT IGNORE 多实例安全), 已维护则跳过(1 次轻插入)
    内容: 快照/agg 数据追平(每 miss) + 每日例行(日志治理等)"""
    try:
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _got = execute("INSERT IGNORE INTO maintenance_log(`date`, task) VALUES(%s, 'daily')", [_today])
        if not _got:
            return  # 今天已维护(本实例或其他实例)
        # ② digest 预填(每日维护——get_sales_digest 填表缓存——健康/低库存/规则/补货/断货消费方首访命中——首冷降频)
        try:
            from biz.sales import get_sales_digest
            get_sales_digest(channel)
            if channel == "jd":
                get_sales_digest("other")
        except Exception as _e:
                try_err('dashboard', 'digest 预填降级', _e)
        # ③ quality_logs 膨胀治理(双保险, 不依赖 cron cleanup-logs): 超 500 截断至 300
        #   (id 曾达 159 万级——大量写删循环, 每日维护抢占保证只跑一次, 与 cleanup 同逻辑幂等)
        try:
            _c = one("SELECT COUNT(*) AS c FROM quality_logs") or {}
            if int(_c.get("c") or 0) > 500:
                _k = one("SELECT id FROM quality_logs ORDER BY id DESC LIMIT 300") or {}
                if _k.get("id"):
                    execute("DELETE FROM quality_logs WHERE id < %s", [_k.get("id")])
        except Exception as _e:
                try_err('dashboard', '静默降级', _e)
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
            _yest = (datetime.now(timezone.utc) - _td2(days=1)).strftime("%Y-%m-%d")
            from routes.misc import _export_md as _em
            for _sc in ("user", "dev", "all"):
                _d3, _cnt3, _md3 = _em(_yest, _sc, "")
                execute("INSERT INTO log_archives(`date`, `scope`, markdown, `count`, updated_at) "
                        "VALUES(%s,%s,%s,%s,NOW(6)) ON DUPLICATE KEY UPDATE "
                        "markdown=VALUES(markdown), `count`=VALUES(`count`), updated_at=NOW(6)",
                        [_d3, _sc, _md3, _cnt3])
            _cut = (datetime.now(timezone.utc) - _td2(days=90)).strftime("%Y-%m-%d")
            execute("DELETE FROM log_archives WHERE `date` < %s", [_cut])
        except Exception as _e:
                try_err('dashboard', '静默降级', _e)
    except Exception as _me:
        try:
            execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                    "VALUES('maint','error',%s,%s,'dash')",
                    ("daily_maintenance 失败", str(_me)[:200]))
        except Exception as _e:
                try_err('dashboard', '静默降级', _e)


def _daily_rules_guard():
    """每日规则应用层兜底(不依赖 schedules——平台实测从未触发):
    maintenance_log(date+'daily_rules' 主键) 抢占, 抢到则后台线程执行 run_daily_rules()(不阻塞 summary);
    失败回滚抢占标记, 下次 summary 请求重试(多实例安全)"""
    try:
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _got = execute("INSERT IGNORE INTO maintenance_log(`date`, task) VALUES(%s, 'daily_rules')", [_today])
        if not _got:
            return  # 今天已评估(本实例或其他实例)

        def _run():
            try:
                from routes.cron import run_daily_rules
                _r = run_daily_rules()
                execute("INSERT INTO quality_logs(log_type, level, message, source) "
                        "VALUES('maint','info',%s,'dash')",
                        ("应用层兜底 daily-rules 完成: 孤儿清理 %d, 规则触发 %d"
                         % (_r.get("orphan_cleaned") or 0, len(_r.get("rules_triggered") or [])),))
            except Exception as _e:
                try:
                    # 失败回滚抢占标记 → 下次 summary 请求重试
                    execute("DELETE FROM maintenance_log WHERE `date`=%s AND task='daily_rules'", [_today])
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


@router.get("/dashboard/summary")
@traced
def dashboard_summary(channel: str = "jd", start_date: str = "", end_date: str = "", mode: str = "bbcc"):
    """看板汇总(30s 共享表缓存——TiDB 表跨实例一致; 写操作 invalidate_all 全局失效, 数据变化最多 30s 可见)
    附带: 应用层每日维护(快照新鲜度自愈 + 每日规则兜底, 不依赖 schedules) + 健康分快照幂等写入(趋势数据源)"""
    _key = "dash_summary|%s|%s|%s|%s" % (channel, start_date, end_date, mode)
    # 维护/守卫/快照仅在缓存 miss 时执行(命中直接返回——省 ~1s; 维护每天多次 miss 仍覆盖)

    def _b():
        # 数据追平(每 miss 即时——一次 SQL 三 MAX 合并, 物化跟数据不延迟)
        try:
            _track_data(channel, None)
        except Exception as _e:
                try_err('dashboard', '数据追平降级', _e)
        # 维护/守卫合并判断: 一次查询今天已跑状态(未跑则 INSERT IGNORE 抢占——原子防并发双跑)
        try:
            from db import query as _qm
            _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _done = {str(r.get("task") or "") for r in
                     _qm("SELECT task FROM maintenance_log WHERE `date`=%s AND task IN ('daily','daily_rules')",
                         [_today])}
            if "daily" not in _done:
                _daily_maintenance(channel)
            if "daily_rules" not in _done:
                _daily_rules_guard()
        except Exception as _e:
            try_err('dashboard', '维护守卫降级(仍计算看板)', _e)
        _r = _build_summary(channel, start_date, end_date, mode)
        _sync_health_snapshot(channel, (_r or {}).get("health_index") if isinstance(_r, dict) else None)
        return _r

    _result = _cache_get(_key, _SUMMARY_TTL, _b)
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
    return query("SELECT sku, warehouse_type, available_qty FROM inventory "
                 "WHERE channel=%s", [channel])


def _q_digest(channel):
    try:
        from biz.sales import get_sales_digest
        _, _ds_map, _ = get_sales_digest(channel)
        return _ds_map
    except Exception:
        return {}


def _build_summary_agg(channel, days60, now, mode="bbcc"):
    """agg 路径(强实时): 前天及以前读 orders_day_agg, 昨天+今天实时直查 orders(补录/新增当日立即反映)
    4 个独立查询并行(agg/当天直查/inventory/digest——TiDB 多实例并发, 非缓存 3s→1.5s);
    近 2 天直查失败降级仅 agg"""
    today = now.strftime("%Y-%m-%d")
    d1 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _st30 = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    import time as _t
    _t0 = _t.time()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as _ex:  # 2026-10-11 线程收敛(4→3——TiDB 连接数压力降, 查询并行收益保留)
        _f1 = _ex.submit(_q_agg, channel, days60, d1)
        _f2 = _ex.submit(_q_recent, channel, d1)
        _f3 = _ex.submit(_q_inventory, channel)
        _f4 = _ex.submit(_q_digest, channel)
        agg = _f1.result()
        try:
            recent = _f2.result()
        except Exception:
            recent = []  # 近 2 天直查失败: 仅 agg, 不阻塞
        _inv_rows = _f3.result()
        _ds_map = _f4.result()
    _t1 = _t.time()
    _ass = _assemble(list(agg) + list(recent), channel, _st30, today, mode,
                     _inv_rows=_inv_rows, _ds_map=_ds_map)
    _t2 = _t.time()
    # 临时计时(定位非缓存 3s 分布——排查后移除)
    try:
        from db import execute as _et
        _et("INSERT INTO quality_logs(log_type, level, message, details, source) "
            "VALUES('perf_debug','info',%s,%s,'dash')",
            ("summary agg 计时: 查询 %.2fs 组装 %.2fs" % (_t1 - _t0, _t2 - _t1),
             "parallel"))
    except Exception:
        pass
    return _ass


def _build_summary(channel, start_date, end_date, mode="bbcc"):
    """summary 计算体(共享缓存 builder; 原函数体迁移)"""
    now = datetime.now(timezone.utc)
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

    days60 = (now - timedelta(days=59)).strftime("%Y-%m-%d")
    # 物化表优先(历史区间读 orders_day_agg 万级, 当天实时直查 orders): agg 不可用降级直查
    try:
        _aggc = one("SELECT COUNT(*) AS c, COALESCE(MAX(`date`),'') AS m FROM orders_day_agg WHERE channel=%s", [channel]) or {}
        _agg_ok = int(_aggc.get("c") or 0) > 0
        # 陈旧检测: agg 最新日期落后 3 天以上(如重置后旧残留) → 降级直查, 不误用旧数据
        _aggmx = str(_aggc.get("m") or "")[:10]
        _agg_cut = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        if _agg_ok and _aggmx < _agg_cut:
            _agg_ok = False
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
    return _assemble(rows, channel, (now - timedelta(days=29)).strftime("%Y-%m-%d"), today, mode)


def _assemble(rows, channel, start_date, end_date, mode="bbcc", _inv_rows=None, _ds_map=None):
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
    stages = [("总订单", ftotal, 100.0)]
    for n in ["待确认", "待发货", "已发货", "已完成"]:
        v = funnel.get(n, 0)
        stages.append((n, v, round(v / ftotal * 100, 1) if ftotal else 0))
    funnel_res = []
    for i, (n, v, pct) in enumerate(stages):
        prev = stages[i - 1][1] if i > 0 else ftotal
        funnel_res.append({"name": n, "value": v, "percentage": pct,
                           "conversion": round(min(v / prev * 100, 100), 1) if prev else 0})

    now = datetime.now(timezone.utc)
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
    _prev_m1 = (_last_m1 and (now.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d") if False else None
    _tmp = now.replace(day=1) - timedelta(days=1)  # 上月最后一天
    _prev_m1 = _tmp.replace(day=1) - timedelta(days=1)
    _prev_m1 = _prev_m1.replace(day=1).strftime("%Y-%m-%d")           # 上上月1号
    _prev_m_end = (_tmp - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")  # 占位(实际用上月结束-一个月)
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

    tg, to = _agg(today_s, today_s)
    pg, po = _agg(d1, d1)
    wg, wo = _agg(d7, today_s)
    pwg, pwo = _agg(d14, _prev_sun)
    mg, mo = _agg(d30, d31)
    pmg, pmo = _agg(_prev_m1, _prev_m_end)

    periods = {
        "today": {"gmv": tg, "orders": to, "days": 1, "prev_gmv": pg, "prev_orders": po,
                  "net_gmv": round(tg - _refund(today_s, today_s), 2), "subsidy_amount": round(_sub(today_s, today_s), 2),
                  "payout": round(tg - _refund(today_s, today_s) - _sub(today_s, today_s), 2)},
        "week": {"gmv": wg, "orders": wo, "days": (_last_sun and 7), "prev_gmv": pwg, "prev_orders": pwo,
                 "net_gmv": round(wg - _refund(d7, today_s), 2), "subsidy_amount": round(_sub(d7, today_s), 2),
                 "payout": round(wg - _refund(d7, today_s) - _sub(d7, today_s), 2)},
        "month": {"gmv": mg, "orders": mo, "days": 30, "prev_gmv": pmg, "prev_orders": pmo,
                  "net_gmv": round(mg - _refund(d30, today_s), 2), "subsidy_amount": round(_sub(d30, today_s), 2),
                  "payout": round(mg - _refund(d30, today_s) - _sub(d30, today_s), 2)},
    }

    health = _health_index(channel, mode, _inv_rows, _ds_map)
    # 低库存计数(动态安全线, 替代静态 SQL——seed safety_qty=0 后静态判定恒 0)
    low_stock = {"c": 0}
    try:
        _ls_rows = ([x for x in _inv_rows if int(x.get("available_qty") or 0) > 0]
                    if _inv_rows is not None else
                    query("SELECT sku, warehouse_type, available_qty FROM inventory "
                          "WHERE channel=%s AND available_qty>0", [channel]))
        _ls_ds = _ds_map if _ds_map is not None else {}
        if not _ls_ds:
            try:
                from biz.sales import load_daily_sales_grouped as _ldsg, calc_sales_multi as _csm, rolling_predict as _rp2
                _by2, _ = _ldsg(28, channel)
                _m2 = _csm(_by2, windows=[7, 14, 28])
                _ls_ds = {s: _rp2(_m2[7].get(s, 0), _m2[14].get(s, 0), _m2[28].get(s, 0)) for s in _by2}
            except Exception:
                pass
        _cfg_ls = {}
        try:
            _cfg_ls = {r.get("key"): r.get("value") for r in
                       query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])}
        except Exception:
            pass
        def _mcl(key, m2, default):
            v = _cfg_ls.get("mode_%s_%s" % (m2, key))
            if v is None:
                v = _cfg_ls.get(key)
            try:
                return int(float(v or default))
            except Exception:
                return default
        _pbbcc = (_mcl("b_to_c_days", "bbcc", 3) + _mcl("c_safety_days", "bbcc", 0)
                  + _mcl("ship_to_b_days", "bbcc", 0) + _mcl("safety_multiplier", "bbcc", 0))
        _ptrad = _mcl("lead_time_days", "traditional", 10) + _mcl("safety_multiplier", "traditional", 0)
        _ppur = _mcl("purchase_lead_days", "bbcc", 14) + _mcl("purchase_safety_days", "bbcc", 3)
        _per_ls = _pbbcc if mode == "bbcc" else _ptrad
        _ls_cnt = 0
        for _r5 in _ls_rows:
            _wt5 = str(_r5.get("warehouse_type") or "")
            _per5 = _ppur if _wt5 == "own" else _per_ls
            _d5 = _ls_ds.get(str(_r5.get("sku") or ""), 0)
            _dyn5 = round(_d5 * _per5, 1) if _d5 > 0 and _per5 > 0 else 0.0
            if _dyn5 > 0 and int(_r5.get("available_qty") or 0) < _dyn5:
                _ls_cnt += 1
        low_stock = {"c": _ls_cnt}
    except Exception as _e:
            try_err('dashboard', '低库存计数动态降级', _e)
    alert_count = one("SELECT COUNT(*) AS c FROM alerts WHERE channel=%s AND status='active'", [channel]) or {}
    product_count = one("SELECT COUNT(*) AS c FROM products WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')", [channel]) or {}
    supplier_count = one("SELECT COUNT(*) AS c FROM suppliers") or {}

    summary = {
        "gmv": round(gmv, 2), "net_gmv": round(gmv - refund_amt, 2),
        "refund_amount": round(refund_amt, 2), "subsidy_amount": round(subsidy, 2),
        "payout": round(gmv - refund_amt - subsidy, 2),
        "total_orders": total_orders, "pending_count": pending, "refund_count": refund,
        "low_stock_count": int(low_stock.get("c") or 0), "active_alerts": int(alert_count.get("c") or 0),
        "total_products": int(product_count.get("c") or 0), "total_suppliers": int(supplier_count.get("c") or 0),
    }
    # 周期趋势(前端 gmv 小卡图表按今日/本周/本月联动)
    def _trend_range(d0, d1):
        return [{"日期": x.get("日期"), "GMV": x.get("GMV"), "订单数": x.get("订单数"),
                 "net_gmv": x.get("net_gmv"), "payout": x.get("payout")}
                for x in trend_data if d0 <= str(x.get("日期") or "")[:10] <= d1]

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
        return [{"name": k, "gmv": round(v, 2),
                 "net_gmv": round(v - srf.get(k, 0), 2),
                 "payout": round(v - srf.get(k, 0) - ssb.get(k, 0), 2)}
                for k, v in sorted(sg.items(), key=lambda x: -x[1])]

    periods["today_trend"] = _trend_range(today_s, today_s)
    periods["week_trend"] = _trend_range(d7, today_s)
    periods["month_trend"] = _trend_range(d30, today_s)

    # 品牌维度(店铺 GMV 卡切品牌: 近 60 天 paid 订单按品牌聚合, join products)
    # v4-paidset: g/sb 必须用 _status_cond()(PAID 4 状态含申请退款) —— 历史线上曾误用非支付状态集(待确认+已退货)导致品牌 GMV 低估/净额为负
    brands_map = {}
    try:
        _br = query(
            "SELECT COALESCE(p.brand,'') AS brand, "
            "SUM(IF(%s, o.total_amount - COALESCE(o.discount_amount,0) "
            "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS g, "
            "SUM(IF(%s, COALESCE(o.subsidy_amount,0), 0)) AS sb, "
            "SUM(IF(o.order_status = '申请退款', o.total_amount - COALESCE(o.discount_amount,0) "
            "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS rf "
            "FROM orders o LEFT JOIN (SELECT DISTINCT sku, brand, channel FROM products WHERE brand IS NOT NULL AND brand != '') p ON o.sku=p.sku AND o.channel=p.channel "
            "WHERE o.channel=%%s AND (o.deleted_at IS NULL OR o.deleted_at='') "
            "AND o.ordered_at>=%%s GROUP BY p.brand" % (_status_cond(), _status_cond()),
            [channel, d60 + " 00:00:00"])
        for _r in _br:
            _b = _r.get("brand") or "未分类"
            if not _b or _b == "未分类":
                continue
            brands_map[_b] = {"g": round(float(_r.get("g") or 0), 2),
                              "rf": round(float(_r.get("rf") or 0), 2),
                              "sb": round(float(_r.get("sb") or 0), 2)}
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)
    brands = [{"name": k, "gmv": v["g"], "net_gmv": round(v["g"] - v["rf"], 2),
              "payout": round(v["g"] - v["rf"] - v["sb"], 2)}
              for k, v in sorted(brands_map.items(), key=lambda x: -x[1]["g"])]
    def _brands_range(d0):
        """按起始日聚合品牌 GMV(周期联动)"""
        try:
            _bm = {}
            for _r2 in query(
                    "SELECT COALESCE(p.brand,'') AS brand, "
                    "SUM(IF(%s, o.total_amount - COALESCE(o.discount_amount,0) "
                    "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS g, "
                    "SUM(IF(%s, COALESCE(o.subsidy_amount,0), 0)) AS sb, "
                    "SUM(IF(o.order_status = '申请退款', o.total_amount - COALESCE(o.discount_amount,0) "
                    "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS rf "
                    "FROM orders o LEFT JOIN (SELECT DISTINCT sku, brand, channel FROM products WHERE brand IS NOT NULL AND brand != '') p ON o.sku=p.sku AND o.channel=p.channel "
                    "WHERE o.channel=%%s AND (o.deleted_at IS NULL OR o.deleted_at='') "
                    "AND o.ordered_at>=%%s GROUP BY p.brand" % (_status_cond(), _status_cond()),
                    [channel, d0 + " 00:00:00"]):
                _b = _r2.get("brand") or ""
                if not _b or _b == "未分类":
                    continue
                _bm[_b] = {"g": round(float(_r2.get("g") or 0), 2),
                           "rf": round(float(_r2.get("rf") or 0), 2),
                           "sb": round(float(_r2.get("sb") or 0), 2)}
            return [{"name": k, "gmv": v["g"], "net_gmv": round(v["g"] - v["rf"], 2),
                     "payout": round(v["g"] - v["rf"] - v["sb"], 2)}
                    for k, v in sorted(_bm.items(), key=lambda x: -x[1]["g"])]
        except Exception:
            return []
    period_brands = {"today": _brands_range(today_s),
                     "week": _brands_range(d7),
                     "month": _brands_range(d30)}
    period_stores = {"today": _stores_range(today_s, today_s),
                     "week": _stores_range(d7, today_s),
                     "month": _stores_range(d30, today_s)}
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
        _total = sum(_ft.values())
        _stages = [("总订单", _total, 100.0)]
        for n in ["待确认", "待发货", "已发货", "已完成"]:
            v = _ft.get(n, 0)
            _stages.append((n, v, round(v / _total * 100, 1) if _total else 0))
        _out = []
        for i, (n, v, pct) in enumerate(_stages):
            prev = _stages[i - 1][1] if i > 0 else _total
            _out.append({"name": n, "value": v, "percentage": pct,
                         "conversion": round(min(v / prev * 100, 100), 1) if prev else 0})
        return _out
    period_funnel = {"today": _funnel_range(today_s, today_s),
                     "week": _funnel_range(d7, today_s),
                     "month": _funnel_range(d30, today_s)}
    if start_date and end_date:
        period_funnel["custom"] = _funnel_range(start_date, end_date)

    return {"summary": summary, "periods": periods, "trend": trend_data,
            "funnel": funnel_res, "period_funnel": period_funnel, "health_index": health, "stores": stores,
            "brands": brands, "period_stores": period_stores, "period_brands": period_brands}




_aux_cache = {}
_AUX_TTL = 60
_risk_cache = {}
_RISK_TTL = 30


@router.get("/dashboard/aux")
@traced
def dashboard_aux(channel: str = "jd", mode: str = "bbcc"):
    """看板辅助聚合(60s 共享表缓存——TiDB 表跨实例一致): alerts(分组配额, 低库存组按补货模式过滤维度) + alertCounts
    + stockOverview + bcOutOfStock + stockRisk

    模式跟随: bbcc → 低库存显示 BC 盘(platform/platform_b)+own(集货仓=B仓调拨源);
    traditional → 低库存显示 C 仓(platform)+own(自有/三方仓)
    """
    _key = "dash_aux|%s|%s" % (channel, mode)
    return ok(_cache_get(_key, _AUX_TTL, lambda: _build_aux(channel, mode)))


def _build_aux(channel, mode):
    """aux 计算体(共享缓存 builder)"""
    from routes.alerts import _FIELDS as _AF
    # 低库存维度过滤(跟随补货模式): bbcc → BC+own; traditional → C+own(白名单拼接)
    _wh_in = "('own','platform','platform_b')" if mode == "bbcc" else "('own','platform')"
    _wh_cond = " AND warehouse_type IN " + _wh_in
    # alerts 分组配额(2026-10-11 合并: 原 3 次串行查询 → 一次全量 + Python 分组——降查询数)
    alerts = []
    _ar = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' "
                "ORDER BY id DESC LIMIT 300" % _AF, [channel])
    _ls_c = _rp_c = _ot_c = 0
    for r in _ar:
        at = r.get("alert_type") or ""
        if at == "low_stock":
            if _ls_c < 100 and str(r.get("warehouse_type") or "") in _wh_in:
                alerts.append(r)
                _ls_c += 1
        elif at == "replenish":
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
    alert_counts_full = {"total": total, "by_type": by_type, "by_severity": by_sev,
                         "replenish": _rp, "non_replenish": total - _rp,
                         "by_warehouse": _wmap(by_wh), "ls_warehouse": _wmap(by_wh_ls),
                         "slow_warehouse": _wmap(by_wh_slow), "rp_warehouse": _wmap(by_wh_rp)}
    # stockOverview(缺货/低库存)
    out = one("SELECT COUNT(*) AS c FROM inventory WHERE channel=%s AND available_qty=0", [channel]) or {}
    # 低库存(动态安全线判定, 替代静态 safety_qty): avail < ds×周期; 维度过滤跟随模式(bbcc→BC+own / trad→C+own)
    low = {"c": 0}
    try:
        from biz.sales import get_sales_digest
        _, _dsm, _ = get_sales_digest(channel)
        _cfg2 = {r.get("key"): r.get("value") for r in
                 query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])}
        def _mc2(key, m2, default):
            v = _cfg2.get("mode_%s_%s" % (m2, key))
            if v is None:
                v = _cfg2.get(key)
            try:
                return int(float(v or default))
            except Exception:
                return default
        _per_bbcc = (_mc2("b_to_c_days", "bbcc", 3) + _mc2("c_safety_days", "bbcc", 0)
                     + _mc2("ship_to_b_days", "bbcc", 0) + _mc2("safety_multiplier", "bbcc", 0))
        _per_trad = _mc2("lead_time_days", "traditional", 10) + _mc2("safety_multiplier", "traditional", 0)
        _per_pur = _mc2("purchase_lead_days", "bbcc", 14) + _mc2("purchase_safety_days", "bbcc", 3)
        _period = _per_bbcc if mode == "bbcc" else _per_trad
        _rows2 = query("SELECT sku, warehouse_type, available_qty FROM inventory "
                       "WHERE channel=%s AND available_qty>0 AND warehouse_type IN " + _wh_in, [channel])
        _cnt = 0
        for _r2 in _rows2:
            _wt2 = str(_r2.get("warehouse_type") or "")
            _p2 = _per_pur if _wt2 == "own" else _period
            _d2 = _dsm.get(str(_r2.get("sku") or ""), 0)
            _dyn2 = round(_d2 * _p2, 1) if _d2 > 0 and _p2 > 0 else 0.0
            if _dyn2 > 0 and int(_r2.get("available_qty") or 0) < _dyn2:
                _cnt += 1
        low = {"c": _cnt}
    except Exception as _e:
            try_err('dash', '低库存动态判定降级', _e)
    so_items = query("SELECT sku, product_name, warehouse, warehouse_type, available_qty, safety_qty "
                     "FROM inventory WHERE channel=%s AND available_qty=0 ORDER BY id DESC LIMIT 100", [channel])
    # bc 合计缺货 SKU
    bc_rows = query(
        "SELECT sku, MAX(product_name) AS product_name FROM inventory "
        "WHERE channel=%s AND warehouse_type IN ('platform','platform_b') "
        "GROUP BY sku HAVING SUM(available_qty) <= 0 ORDER BY sku LIMIT 100", [channel])
    bc_out = [{"sku": r.get("sku"), "product_name": r.get("product_name") or r.get("sku"),
               "warehouse_type": "bc"} for r in bc_rows]
    _aux_result = {
        "alerts": alerts,
        "alertCounts": alert_counts_full,
        "stockOverview": {"items": so_items,
                          "out_of_stock_count": int(out.get("c") or 0),
                          "low_stock_count": int(low.get("c") or 0),
                          "total": int(out.get("c") or 0) + int(low.get("c") or 0)},
        "bcOutOfStock": bc_out,
    }
    return _aux_result


@router.get("/dashboard/stock-risk")
@traced
def stock_risk(channel: str = "jd", full: int = 0):
    """濒临断货(30s 共享表缓存)+单独无锁路径(2026-10-11: 行锁对 stock-risk 16s/无锁 2s——无锁保留;
    瞬时 84s 环境波动(无锁同)——非锁非代码——TiDB serverless 偶发)"""
    from routes.analysis_cache import _CACHE_V as _CV
    _key = "%s|stock_risk|%s|%s" % (_CV, channel, full)
    try:
        from db import one, execute
        row = one("SELECT value, TIMESTAMPDIFF(SECOND, created_at, NOW(6)) AS age "
                  "FROM analysis_cache WHERE `key`=%s", [_key])
        if row:
            age = row.get("age")
            if age is not None and int(age) < _RISK_TTL:
                return ok(_loads(row.get("value") or ""))
        val = _stock_risk(channel, full=full)
        execute("REPLACE INTO analysis_cache(`key`, value, created_at) VALUES(%s,%s,NOW(6))",
                (_key, json.dumps(val, ensure_ascii=False, default=str)))
        return ok(val)
    except Exception:
        return ok(_stock_risk(channel, full=full))


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
    paid = tuple(SALES_STATUSES)  # 加速判定用日销口径(排除申请退款)
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




