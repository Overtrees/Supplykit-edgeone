"""日销计算工具 — 三窗口异常剔除 + 趋势加权融合

供 insights.py（补货建议）和 dashboard.py（濒临断货）共用
"""
from datetime import datetime, timedelta, timezone
import os
import logging

logger = logging.getLogger("sales_utils")


def sku_to_channel(sku, db=None):
    """按 SKU 推断渠道（严谨：从 products 主表查，查不到再查 inventory）"""
    if not sku:
        return None
    try:
        if db is None:
            from app.core.database import get_db
            db = get_db()
        # products 表是 SKU 主表，优先
        p = db.table("products").select("channel").eq("sku", sku).execute().data
        if p and p[0].get('channel'):
            return p[0]['channel']
        # 回退查 inventory
        i = db.table("inventory").select("channel").eq("sku", sku).execute().data
        if i and i[0].get('channel'):
            return i[0]['channel']
    except Exception as e:
        logger.warning(f"[sales] sku_to_channel {sku}: {e}")
    return None


def load_daily_sales(cutoff_days, db, sku_barcode_map=None, channel=None, warehouse=None, skus=None):
    """统一数据源：从快照读历史 + 当天 orders 补充，消除重复计算
    
    返回: {key: {date: qty, ...}, ...}  key 为 sku 或 sku|barcode
    skus: 可选 SKU 列表过滤（分页场景只算当前页，避免全量聚合）
    """
    from app.core.database import get_conn
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).strftime('%Y-%m-%d')
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    daily_by_sku = {}
    _sku_set = set(skus) if skus else None
    _sku_filter = (' AND sku IN (%s)' % ','.join(['?'] * len(skus))) if skus else ''
    _sku_params = list(skus) if skus else []
    
    # 1. 快照读历史（原始 SQL 避免 ORM 行转 dict 开销）
    try:
        conn = get_conn()
        if channel and warehouse:
            rows = conn.execute("SELECT date, sku, order_count FROM daily_sales_snapshot WHERE date>=? AND channel=? AND warehouse=?" + _sku_filter, (cutoff, channel, warehouse) + tuple(_sku_params)).fetchall()
        elif channel:
            rows = conn.execute("SELECT date, sku, order_count FROM daily_sales_snapshot WHERE date>=? AND channel=?" + _sku_filter, (cutoff, channel) + tuple(_sku_params)).fetchall()
        else:
            rows = conn.execute("SELECT date, sku, order_count FROM daily_sales_snapshot WHERE date>=?" + _sku_filter, (cutoff,) + tuple(_sku_params)).fetchall()
        for row in rows:
            sku = row[1]  # tuple 索引访问，避免 dict 创建开销
            key = sku
            if sku_barcode_map and sku_barcode_map.get(sku):
                key = f"{sku}|{sku_barcode_map[sku]}"
            # 求和而非覆盖: 同 SKU 多仓同日时, 全渠道口径应累加各仓 order_count(曾只留最后一仓)
            _d = row[0]
            _v = row[2] or 0
            _m = daily_by_sku.setdefault(key, {})
            _m[_d] = _m.get(_d, 0) + _v
    except Exception as e:
        logger.warning(f"[sales] snapshot raw read: {e}")
    
    # 2. 当天 orders 补充（原始 SQL）
    try:
        from app.core.dashboard_cache import _PAID_STATUSES
        q = db.table("orders").select("*")
        if channel: q = q.eq("channel", channel)   # 渠道隔离: 当天订单补足必须按 channel 过滤(曾漏过滤, 跨渠道混入)
        orders = q.gte("ordered_at", today).execute().data or []
        for o in orders:
            # 软删除订单不计入日销（修复：删单后当天日销仍含该单）
            if o.get("deleted_at"):
                continue
            # 统一口径: 只计已支付(待发货/已发货/已完成/申请退款)——当天未付款单不算销量
            if (o.get('order_status') or '') not in _PAID_STATUSES:
                continue
            sku = o.get('sku', '')
            if not sku: continue
            if skus is not None and sku not in _sku_set:
                continue
            key = sku
            if sku_barcode_map and sku_barcode_map.get(sku):
                key = f"{sku}|{sku_barcode_map[sku]}"
            dt = str(o.get('ordered_at', ''))[:10]
            qty = int(o.get('quantity', 0) or 0)
            if dt >= cutoff:
                d = daily_by_sku.setdefault(key, {})
                d[dt] = d.get(dt, 0) + qty
    except Exception as e:
        logger.warning(f"[sales] today orders: {e}")
    
    return daily_by_sku


