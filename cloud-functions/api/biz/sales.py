"""biz/sales.py —— 日销计算(从旧 backend sales_utils 移植, 原生 SQL 读取)

三窗口滚动预测 + 3σ 异常剔除 + 近 3 天 1.5 倍加权(与旧版口径完全一致)
"""
from datetime import datetime, timedelta, timezone

from db import query


def load_daily_sales(cutoff_days, channel, skus=None):
    """统一数据源: 快照历史 + 当天已支付订单补足(渠道隔离)

    返回 {sku: {date: qty}}
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    daily = {}

    def _add(sku, d, qty):
        if skus is not None and sku not in skus:
            return
        m = daily.setdefault(sku, {})
        m[d] = m.get(d, 0) + qty

    # 1. 快照历史
    rows = query(
        "SELECT date, sku, order_count FROM daily_sales_snapshot "
        "WHERE channel=%s AND date>=%s", (channel, cutoff))
    for r in rows:
        _add(str(r.get("sku") or ""), str(r.get("date") or "")[:10], int(r.get("order_count") or 0))
    # 2. 当天已支付订单补足(口径: 待发货/已发货/已完成/申请退款)
    today_rows = query(
        "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
        "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='')",
        (channel, today + " 00:00:00"))
    for o in today_rows:
        if (o.get("order_status") or "") not in ("待发货", "已发货", "已完成", "申请退款"):
            continue
        _add(str(o.get("sku") or ""), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    return daily


def load_daily_sales_grouped(cutoff_days, channel, skus=None):
    """快照按 SKU×仓 双口径: 返回 (by_sku, by_sku_wh)"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    by_sku = {}
    by_sku_wh = {}

    def _add(key, wh, d, qty):
        if skus is not None and str(key) not in skus:
            return
        m = by_sku.setdefault(str(key), {})
        m[d] = m.get(d, 0) + qty
        w = by_sku_wh.setdefault("%s|%s" % (key, wh or ""), {})
        w[d] = w.get(d, 0) + qty

    rows = query(
        "SELECT date, sku, warehouse, order_count FROM daily_sales_snapshot "
        "WHERE channel=%s AND date>=%s", (channel, cutoff))
    for r in rows:
        _add(r.get("sku"), r.get("warehouse"), str(r.get("date") or "")[:10], int(r.get("order_count") or 0))
    today_rows = query(
        "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
        "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='')",
        (channel, today + " 00:00:00"))
    for o in today_rows:
        if (o.get("order_status") or "") not in ("待发货", "已发货", "已完成", "申请退款"):
            continue
        _add(o.get("sku"), o.get("warehouse"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    return by_sku, by_sku_wh


def calc_sales_multi(daily_by_sku, windows=None):
    """一次遍历计算多窗口日均(3σ 剔除 + 近3天1.5倍加权)——与旧版一致"""
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
            ws = 0.0
            wt = 0
            for idx, v in enumerate(reversed(vals)):
                if abs(v - mean) <= threshold:
                    w = 1.5 if idx >= win - 3 else 1.0
                    ws += v * w
                    wt += w
            results[win][key] = ws / wt if wt > 0 else 0
    return results


def rolling_predict(s7, s14, s28):
    """三窗口趋势加权融合——与旧版一致"""
    a7 = 1 if s7 > s14 * 1.15 else (-1 if s7 < s14 * 0.85 else 0)
    a14 = 1 if s14 > s28 * 1.15 else (-1 if s14 < s28 * 0.85 else 0)
    weights = {
        (1, 1): (0.50, 0.30, 0.20), (1, 0): (0.35, 0.40, 0.25), (1, -1): (0.25, 0.35, 0.40),
        (0, 1): (0.20, 0.40, 0.40), (0, 0): (0.10, 0.20, 0.70), (0, -1): (0.15, 0.35, 0.50),
        (-1, 1): (0.25, 0.35, 0.40), (-1, 0): (0.20, 0.30, 0.50), (-1, -1): (0.40, 0.35, 0.25),
    }
    w7, w14, w28 = weights.get((a7, a14), (0.10, 0.20, 0.70))
    return s7 * w7 + s14 * w14 + s28 * w28
