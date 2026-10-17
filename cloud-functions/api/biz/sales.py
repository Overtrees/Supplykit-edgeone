"""biz/sales.py —— 日销计算(从旧 backend sales_utils 移植, 原生 SQL 读取)

三窗口滚动预测 + 3σ 异常剔除 + 近 3 天 1.5 倍加权(与旧版口径完全一致)
"""
from datetime import datetime, timedelta, timezone

from db import query
from routes.common import SALES_STATUSES as _SS  # 日销口径: 发货消耗状态(排除申请退款)


def _sku_in_clause(skus, batch=500, max_sql=2000):
    """skus 集合 → SQL 下推分片列表; 返回 None = 走全量拉 + Python 过滤(大集合防往返爆炸)

    2026-10-17 部署实证: stock_risk 传 5.2 万 skus 全量下推 → 105 片×2 = 210 次 SQL 往返 → 25.6s(比原来更慢);
    阈值分流: 集合 ≤2000 → IN 分片下推(往返 ≤8 次, 快); >2000 → 一次全量拉 + Python 过滤(1~2 次往返)——
    同集过滤语义不变, 只是执行路径按集合大小选最优
    """
    if not skus:
        return []
    _list = sorted({str(s) for s in skus if s})
    if len(_list) > max_sql:
        return None  # 大集合: 走 Python 过滤(1 次全量拉, 避免 N/500×2 次往返)
    out = []
    for i in range(0, len(_list), batch):
        chunk = _list[i:i + batch]
        out.append((",".join(["%s"] * len(chunk)), chunk))
    return out