def load_daily_sales_grouped(cutoff_days, db, sku_barcode_map=None, channel=None, skus=None):
    """按 SKU 与 SKU×仓库 双口径读日销(一次快照查询)——支撑补货模式口径:
      BBCC/全盘 → 按 SKU 合计(跨仓累加); 传统多仓 → 逐仓(SKU×warehouse)独立
    返回: (by_sku, by_sku_wh)  by_sku[key]={date:qty}  by_sku_wh[f"{key}|{wh}"]={date:qty}
    """
    from app.core.database import get_conn
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).strftime('%Y-%m-%d')
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    by_sku = {}
    by_sku_wh = {}
    _sku_set = set(skus) if skus else None
    _sku_filter = (' AND sku IN (%s)' % ','.join(['?'] * len(skus))) if skus else ''
    _sku_params = list(skus) if skus else []
    try:
        conn = get_conn()
        rows = conn.execute("SELECT date, sku, warehouse, order_count FROM daily_sales_snapshot WHERE channel=? AND date>=?" + _sku_filter, [channel, cutoff] + _sku_params).fetchall()
        for date, sku, wh, cnt in rows:
            if _sku_set is not None and sku not in _sku_set:
                continue
            key = str(sku)
            if sku_barcode_map and sku_barcode_map.get(sku):
                key = f"{sku}|{sku_barcode_map[sku]}"
            cnt = cnt or 0
            m = by_sku.setdefault(key, {}); m[date] = m.get(date, 0) + cnt
            wk = f"{key}|{str(wh or '')}"
            w = by_sku_wh.setdefault(wk, {}); w[date] = w.get(date, 0) + cnt
    except Exception as e:
        logger.warning(f"[sales] snapshot grouped read: {e}")
    # 当天 orders 补充(只计已支付, 同 load_daily_sales 口径)
    try:
        from app.core.dashboard_cache import _PAID_STATUSES
        q = db.table("orders").select("*")
        if channel: q = q.eq("channel", channel)   # 渠道隔离
        orders = q.gte("ordered_at", today).execute().data or []
        for o in orders:
            if o.get("deleted_at") or (o.get('order_status') or '') not in _PAID_STATUSES:
                continue
            sku = o.get('sku', '')
            if not sku: continue
            if _sku_set is not None and sku not in _sku_set:
                continue
            key = str(sku)
            if sku_barcode_map and sku_barcode_map.get(sku):
                key = f"{sku}|{sku_barcode_map[sku]}"
            dt = str(o.get('ordered_at', ''))[:10]
            qty = int(o.get('quantity', 0) or 0)
            m = by_sku.setdefault(key, {}); m[dt] = m.get(dt, 0) + qty
            wk = f"{key}|{str(o.get('warehouse', '') or '')}"
            w = by_sku_wh.setdefault(wk, {}); w[dt] = w.get(dt, 0) + qty
    except Exception as e:
        logger.warning(f"[sales] today orders grouped: {e}")
    return by_sku, by_sku_wh


def calc_sales_multi(daily_by_sku, windows=None, sku_barcode_map=None):
    """一次遍历 daily_by_sku 计算多个窗口的日均销量（含 3σ 剔除 + 近3天1.5倍加权）
    
    替代多次 calc_sales_from_daily 调用，减少重复遍历。
    windows: 窗口天数列表，如 [7, 14, 28]
    返回: {7: {key: val}, 14: {key: val}, 28: {key: val}}
    """
    if windows is None:
        windows = [7, 14, 28]
    now = datetime.now(timezone.utc)
    max_win = max(windows)
    all_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max_win)]
    results = {w: {} for w in windows}
    for key, daily in daily_by_sku.items():
        n = len(daily)
        base_vals = [daily.get(d, 0) for d in all_days]
        for win in windows:
            vals = base_vals[:win]
            total = sum(vals)
            base_avg = total / win
            if n < 3 or win < 7:
                results[win][key] = base_avg
                continue
            mean = sum(vals) / win
            var = sum((v - mean) ** 2 for v in vals) / win
            std = var ** 0.5
            threshold = max(3 * std, mean * 1.5)
            weighted_sum = 0
            weight_total = 0
            for idx, v in enumerate(reversed(vals)):
                if abs(v - mean) <= threshold:
                    w = 1.5 if idx >= win - 3 else 1.0
                    weighted_sum += v * w
                    weight_total += w
            results[win][key] = weighted_sum / weight_total if weight_total > 0 else 0
    return results


