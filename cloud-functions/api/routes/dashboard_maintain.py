"""dashboard 维护/追平模块(2026-10-11 拆分——降低看板核心回归风险; dashboard.py re-export 保持调用不变)
函数: _rebuild_day_agg(agg 物化增量) / _snapshot_step(快照分段续跑) / _track_data(数据追平·三 MAX 合并)
"""
from datetime import datetime, timedelta, timezone

from db import query, one, execute
from routes.common import try_err, status_cond as _status_cond, SALES_SQL as _SALES_SQL

# 窗口完整性检测限频(每渠道 60s——多 miss 合并, 不拖每请求)
_WINDOW_LAST = {}



def _rebuild_day_agg(days=90, channel=None):
    """物化日级汇总 orders_day_agg(2026-09-15 看板提速): 同口径聚合历史订单(与 _build_summary 同 SQL)
    幂等(ON DUPLICATE 累加); summary 历史区间读 agg(万级) 替代 19 万行直查
    注: 仅 upsert 现存组合——软删致组合消失的残留由 _track_data 窗口自愈(单日精准重建)处理,
    不在全窗口 DELETE(大事务持锁 → 并发 FOR UPDATE 9004 锁超时, 实测 summary 86s)
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

def _rebuild_agg_day(channel, day):
    """单日精准重建(2026-10-25 残留根治): DELETE 该日 agg 行 + INSERT SELECT 该日订单(小事务短锁,
    不碰全窗口——大事务持锁致并发 9004)——软删使组合消失时 ON DUPLICATE 覆盖不了的残留由这里清"""
    try:
        from db import transaction
        _d2 = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        with transaction():
            execute("DELETE FROM orders_day_agg WHERE channel=%s AND `date`=%s", [channel, day])
            execute("INSERT INTO orders_day_agg(`date`, channel, order_status, store, gmv, subsidy, cnt) "
                    "SELECT DATE(ordered_at), channel, order_status, store, "
                    "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)), "
                    "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)), COUNT(*) "
                    "FROM orders WHERE (deleted_at IS NULL OR deleted_at='') AND channel=%%s "
                    "AND ordered_at >= %%s AND ordered_at < %%s "
                    "GROUP BY DATE(ordered_at), channel, order_status, store"
                    % (_status_cond(), _status_cond()),
                    [channel, day + " 00:00:00", _d2 + " 00:00:00"])
        return True
    except Exception as _e:
            try_err('dashboard', '单日重建降级', _e)
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
            "INSERT INTO daily_sales_snapshot(`date`, channel, sku, barcode, warehouse, order_count) "
            "SELECT DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse, SUM(quantity) FROM orders "
            "WHERE order_status IN (" + _SALES_SQL + ") AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at >= %s AND ordered_at < %s "
            "GROUP BY DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse "
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

def _snapshot_reconcile(channel, days=90, limit_days=10):
    """快照窗口对账自愈(2026-10-29): 软删订单后快照残留组合不会因 upsert 消失

    背景: 快照是"按组合 upsert", 订单软删后对应 (date,sku,码,仓) 组合在快照里仍在 →
    物化/补货仍读到已删订单的日销(实测: 删单后 SKU 物化残留)。agg 有窗口自愈, 快照没有。
    做法: 按天比对(快照行数/合计 vs 订单聚合), 不一致的天 **DELETE + 单日 INSERT 重建**——
      单日小事务(不锁全窗口, 避免曾出现的 9004 锁超时); 单次最多修 limit_days 天, 余下下次续跑。
    """
    try:
        from routes.common import SALES_SQL as _SQL
        _start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        _snap = {str(r.get("d"))[:10]: (int(r.get("c") or 0), int(r.get("q") or 0))
                 for r in query("SELECT `date` AS d, COUNT(*) AS c, SUM(order_count) AS q "
                                "FROM daily_sales_snapshot WHERE channel=%s AND `date`>=%s "
                                "GROUP BY `date`", [channel, _start])}
        _ord = {str(r.get("d"))[:10]: (int(r.get("c") or 0), int(r.get("q") or 0))
                for r in query("SELECT DATE(ordered_at) AS d, "
                               "COUNT(DISTINCT CONCAT_WS('|', sku, COALESCE(barcode,''), warehouse)) AS c, "
                               "SUM(quantity) AS q FROM orders WHERE channel=%s "
                               "AND order_status IN (" + _SQL + ") "
                               "AND (deleted_at IS NULL OR deleted_at='') AND ordered_at>=%s "
                               "GROUP BY DATE(ordered_at)", [channel, _start])}
        _bad = sorted(d for d in set(_snap) | set(_ord) if _snap.get(d) != _ord.get(d))
        for d in _bad[:limit_days]:
            execute("DELETE FROM daily_sales_snapshot WHERE channel=%s AND `date`=%s", [channel, d])
            execute("INSERT INTO daily_sales_snapshot(`date`, channel, sku, barcode, warehouse, order_count) "
                    "SELECT DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse, SUM(quantity) "
                    "FROM orders WHERE channel=%s AND DATE(ordered_at)=%s AND order_status IN (" + _SQL + ") "
                    "AND (deleted_at IS NULL OR deleted_at='') "
                    "GROUP BY DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse", [channel, d])
        if _bad:
            try_err('dash', '快照残留自愈 %s: 发现 %d 天不一致, 本次修 %d 天'
                    % (channel, len(_bad), min(len(_bad), limit_days)), None)
        return len(_bad)
    except Exception as _e:
        try_err('dash', '快照对账降级', _e)
        return 0


def _track_data(channel, _db2):
    """数据追平(即时): 一次 SQL 合并三 MAX 子查询(orders/快照/agg——3 查询→1 往返)——
    每 miss 即时检查追平(非 TTL 降频——物化即时跟数据); 写操作后另有 invalidate_all 触发增量
    2026-10-25 增: 60 天查询窗口行数一致性自愈(接力——每 miss 检测, 不一致则重建 60 天一次,
    ON DUPLICATE 幂等覆盖; 修复后一致即停)——兜底"非尾部缺口"(软删残留/历史遗留/手动改库:
    尾部 MAX 检测发现不了中间缺口, 曾 10-16 agg cnt 残留 2809 vs orders 1100)"""
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
            if _rebuild_day_agg(90):   # 2026-10-30 与 summary/物化/对账/回填窗口统一 90
                _clear_agg_dirty()
            else:
                _mark_agg_dirty()
        # 窗口完整性自愈(仅行数维度——增删/软删/补录; 值维度由写路径重建+agg_dirty 兜):
        # 60 天窗口 orders 行数 vs agg cnt 总和, 不一致 → 逐日定位缺口(orders 独有/agg 残留)
        # → 单日精准重建(DELETE 该日+INSERT, 小事务短锁——全窗口重建大事务曾致并发 9004 锁超时)
        # 限频 60s(模块级时间戳, 每 miss 的 2 COUNT 查询不拖请求——检测轻量低频即可)
        try:
            global _WINDOW_LAST
            _now = datetime.now(timezone.utc)
            if _WINDOW_LAST.get(channel) and (_now - _WINDOW_LAST[channel]).total_seconds() < 60:
                pass  # 窗口内已检测(上次 miss 做过), 跳过
            else:
                _WINDOW_LAST[channel] = _now
                _w60 = (_now - timedelta(days=59)).strftime("%Y-%m-%d")
                _oc60 = one("SELECT COUNT(*) AS c FROM orders WHERE channel=%s "
                            "AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s",
                            [channel, _w60]) or {}
                _ac60 = one("SELECT COALESCE(SUM(cnt),0) AS c FROM orders_day_agg "
                            "WHERE channel=%s AND `date` >= %s", [channel, _w60]) or {}
                if int(_oc60.get("c") or 0) != int(_ac60.get("c") or 0):
                    _gd = query("SELECT d.d AS d FROM "
                                "(SELECT DATE(ordered_at) AS d, COUNT(*) AS oc FROM orders "
                                "WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s "
                                "GROUP BY DATE(ordered_at)) d "
                                "LEFT JOIN (SELECT `date`, SUM(cnt) AS ac FROM orders_day_agg "
                                "WHERE channel=%s AND `date` >= %s GROUP BY `date`) a ON d.d=a.`date` "
                                "WHERE d.oc <> COALESCE(a.ac,0) OR a.ac IS NULL", [channel, _w60, channel, _w60])
                    _sd = query("SELECT a.`date` AS d FROM "
                                "(SELECT `date`, SUM(cnt) AS ac FROM orders_day_agg "
                                "WHERE channel=%s AND `date` >= %s GROUP BY `date`) a "
                                "LEFT JOIN (SELECT DATE(ordered_at) AS d, COUNT(*) AS oc FROM orders "
                                "WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s "
                                "GROUP BY DATE(ordered_at)) o ON a.`date`=o.d "
                                "WHERE o.oc IS NULL OR o.oc=0", [channel, _w60, channel, _w60])
                    _days = list({str((r or {}).get("d") or "")[:10] for r in (_gd or [])} |
                                 {str((r or {}).get("d") or "")[:10] for r in (_sd or [])})
                    _days = [x for x in _days if x]
                    if _days:
                        _ok = True
                        for _d in _days:
                            if not _rebuild_agg_day(channel, _d):
                                _ok = False
                        if _ok:
                            _clear_agg_dirty()
                        else:
                            _mark_agg_dirty()
        except Exception as _e:
                try_err('dashboard', '窗口完整性检测降级', _e)
        # 2026-10-29 P3: 每日一次日销对账(口径漂移是静默错误, 只有外部断言能发现)
        # maintenance_log 抢占(与 cron freshness 同法) → 一天只跑一次; 后台线程执行, 不拖慢 summary
        try:
            _rc = execute("INSERT IGNORE INTO maintenance_log(`date`, task) VALUES(%s,'reconcile')",
                          [datetime.now(timezone.utc).strftime("%Y-%m-%d")])
            if _rc:
                import threading as _th
                def _rc_run():
                    try:
                        from routes.cron import run_reconcile
                        run_reconcile(90)
                    except Exception as _erc:
                        try_err('dashboard', '日销对账执行降级', _erc)
                    try:
                        _snapshot_reconcile(channel)   # 软删订单后的快照残留自愈
                    except Exception as _esr:
                        try_err('dashboard', '快照残留自愈降级', _esr)
                _th.Thread(target=_rc_run, daemon=True).start()
        except Exception as _erc2:
            try_err('dashboard', '日销对账调度降级', _erc2)
    except Exception as _e:
            try_err('dashboard', '数据追平降级', _e)
