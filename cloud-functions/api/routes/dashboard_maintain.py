"""dashboard 维护/追平模块(2026-10-11 拆分——降低看板核心回归风险; dashboard.py re-export 保持调用不变)
函数: _rebuild_day_agg(agg 物化增量) / _snapshot_step(快照分段续跑) / _track_data(数据追平·三 MAX 合并)
"""
from datetime import datetime, timedelta, timezone

from db import query, one, execute
from routes.common import try_err, status_cond as _status_cond



def _rebuild_day_agg(days=90, channel=None):
    """物化日级汇总 orders_day_agg(2026-09-15 看板提速): 同口径聚合历史订单(与 _build_summary 同 SQL)
    幂等(ON DUPLICATE 累加); summary 历史区间读 agg(万级) 替代 19 万行直查
    返回 bool(成功/失败)——调用方据此置/清 agg_dirty 脏标记(封死重建失败边界)"""
    try:
        _start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        _chw = " AND channel=%s" if channel else ""
        _chp = [channel] if channel else []
        _sql = ("INSERT INTO orders_day_agg(`date`, channel, order_status, store, gmv, subsidy, cnt) "
                "SELECT DATE(ordered_at), channel, order_status, store, "
                "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)), "
                "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)), COUNT(*) "
                "FROM orders WHERE (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %%s%s "
                "GROUP BY DATE(ordered_at), channel, order_status, store "
                "ON DUPLICATE KEY UPDATE gmv=VALUES(gmv), subsidy=VALUES(subsidy), cnt=VALUES(cnt)"
                % (_status_cond(), _status_cond(), _chw))
        execute(_sql, [_start] + _chp)
        return True
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)
            return False

def _mark_agg_dirty():
    """agg 重建失败置位(幂等, 1 行): summary 陈旧检测读到 → 降级直查, 不误用缺数据 agg"""
    try:
        execute("INSERT INTO agg_dirty(id) VALUES(1) ON DUPLICATE KEY UPDATE ts=NOW(6)")
    except Exception as _e:
            try_err('dashboard', 'agg_dirty 置位降级', _e)

def _clear_agg_dirty():
    """agg 重建成功清位(自愈): 下次写操作重建成功 → 恢复 agg 路径"""
    try:
        execute("DELETE FROM agg_dirty WHERE id=1")
    except Exception as _e:
            try_err('dashboard', 'agg_dirty 清位降级', _e)

def _snapshot_step(channel, _db2):
    try:
        from db import execute as _elog
        _elog("INSERT INTO quality_logs(log_type, level, message, source) "
              "VALUES('perf_debug','info',%s,'dash')",
              ("快照续段: ch=%s db2=%s" % (channel, _db2)))
    except Exception:
        pass
    """快照分段续跑(2026-10-10): 每 miss 推进 10 天(幂等 ON DUPLICATE)——12.6万行单次 INSERT 8-10s
    拆分为 10天/段 ~1s, 多 miss 追平至数据最新; 准确性=段互斥([from,to) 并集=全量)/完整性=推进至 orders MAX
    """
    try:
        from datetime import timedelta as _td
        _now = datetime.now(timezone.utc)
        _cur = one("SELECT MAX(`date`) AS m FROM daily_sales_snapshot WHERE channel=%s", [channel]) or {}
        _cur_d = str(_cur.get("m") or "")[:10]
        _frm = datetime.strptime(_cur_d, "%Y-%m-%d") + _td(days=1) if _cur_d else (datetime.strptime(_db2, "%Y-%m-%d") - _td(days=90))
        _to = min(_frm + _td(days=10), datetime.strptime(_db2, "%Y-%m-%d") + _td(days=1))
        if _frm >= _to:
            return
        execute(
            "INSERT INTO daily_sales_snapshot(`date`, channel, sku, warehouse, order_count) "
            "SELECT DATE(ordered_at), channel, sku, warehouse, SUM(quantity) FROM orders "
            "WHERE order_status IN ('待发货','已发货','已完成') AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at >= %s AND ordered_at < %s "
            "GROUP BY DATE(ordered_at), channel, sku, warehouse "
            "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count)",
            [_frm.strftime("%Y-%m-%d"), _to.strftime("%Y-%m-%d")])
        try:
            from db import execute as _elog2
            _elog2("INSERT INTO quality_logs(log_type, level, message, source) "
                   "VALUES('perf_debug','info',%s,'dash')",
                   ("快照续段完成: %s~%s" % (_frm.strftime('%Y-%m-%d'), _to.strftime('%Y-%m-%d'))))
        except Exception:
            pass
    except Exception as _e:
            try_err('dashboard', '快照续段降级', _e)

def _track_data(channel, _db2):
    """数据追平(即时): 一次 SQL 合并三 MAX 子查询(orders/快照/agg——3 查询→1 往返)——
    每 miss 即时检查追平(非 TTL 降频——物化即时跟数据); 写操作后另有 invalidate_all 触发增量"""
    try:
        _r = one("SELECT (SELECT COALESCE(MAX(DATE(ordered_at)),'') FROM orders "
                 "WHERE (deleted_at IS NULL OR deleted_at='')) AS om, "
                 "(SELECT MAX(`date`) FROM daily_sales_snapshot WHERE channel=%s) AS sm, "
                 "(SELECT COALESCE(MAX(`date`),'') FROM orders_day_agg) AS am", [channel]) or {}
        _db2 = str(_r.get("om") or "")[:10]
        _m = str(_r.get("sm") or "")[:10]
        if (not _m) or (_db2 and _m < _db2):
            _snapshot_step(channel, _db2)
        _amax = str(_r.get("am") or "")[:10]
        if _db2 and _amax < _db2:
            if _rebuild_day_agg(60):
                _clear_agg_dirty()
            else:
                _mark_agg_dirty()
    except Exception as _e:
            try_err('dashboard', '数据追平降级', _e)