def calc_sales_from_daily(daily_by_sku, cutoff_days, orders=None, sku_barcode_map=None):
    """从已构建的 daily_by_sku 计算指定窗口的日均销量（含 3σ 剔除 + 近3天1.5倍加权）
    
    daily_by_sku: load_daily_sales 的返回值，或旧版 calc_sales 兼容格式
    cutoff_days: 窗口天数
    orders: 可选，用于补充 0 日销 SKU（兼容旧调用方）
    sku_barcode_map: 可选，用于补 0 日销
    """
    # 预计算日期列表，避免循环内重复调用 datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    all_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(cutoff_days)]
    result = {}
    for key, daily in daily_by_sku.items():
        n = len(daily)
        total = sum(daily.values())
        base_avg = total / cutoff_days
        if n < 3 or cutoff_days < 7:
            result[key] = base_avg
            continue
        vals = [daily.get(d, 0) for d in all_days]
        nd = cutoff_days
        mean = sum(vals) / nd
        var = sum((v - mean) ** 2 for v in vals) / nd
        std = var ** 0.5
        threshold = max(3 * std, mean * 1.5)
        weighted_sum = 0
        weight_total = 0
        for idx, v in enumerate(reversed(vals)):
            if abs(v - mean) <= threshold:
                w = 1.5 if idx >= nd - 3 else 1.0
                weighted_sum += v * w
                weight_total += w
        result[key] = weighted_sum / weight_total if weight_total > 0 else 0

    # 补 0 日销的 SKU
    if orders:
        for o in orders:
            sku = o.get('sku', '')
            if not sku: continue
            key = sku
            if sku_barcode_map and sku_barcode_map.get(sku):
                key = f"{sku}|{sku_barcode_map[sku]}"
            if key not in result:
                result[key] = 0

    if os.getenv('SALES_LOG') and any(v > 0 for v in result.values()):
        nonzero = {k: round(v, 2) for k, v in result.items() if v > 0}
        logger.info(f"[SALES] cutoff={cutoff_days}d → {len(nonzero)} SKU: {nonzero}")
    return result


def calc_sales(orders, cutoff_days, source='', wh_name=None, sku_barcode_map=None, db=None):
    """旧版兼容入口：内部调用 load_daily_sales + calc_sales_from_daily
    
    如果传入了 db，优先使用快照（统一数据源），orders 只用于当天补充。
    如果未传入 db，走旧逻辑（仅从 orders 计算）。
    """
    if db:
        # 统一数据源路径：快照 + 当天 orders
        daily = load_daily_sales(cutoff_days, db, sku_barcode_map=sku_barcode_map)
        # 过滤 source/wh_name（外部调用时已有）
        if source or wh_name:
            filtered = {}
            for o in orders:
                if source and o.get('data_source', '') != source: continue
                if wh_name and o.get('warehouse', '') != wh_name: continue
                sku = o.get('sku', '')
                if not sku: continue
                key = sku
                if sku_barcode_map and sku_barcode_map.get(sku):
                    key = f"{sku}|{sku_barcode_map[sku]}"
                if key not in filtered:
                    filtered[key] = daily.get(key, {})
            daily = filtered
        return calc_sales_from_daily(daily, cutoff_days, orders=orders, sku_barcode_map=sku_barcode_map)
    else:
        # 旧路径：仅从 orders 计算（无 db 时）
        cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).strftime('%Y-%m-%d')
        daily_by_sku = {}
        for o in orders:
            if source and o.get('data_source', '') != source: continue
            if wh_name and o.get('warehouse', '') != wh_name: continue
            sku = o.get('sku', '')
            if not sku: continue
            key = sku
            if sku_barcode_map and sku_barcode_map.get(sku):
                key = f"{sku}|{sku_barcode_map[sku]}"
            dt = str(o.get('ordered_at', ''))[:10]
            qty = int(o.get('quantity', 0) or 0)
            if dt >= cutoff:
                d = daily_by_sku.setdefault(key, {})
                d[dt] = d.get(dt, 0) + qty
        return calc_sales_from_daily(daily_by_sku, cutoff_days, orders=orders, sku_barcode_map=sku_barcode_map)


