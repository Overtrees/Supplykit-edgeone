"""biz/sales.py —— 日销计算(从旧 backend sales_utils 移植, 原生 SQL 读取)

三窗口滚动预测 + 3σ 异常剔除 + 近 3 天 1.5 倍加权(与旧版口径完全一致)
"""
from datetime import datetime, timedelta, timezone

from db import query
from routes.common import SALES_STATUSES as _SS  # 日销口径: 发货消耗状态(排除申请退款)


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
    # 2. 当天已支付订单补足(口径: 发货消耗状态——待发货/已发货/已完成, 申请退款不消耗库存)
    today_rows = query(
        "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
        "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='')",
        (channel, today + " 00:00:00"))
    for o in today_rows:
        if (o.get("order_status") or "") not in _SS:
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
        if (o.get("order_status") or "") not in _SS:
            continue
        _add(o.get("sku"), o.get("warehouse"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    return by_sku, by_sku_wh


def smooth_promo_spikes(daily, now=None, max_win=28, hist_days=60, peak_ratio=5.0, cap_ratio=2.0):
    """促销尖峰削峰: 近窗口单日销量 vs 窗口外历史基线(28-60 天日均)

    峰值 > peak_ratio×基线 → 截断到 cap_ratio×基线(保留合理量, 不彻底抹掉);
    无历史基线(新品/起量)不削——真实增长不被误杀。
    返回处理后的 daily(供 calc_sales_multi 前调用)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    hist_days_list = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max_win, hist_days)]
    hist_nz = [daily.get(d, 0) for d in hist_days_list if daily.get(d, 0) > 0]
    if not hist_nz:
        return daily  # 无历史基线, 无法判别促销
    baseline = sum(hist_nz) / len(hist_nz)
    if baseline <= 0:
        return daily
    out = dict(daily)
    win_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max_win)]
    for d in win_days:
        v = out.get(d, 0)
        if v > baseline * peak_ratio:
            out[d] = round(baseline * cap_ratio, 2)
    return out


def calc_sales_multi(daily_by_sku, windows=None, sparse="plain"):
    """一次遍历计算多窗口日均: 3σ 异常剔除(非零日统计) + 近3天1.5倍加权 + 统一摊薄语义

    sparse 模式(窗口内非零日 < 3, 3σ 会把孤立销售日当离群清零):
      - plain: 直接窗口日均(采购/看板求稳平滑, 与旧版一致)
      - shrink: 按证据收缩 ×(nnz/3)(补货保守——不虚高也不误杀真实需求)
    """
    if windows is None:
        windows = [7, 14, 28]
    now = datetime.now(timezone.utc)
    max_win = max(windows)
    all_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max_win)]
    results = {w: {} for w in windows}
    for key, daily in daily_by_sku.items():
        base_vals = [daily.get(d, 0) for d in all_days]
        for win in windows:
            vals = base_vals[:win]
            total = sum(vals)
            if total <= 0:
                results[win][key] = 0.0
                continue
            nz = [v for v in vals if v > 0]
            nnz = len(nz)
            base_avg = total / win
            if nnz < 3:
                # 稀疏: 不武断二选一(剔除=误杀真实需求 / 全额日均=促销虚高), 按证据强度收缩
                results[win][key] = base_avg if sparse == "plain" else base_avg * (nnz / 3.0)
                continue
            # 稳定序列: 3σ 按非零日统计(0 日不拉大 σ → 稀疏窗口不被误杀), 近3天 1.5 倍加权
            nz_mean = sum(nz) / nnz
            var = sum((v - nz_mean) ** 2 for v in nz) / nnz
            std = var ** 0.5
            threshold = max(3 * std, nz_mean * 1.5)
            ws = 0.0
            for idx, v in enumerate(reversed(vals)):
                if v > 0 and abs(v - nz_mean) <= threshold:
                    w = 1.5 if idx >= win - 3 else 1.0
                    ws += v * w
            # 统一摊薄语义: 窗口日均(剔除离群日计 0) —— 与 base_avg 同口径
            results[win][key] = ws / win
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