def load_daily_sales(cutoff_days, channel, skus=None):
    """统一数据源: 快照历史 + 当天已支付订单补足(渠道隔离)

    返回 {sku: {date: qty}}
    2026-10-17 等价重构: skus 过滤从 Python 全量拉→丢 下推 SQL `sku IN (500/片)`——
    5 万 SKU/百万订单时快照几十万行不再进 Python(进销存 7s→毫秒级); 语义等同(同集过滤)
    大集合(>2000)回退 Python 过滤(210 次往返反慢——部署实证 25.6s)
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    daily = {}
    _in_chunks = _sku_in_clause(skus)
    _py_filter = _in_chunks is None  # 大集合 → 全量拉 + Python 过滤(1~2 次往返)
    _skuset = {str(s) for s in (skus or [])} if _py_filter else None

    def _add(sku, d, qty):
        if _py_filter and str(sku) not in _skuset:
            return
        m = daily.setdefault(str(sku), {})
        m[d] = m.get(d, 0) + qty

    # 1. 快照历史(SQL 层 sku 过滤——分片, 等价于原 Python 过滤)
    if _in_chunks:
        for _ph, _chunk in _in_chunks:
            rows = query(
                "SELECT date, sku, order_count FROM daily_sales_snapshot USE INDEX(idx_snapshot_ch_date_full) "
                "WHERE channel=%s AND date>=%s AND sku IN (%s)" % ("%s", "%s", _ph),
                [channel, cutoff] + _chunk)
            for r in rows:
                _add(r.get("sku"), str(r.get("date") or "")[:10], int(r.get("order_count") or 0))
    else:
        rows = query(
            "SELECT date, sku, order_count FROM daily_sales_snapshot USE INDEX(idx_snapshot_ch_date_full) "
            "WHERE channel=%s AND date>=%s", (channel, cutoff))
        for r in rows:
            _add(r.get("sku"), str(r.get("date") or "")[:10], int(r.get("order_count") or 0))
    # 2. 当天已支付订单补足(口径: 发货消耗状态——待发货/已发货/已完成, 申请退款不消耗库存)
    if _in_chunks:
        for _ph, _chunk in _in_chunks:
            today_rows = query(
                "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
                "WHERE channel=%s AND ordered_at>=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='')"
                % ("%s", "%s", _ph),
                [channel, today + " 00:00:00"] + _chunk)
            for o in today_rows:
                if (o.get("order_status") or "") not in _SS:
                    continue
                _add(o.get("sku"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    else:
        today_rows = query(
            "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
            "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='')",
            (channel, today + " 00:00:00"))
        for o in today_rows:
            if (o.get("order_status") or "") not in _SS:
                continue
            _add(o.get("sku"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    return daily


def load_daily_sales_grouped(cutoff_days, channel, skus=None):
    """快照按 SKU×仓 双口径: 返回 (by_sku, by_sku_wh)
    2026-10-17 等价重构: skus 过滤下推 SQL(同 load_daily_sales)——同集过滤, 不拉全量;
    大集合(>2000)回退 Python 过滤(210 次往返反慢——部署实证 stock_risk 25.6s)"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    by_sku = {}
    by_sku_wh = {}
    _in_chunks = _sku_in_clause(skus)
    _py_filter = _in_chunks is None
    _skuset = {str(s) for s in (skus or [])} if _py_filter else None

    def _add(key, wh, d, qty):
        if _py_filter and str(key) not in _skuset:
            return
        m = by_sku.setdefault(str(key), {})
        m[d] = m.get(d, 0) + qty
        w = by_sku_wh.setdefault("%s|%s" % (key, wh or ""), {})
        w[d] = w.get(d, 0) + qty

    if _in_chunks:
        for _ph, _chunk in _in_chunks:
            rows = query(
                "SELECT date, sku, warehouse, order_count FROM daily_sales_snapshot USE INDEX(idx_snapshot_ch_date_full) "
                "WHERE channel=%s AND date>=%s AND sku IN (%s)" % ("%s", "%s", _ph),
                [channel, cutoff] + _chunk)
            for r in rows:
                _add(r.get("sku"), r.get("warehouse"), str(r.get("date") or "")[:10], int(r.get("order_count") or 0))
    else:
        rows = query(
            "SELECT date, sku, warehouse, order_count FROM daily_sales_snapshot USE INDEX(idx_snapshot_ch_date_full) "
            "WHERE channel=%s AND date>=%s", (channel, cutoff))
        for r in rows:
            _add(r.get("sku"), r.get("warehouse"), str(r.get("date") or "")[:10], int(r.get("order_count") or 0))
    if _in_chunks:
        for _ph, _chunk in _in_chunks:
            today_rows = query(
                "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
                "WHERE channel=%s AND ordered_at>=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='')"
                % ("%s", "%s", _ph),
                [channel, today + " 00:00:00"] + _chunk)
            for o in today_rows:
                if (o.get("order_status") or "") not in _SS:
                    continue
                _add(o.get("sku"), o.get("warehouse"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    else:
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
            _v0 = _row["value"]
            if _v0 == "__CALC__":
                # 并发共享: 别人在重算(表级 __CALC__ 标记) → 轮询等待结果(2s 内共享, 超时自己抢)
                for _i in range(40):
                    _t.sleep(0.05)
                    _row = _one("SELECT value FROM analysis_cache WHERE `key`=%s", [_tk])
                    _v0 = _row.get("value") if _row else None
                    if _v0 and _v0 != "__CALC__":
                        break
                if _v0 and _v0 != "__CALC__":
                    try:
                        _pl = _json.loads(_v0)
                        if _pl.get("fused") is not None:
                            _f = {str(k): float(v) for k, v in _pl["fused"].items()}
                            _sg = {str(k): float(v) for k, v in (_pl.get("sigma") or {}).items()}
                            _SALES_DIGEST_CACHE[_key] = (_now, ({}, _f, _sg))
                            return {}, _f, _sg
                    except Exception:
                        pass
            else:
                _pl = _json.loads(_v0)
                _age = (_now - _t.mktime(_t.strptime(str(_row.get("created_at") or "")[:19],
                                                    "%Y-%m-%d %H:%M:%S"))) if _row.get("created_at") else 999
                if _age <= 300 and _pl.get("fused") is not None:
                    _f = {str(k): float(v) for k, v in _pl["fused"].items()}
                    _sg = {str(k): float(v) for k, v in (_pl.get("sigma") or {}).items()}
                    _SALES_DIGEST_CACHE[_key] = (_now, ({}, _f, _sg))
                    return {}, _f, _sg
    except Exception:
        pass
    # 并发合并(表级 __CALC__——summary/risk 冷算同时 miss digest 时共享一次重算, 非竞争×2)
    try:
        from db import execute as _em
        _em("INSERT INTO analysis_cache(`key`, value, created_at) VALUES(%s,'__CALC__',NOW(6)) "
            "ON DUPLICATE KEY UPDATE value='__CALC__', "
            "created_at=IF(value='__CALC__', created_at, VALUES(created_at))",
            ["sales_digest|%s|%s" % (channel, days)])
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
    # 写表缓存(跨实例共享; 数据量小 fused+sigma 970 SKU 数值; 覆盖 __CALC__ 标记为结果)
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


# ── 2026-10-17 日销三窗口物化表(根治冷算 22s; 不改变输入集合/四维语义) ──────────────
# 物化只存"慢变的日销派生"(快照+订单派生, 消费方共享); 库存/当天直查永不物化——
# 动态安全线实时性由库存直查保证, 与现状语义完全一致(物化表=订单聚合的预计算)
# 补货特有口径(削峰+shrink)用 *_cut 列隔离, 不吞并 digest/看板/规则的 plain 口径

_SKU_STATS_DDL = (
    "CREATE TABLE IF NOT EXISTS sku_sales_stats ("
    "channel VARCHAR(20) NOT NULL, sku VARCHAR(64) NOT NULL, warehouse VARCHAR(64) DEFAULT '', "
    "ds7 DECIMAL(12,3) DEFAULT 0, ds14 DECIMAL(12,3) DEFAULT 0, ds28 DECIMAL(12,3) DEFAULT 0, "
    "fused DECIMAL(12,3) DEFAULT 0, sigma DECIMAL(12,3) DEFAULT 0, "
    "ds7_cut DECIMAL(12,3) DEFAULT 0, ds14_cut DECIMAL(12,3) DEFAULT 0, ds28_cut DECIMAL(12,3) DEFAULT 0, "
    "fused_cut DECIMAL(12,3) DEFAULT 0, "
    "nnz7 INT DEFAULT 0, nnz14 INT DEFAULT 0, nnz28 INT DEFAULT 0, "
    "last_sale DATE, updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), "
    "PRIMARY KEY(channel, sku, warehouse))"
)


def _rebuild_sku_sales_stats(channel, days=28, skus=None):
    """物化表重建(含当天——订单写后 invalidate 触发; 快照+当天订单同 load_daily_sales_grouped)

    - 行粒度: warehouse='__C__'=全国 C 仓合计(bbcc 用); 具体仓名=逐仓(traditional 用)
    - plain = calc_sales_multi(默认) —— digest/看板/规则/进销存消费
    - cut   = 削峰(smooth_promo_spikes)+ sparse=shrink —— 补货专属(与 _build_repl 现口径一致)
    - upsert 幂等 ON DUPLICATE; 失败降级(不阻塞; 消费方读表 miss 时降级直算)
    """
    from db import execute as _ex, executemany as _em, query as _q
    try:
        _ex(_SKU_STATS_DDL)
        by_sku, by_sku_wh = load_daily_sales_grouped(days, channel, skus=skus)
        if not by_sku and not by_sku_wh:
            return 0
        now_d = datetime.now(timezone.utc)
        # C 仓名集合(与补货 bbcc daily_c 同口径: inventory warehouse_type='platform')
        try:
            _c_whs = {str(r.get("warehouse") or "") for r in _q(
                "SELECT DISTINCT warehouse FROM inventory WHERE channel=%s "
                "AND warehouse_type='platform' AND warehouse!=''", [channel])}
        except Exception:
            _c_whs = set()
        # 全国 C 仓合计(逐仓行按 C 仓过滤聚合——与 _build_repl daily_c 等价)
        by_sku_c = {}
        if _c_whs:
            for wk, wd in by_sku_wh.items():
                base, wh = wk.rsplit("|", 1) if "|" in wk else (wk, "")
                if wh in _c_whs:
                    m = by_sku_c.setdefault(base, {})
                    for d, q in wd.items():
                        m[d] = m.get(d, 0) + q
        elif by_sku:
            by_sku_c = by_sku  # 无仓型数据兜底: 全国合计量

        def _nnz(daily, win):
            n = 0
            for k, v in daily.items():
                try:
                    if v > 0 and (now_d - datetime.strptime(str(k)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days < win:
                        n += 1
                except Exception:
                    continue
            return n

        def _to_rows(key_daily, is_wh, wh_label=""):
            """批量三窗口(plain) → 行列表; cut 组单独批量(削峰+shrink)"""
            rows_out = []
            if not key_daily:
                return rows_out
            m = calc_sales_multi(key_daily, windows=[7, 14, 28])
            _cut = {k: smooth_promo_spikes(d) for k, d in key_daily.items()}
            mc = calc_sales_multi(_cut, windows=[7, 14, 28], sparse="shrink")
            for k, daily in key_daily.items():
                ds7 = m[7].get(k, 0.0); ds14 = m[14].get(k, 0.0); ds28 = m[28].get(k, 0.0)
                c7 = mc[7].get(k, 0.0); c14 = mc[14].get(k, 0.0); c28 = mc[28].get(k, 0.0)
                vals = list(daily.values())
                sigma = 0.0
                if len(vals) >= 7:
                    mm = sum(vals) / len(vals)
                    sigma = (sum((x - mm) ** 2 for x in vals) / len(vals)) ** 0.5
                _sku = str(k).split("|")[0]
                _wh = (str(k).split("|")[1] if is_wh else "") or wh_label
                rows_out.append({
                    "channel": channel, "sku": _sku, "warehouse": _wh,
                    "ds7": round(ds7, 3), "ds14": round(ds14, 3), "ds28": round(ds28, 3),
                    "fused": round(rolling_predict(ds7, ds14, ds28), 3), "sigma": round(sigma, 3),
                    "ds7_cut": round(c7, 3), "ds14_cut": round(c14, 3), "ds28_cut": round(c28, 3),
                    "fused_cut": round(rolling_predict(c7, c14, c28), 3),
                    "nnz7": _nnz(daily, 7), "nnz14": _nnz(daily, 14), "nnz28": _nnz(daily, 28),
                    "last_sale": max(daily.keys()) if daily else None})
            return rows_out

        rows = (_to_rows(by_sku_c, False, "__C__")
                + _to_rows(by_sku_wh, True))  # __C__ 全国 C 仓 + 逐仓行
        if rows:
            cols = ["channel", "sku", "warehouse", "ds7", "ds14", "ds28", "fused", "sigma",
                    "ds7_cut", "ds14_cut", "ds28_cut", "fused_cut", "nnz7", "nnz14", "nnz28", "last_sale"]
            for i in range(0, len(rows), 500):
                chunk = rows[i:i + 500]
                _em("INSERT INTO sku_sales_stats(%s) VALUES(%s) ON DUPLICATE KEY UPDATE "
                    "ds7=VALUES(ds7), ds14=VALUES(ds14), ds28=VALUES(ds28), fused=VALUES(fused), "
                    "sigma=VALUES(sigma), ds7_cut=VALUES(ds7_cut), ds14_cut=VALUES(ds14_cut), "
                    "ds28_cut=VALUES(ds28_cut), fused_cut=VALUES(fused_cut), "
                    "nnz7=VALUES(nnz7), nnz14=VALUES(nnz14), nnz28=VALUES(nnz28), "
                    "last_sale=VALUES(last_sale), updated_at=CURRENT_TIMESTAMP(6)" % (
                        ", ".join("`%s`" % c for c in cols),
                        ", ".join(["%s"] * len(cols))),
                    [tuple(r[c] for c in cols) for r in chunk])
        return len(rows)
    except Exception as _e:
        from routes.common import try_err
        try_err('sales', 'sku_sales_stats 重建降级', _e)
        return 0


def get_sku_sales_stats(channel, skus=None, warehouse=None, days=28):
    """读写一体的物化读取(消费方入口):
    - 表有数据 → 按 sku/warehouse 查行返回 {sku|sku|wh: (fused, fused_cut, ds7..28, sigma, nnz*, last_sale)}
    - 表空(首启/重建失败)→ 降级直算(与旧逻辑同一函数, 结果等价)
    - 快照未含当天时, 当天订单直查补足——由 _rebuild 内部 load_daily_sales_grouped 保证(含当天)
    """
    try:
        from db import query as _q, one as _o
        _o(_SKU_STATS_DDL)
        _c = _o("SELECT COUNT(*) c FROM sku_sales_stats WHERE channel=%s", [channel]) or {}
        if int(_c.get("c") or 0) <= 0:
            _rebuild_sku_sales_stats(channel, days)
        where = "channel=%s"
        params = [channel]
        if skus:
            _ph = ",".join(["%s"] * min(len(list(skus)), 500))
            if _ph:
                where += " AND sku IN (" + _ph + ")"
                params += list(skus)[:500]
        if warehouse:
            where += " AND warehouse=%s" if warehouse != "any" else " AND warehouse!=''"
            if warehouse != "any":
                params += [warehouse]
        rows = _q("SELECT sku, warehouse, ds7, ds14, ds28, fused, sigma, "
                  "ds7_cut, ds14_cut, ds28_cut, fused_cut, nnz7, nnz14, nnz28, last_sale "
                  "FROM sku_sales_stats WHERE %s" % where, params)
        out = {}
        for r in rows:
            wk = str(r.get("sku") or "")
            if r.get("warehouse"):
                wk += "|" + str(r.get("warehouse"))
            out[wk] = {"sku": r.get("sku"), "warehouse": r.get("warehouse") or "",
                       "ds7": float(r.get("ds7") or 0), "ds14": float(r.get("ds14") or 0),
                       "ds28": float(r.get("ds28") or 0), "fused": float(r.get("fused") or 0),
                       "sigma": float(r.get("sigma") or 0),
                       "ds7_cut": float(r.get("ds7_cut") or 0), "ds14_cut": float(r.get("ds14_cut") or 0),
                       "ds28_cut": float(r.get("ds28_cut") or 0), "fused_cut": float(r.get("fused_cut") or 0),
                       "nnz7": int(r.get("nnz7") or 0), "nnz14": int(r.get("nnz14") or 0),
                       "nnz28": int(r.get("nnz28") or 0), "last_sale": str(r.get("last_sale") or "")[:10]}
        return out
    except Exception as _e:
        from routes.common import try_err
        try_err('sales', 'sku_sales_stats 读取降级', _e)
        return {}