def adjust_dashboard_for_order(order, sign):
    """删单/恢复时即时调整看板缓存（O(1) 增量，不触发全量重建）

    修复: 删单后看板 10s 异步重建窗口内显示旧值。
    直接修改缓存中的 summary/trend/stores/status_distribution。
    """
    try:
        ch = order.get('channel', 'jd')
        from app.core.dashboard_cache import _cache_by_channel, _PAID_STATUSES
        cached = _cache_by_channel.get(ch)
        if not cached:
            return False
        data = cached['data']
        status = order.get('order_status', '')
        qty = 1
        store = order.get('store', '')
        ordered_at = str(order.get('ordered_at', ''))[:10]
        date_key = ordered_at[5:] if len(ordered_at) >= 10 else ordered_at

        s = data.get('summary', {})
        s['total_orders'] = max(0, (s.get('total_orders', 0) or 0) + sign * qty)
        # GMV 金额 = total - discount + freight + tax(与重建口径一致); 补贴单列(回款拆解)
        _amt = float(order.get('total_amount', 0) or 0) - float(order.get('discount_amount', 0) or 0) \
            + float(order.get('freight_amount', 0) or 0) + float(order.get('tax_amount', 0) or 0)
        _sub = float(order.get('subsidy_amount', 0) or 0)
        # 增量修正必须与重建口径一致: GMV=已支付(待发货/已发货/已完成/申请退款); 净GMV=gmv-退款金额
        if status in _PAID_STATUSES:
            s['gmv'] = max(0, round((s.get('gmv', 0) or 0) + sign * _amt, 2))
            s['subsidy_amount'] = max(0, round((s.get('subsidy_amount', 0) or 0) + sign * _sub, 2))
            if status == '申请退款':
                s['refund_count'] = max(0, (s.get('refund_count', 0) or 0) + sign * qty)
                s['refund_amount'] = max(0, round((s.get('refund_amount', 0) or 0) + sign * _amt, 2))
        elif status == '待发货':
            s['pending_count'] = max(0, (s.get('pending_count', 0) or 0) + sign * qty)
        # 净 GMV 恒 = gmv - 退款金额; 实际回款 = 净GMV - 补贴(删除退款单时 gmv 与 refund_amount 同减, 净GMV不变——正确)
        s['net_gmv'] = max(0, round((s.get('gmv', 0) or 0) - (s.get('refund_amount', 0) or 0), 2))
        s['payout'] = max(0, round((s.get('gmv', 0) or 0) - (s.get('refund_amount', 0) or 0) - (s.get('subsidy_amount', 0) or 0), 2))

        for t in data.get('trend', []):
            if t.get('日期') == date_key:
                if status in _PAID_STATUSES:
                    t['订单数'] = max(0, (t.get('订单数', 0) or 0) + sign * qty)
                    t['GMV'] = max(0, round((t.get('GMV', 0) or 0) + sign * _amt, 2))
                break

        for st in data.get('stores', []):
            if st.get('name') == store:
                if status in _PAID_STATUSES:
                    st['orders'] = max(0, (st.get('orders', 0) or 0) + sign * qty)
                    st['gmv'] = max(0, round((st.get('gmv', 0) or 0) + sign * _amt, 2))
                break

        for sd in data.get('status_distribution', []):
            if sd.get('name') == status:
                sd['value'] = max(0, (sd.get('value', 0) or 0) + sign * qty)
                break
        return True
    except Exception as e:
        import logging
        logging.warning(f"[dash] adjust: {e}")
        return False


def adjust_snapshot_for_order(order, sign):
    """删单(sign=-1)/恢复(sign=+1)时即时调整日销快照对应行 order_count

    修复: 删历史订单后 load_daily_sales 读快照仍含该单(窗口到次日重建)。
    O(1) 单条 UPDATE, 不触发全量重建。
    """
    try:
        _d = str(order.get('ordered_at', ''))[:10]
        _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        _qty = int(order.get('quantity', 0) or 0)
        if _qty <= 0 or not _d:
            return False
        # 统一口径: 快照只含已支付订单——未付款单增删不影响快照(避免与重建不一致)
        from app.core.dashboard_cache import _PAID_STATUSES
        if (order.get('order_status') or '') not in _PAID_STATUSES:
            return False
        # 今天的订单不在快照(走当天 orders 补充), 无需调整
        if _d >= _today:
            return False
        from app.core.database import get_conn
        _c = get_conn()
        _c.execute(
            "UPDATE daily_sales_snapshot SET order_count = MAX(order_count + ?, 0) "
            "WHERE date=? AND channel=? AND sku=? AND warehouse=?",
            (sign * _qty, _d, order.get('channel', 'jd'), order.get('sku', ''),
             order.get('warehouse', '') or '未知'))
        _c.commit()
        return True
    except Exception as e:
        import logging
        logging.warning(f"[sales] adjust snapshot: {e}")
        return False


