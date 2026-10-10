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


_SALES_DIGEST_CACHE = {}
_SALES_DIGEST_TTL = 30


def get_sales_digest(channel, days=28):
    """三窗口日销汇总(进程内 30s 缓存): (by_sku, fused_map, sigma_map)

    看板健康指数/低库存卡/规则引擎共享——避免每请求重复查快照+计算;
    与看板 summary/aux 缓存同频(30s/60s), 实时性不劣化; 数据变更后最迟 30s 反映
    """
    import time as _t
    import json as _json
    _now = _t.time()
    _key = (channel, days)
    _hit = _SALES_DIGEST_CACHE.get(_key)
    if _hit and _now - _hit[0] < _SALES_DIGEST_TTL:
        return _hit[1]
    # 表缓存(跨实例共享——首屏新实例命中, 60s; invalidate_all 数据变更清表 → 重算)
    try:
        from db import one as _one
        _tk = "sales_digest|%s|%s" % (channel, days)
        _row = _one("SELECT value, created_at FROM analysis_cache WHERE `key`=%s", [_tk])
        if _row and _row.get("value"):
            _pl = _json.loads(_row["value"])
            _age = (_now - _t.mktime(_t.strptime(str(_row.get("created_at") or "")[:19],
                                                "%Y-%m-%d %H:%M:%S"))) if _row.get("created_at") else 999
            if _age <= 60 and _pl.get("fused") is not None:
                _f = {str(k): float(v) for k, v in _pl["fused"].items()}
                _sg = {str(k): float(v) for k, v in (_pl.get("sigma") or {}).items()}
                _SALES_DIGEST_CACHE[_key] = (_now, ({}, _f, _sg))
                return {}, _f, _sg
    except Exception:
        pass
    by_sku, _ = load_daily_sales_grouped(days, channel)
    _m = calc_sales_multi(by_sku, windows=[7, 14, 28])
    fused = {s: rolling_predict(_m[7].get(s, 0), _m[14].get(s, 0), _m[28].get(s, 0)) for s in by_sku}
    sigma = {}
    for _s, _d in by_sku.items():
        if len(_d) >= 7:
            _vl = list(_d.values())
            _mm = sum(_vl) / len(_vl)
            _vv = sum((x - _mm) ** 2 for x in _vl) / len(_vl)
            sigma[_s] = _vv ** 0.5
    # 写表缓存(跨实例共享; 数据量小 fused+sigma 970 SKU 数值)
    try:
        from db import execute as _exec
        _exec("INSERT INTO analysis_cache(`key`, value) VALUES(%s,%s) "
              "ON DUPLICATE KEY UPDATE value=VALUES(value)",
              ["sales_digest|%s|%s" % (channel, days),
               _json.dumps({"fused": fused, "sigma": sigma})])
    except Exception:
        pass
    _SALES_DIGEST_CACHE[_key] = (_now, (by_sku, fused, sigma))
    return by_sku, fused, sigma


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