def build_daily_sales_snapshot(db):
    # 确保表结构正确（首次调用时重建，添加 warehouse 维度）
    try:
        from app.core.database import get_conn
        _c = get_conn()
        _c.execute("DROP TABLE IF EXISTS daily_sales_snapshot")
        _c.execute("CREATE TABLE IF NOT EXISTS daily_sales_snapshot ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "date TEXT NOT NULL, channel TEXT DEFAULT 'jd',"
            "sku TEXT NOT NULL, warehouse TEXT DEFAULT '',"
            "order_count INTEGER DEFAULT 0,"
            "UNIQUE(date, channel, sku, warehouse))")
        _c.commit()
    except Exception as e:
        import logging; logging.warning(f"[sales] recreate snapshot: {e}")

    """构建/更新日销快照表（增量：只处理快照最大日期之后的新订单）"""
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone
    # 快照中已有的最大日期
    try:
        max_row = db.table("daily_sales_snapshot").select("MAX(date) as m").execute().data
        max_date = (max_row[0]['m'] or '') if max_row else ''
    except Exception as e:
        import logging, traceback
        logging.warning(f"[sales] snapshot max date: {e}\n{traceback.format_exc()}")
        max_date = ''
    # 增量窗口：max_date 之后到昨天
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    start = max(cutoff, max_date) if max_date else cutoff
    orders = db.table("orders").select("*").gte("ordered_at", start).execute().data or []
    # 软删除订单不进入快照（修复：删单后日销快照仍含该单销量）
    orders = [o for o in orders if not (o.get("deleted_at") or "")]
    # 统一口径: 只统计已支付订单(待发货/已发货/已完成/申请退款)——未付款(待确认)不算销量,
    # 否则补货日销高估 + 滞销误判(只有未付款单的SKU被当成有销售)
    from app.core.dashboard_cache import _PAID_STATUSES
    orders = [o for o in orders if (o.get('order_status') or '') in _PAID_STATUSES]
    recent = [o for o in orders if str(o.get('ordered_at',''))[:10] < today]
    if not recent:
        return 0
    # 按日期+渠道+SKU+仓库 聚合（支持传统多仓按仓库维度算日销）
    agg = defaultdict(int)
    for o in recent:
        date = str(o.get('ordered_at',''))[:10]
        channel = o.get('channel','jd')
        sku = o.get('sku','')
        if not sku: continue
        warehouse = o.get('warehouse','') or '未知'
        qty = int(o.get('quantity', 0) or 0)
        agg[(date, channel, sku, warehouse)] += qty
    # 批量 UPSERT（分批 commit，避免单事务 16 万行在慢磁盘下 commit 过慢/被杀）
    from app.core.database import get_conn
    conn = get_conn()
    rows = [(d, ch, s, w, q) for (d, ch, s, w), q in agg.items()]
    _batch = 5000
    for i in range(0, len(rows), _batch):
        part = rows[i:i+_batch]
        conn.executemany(
            "INSERT INTO daily_sales_snapshot(date, channel, sku, warehouse, order_count) VALUES(?,?,?,?,?) "
            "ON CONFLICT(date, channel, sku, warehouse) DO UPDATE SET order_count=excluded.order_count",
            part
        )
        conn.commit()
    count = len(rows)
    # 清理超出 100 天的旧快照
    try:
        old_cutoff = (datetime.now(timezone.utc) - timedelta(days=100)).strftime('%Y-%m-%d')
        conn.execute("DELETE FROM daily_sales_snapshot WHERE date < ?", (old_cutoff,))
        conn.commit()
    except Exception as e:
        import logging; logging.warning(f"[sales] snapshot cleanup: {e}")
    return count


def rolling_predict(s7, s14, s28):
    """三窗口滚动预测：按趋势信号分配权重融合"""
    a7 = 1 if s7 > s14 * 1.15 else (-1 if s7 < s14 * 0.85 else 0)
    a14 = 1 if s14 > s28 * 1.15 else (-1 if s14 < s28 * 0.85 else 0)
    weights = {
        (1, 1): (0.50, 0.30, 0.20),   # 持续上行
        (1, 0): (0.35, 0.40, 0.25),   # 刚抬头
        (1, -1): (0.25, 0.35, 0.40),  # 短期冲高回落
        (0, 1): (0.20, 0.40, 0.40),   # 中期走强
        (0, 0): (0.10, 0.20, 0.70),   # 平稳
        (0, -1): (0.15, 0.35, 0.50),  # 中期走弱
        (-1, 1): (0.25, 0.35, 0.40),  # 短期跌中期回升
        (-1, 0): (0.20, 0.30, 0.50),  # 短期走弱
        (-1, -1): (0.40, 0.35, 0.25), # 持续下行
    }
    w7, w14, w28 = weights.get((a7, a14), (0.10, 0.20, 0.70))
    return s7 * w7 + s14 * w14 + s28 * w28