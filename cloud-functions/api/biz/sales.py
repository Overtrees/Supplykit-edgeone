"""biz/sales.py —— 日销计算(从旧 backend sales_utils 移植, 原生 SQL 读取)

三窗口滚动预测 + 3σ 异常剔除 + 近 3 天 1.5 倍加权(与旧版口径完全一致)
"""
from datetime import datetime, timedelta, timezone

from db import query
from schema_create import table_ddl as _table_ddl
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
                "SELECT sku, COALESCE(barcode,'') AS bc, warehouse, quantity, ordered_at, order_status FROM orders "
                "WHERE channel=%s AND ordered_at>=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='')"
                % ("%s", "%s", _ph),
                [channel, today + " 00:00:00"] + _chunk)
            for o in today_rows:
                if (o.get("order_status") or "") not in _SS:
                    continue
                _add(o.get("sku"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    else:
        today_rows = query(
            "SELECT sku, COALESCE(barcode,'') AS bc, warehouse, quantity, ordered_at, order_status FROM orders "
            "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='')",
            (channel, today + " 00:00:00"))
        for o in today_rows:
            if (o.get("order_status") or "") not in _SS:
                continue
            _add(o.get("sku"), str(o.get("ordered_at") or "")[:10], int(o.get("quantity") or 0))
    return daily


def load_daily_sales_grouped(cutoff_days, channel, skus=None, with_bc_wh=False):
    """快照按 SKU×仓 双口径: 返回 (by_sku, by_sku_wh, by_sku_bc)
    with_bc_wh=True → 额外返回 by_sku_wh_bc(按码×仓, 供物化生成 __C__/__BC__ 的按码行)
    2026-10-17 等价重构: skus 过滤下推 SQL(同 load_daily_sales)——同集过滤, 不拉全量;
    大集合(>2000)回退 Python 过滤(210 次往返反慢——部署实证 stock_risk 25.6s)"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    by_sku = {}
    by_sku_wh = {}
    by_sku_bc_wh = {}  # 2026-10-29 按 69码: "sku|barcode" → {date: qty}(跨仓合计)
    by_sku_wh_bc = {}  # 2026-10-29 按码×仓: "sku|barcode|warehouse" → {date: qty}
    _in_chunks = _sku_in_clause(skus)
    _py_filter = _in_chunks is None
    _skuset = {str(s) for s in (skus or [])} if _py_filter else None

    def _add(key, wh, d, qty, bc=""):
        if _py_filter and str(key) not in _skuset:
            return
        m = by_sku.setdefault(str(key), {})
        m[d] = m.get(d, 0) + qty
        w = by_sku_wh.setdefault("%s|%s" % (key, wh or ""), {})
        w[d] = w.get(d, 0) + qty
        # 2026-10-29 按 69码 聚合(空码归入 "" 组 —— 与库存/批次同规则: 空码降级)
        if bc:
            b = by_sku_bc_wh.setdefault("%s|%s" % (key, bc), {})
            b[d] = b.get(d, 0) + qty
            wb = by_sku_wh_bc.setdefault("%s|%s|%s" % (key, bc, wh or ""), {})
            wb[d] = wb.get(d, 0) + qty

    if _in_chunks:
        for _ph, _chunk in _in_chunks:
            rows = query(
                "SELECT date, sku, COALESCE(barcode,'') AS bc, warehouse, order_count "
                "FROM daily_sales_snapshot USE INDEX(idx_snapshot_ch_date_full) "
                "WHERE channel=%s AND date>=%s AND sku IN (%s)" % ("%s", "%s", _ph),
                [channel, cutoff] + _chunk)
            for r in rows:
                _add(r.get("sku"), r.get("warehouse"), str(r.get("date") or "")[:10],
                     int(r.get("order_count") or 0), str(r.get("bc") or ""))
    else:
        rows = query(
            "SELECT date, sku, COALESCE(barcode,'') AS bc, warehouse, order_count "
            "FROM daily_sales_snapshot USE INDEX(idx_snapshot_ch_date_full) "
            "WHERE channel=%s AND date>=%s", (channel, cutoff))
        for r in rows:
            _add(r.get("sku"), r.get("warehouse"), str(r.get("date") or "")[:10],
                 int(r.get("order_count") or 0), str(r.get("bc") or ""))
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
                _add(o.get("sku"), o.get("warehouse"), str(o.get("ordered_at") or "")[:10],
                     int(o.get("quantity") or 0), str(o.get("bc") or ""))
    else:
        today_rows = query(
            "SELECT sku, warehouse, quantity, ordered_at, order_status FROM orders "
            "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='')",
            (channel, today + " 00:00:00"))
        for o in today_rows:
            if (o.get("order_status") or "") not in _SS:
                continue
            _add(o.get("sku"), o.get("warehouse"), str(o.get("ordered_at") or "")[:10],
                 int(o.get("quantity") or 0), str(o.get("bc") or ""))
    if with_bc_wh:
        return by_sku, by_sku_wh, by_sku_bc_wh, by_sku_wh_bc
    return by_sku, by_sku_wh, by_sku_bc_wh


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
    # 2026-10-17 物化表优先(根治冷算; 表空/未重建 → 降级原直算): 物化行只存三窗口底料,
    # fused/sigma 现场算(口径与旧 get_sales_digest 完全一致——rolling_predict(plain))
    try:
        from biz.sales import get_sku_sales_stats, WH_ALL
        _rows = get_sku_sales_stats(channel, wh=WH_ALL, days=days)
        if _rows:
            fused = {}
            sigma = {}
            for _k, _r in _rows.items():
                _sku = _r.get("sku") or ""
                fused[_sku] = rolling_predict(float(_r.get("ds7") or 0),
                                              float(_r.get("ds14") or 0),
                                              float(_r.get("ds28") or 0))
                if abs(float(_r.get("sigma") or 0)) > 0:
                    sigma[_sku] = float(_r.get("sigma") or 0)
            # 写表缓存(跨实例共享; 数据量小 fused+sigma 970 SKU 数值; 覆盖 __CALC__ 标记为结果)
            try:
                from db import execute as _exec
                _exec("INSERT INTO analysis_cache(`key`, value) VALUES(%s,%s) "
                      "ON DUPLICATE KEY UPDATE value=VALUES(value)",
                      ["sales_digest|%s|%s" % (channel, days),
                       _json.dumps({"fused": fused, "sigma": sigma})])
            except Exception:
                pass
            _SALES_DIGEST_CACHE[_key] = (_now, ({}, fused, sigma))
            return {}, fused, sigma
    except Exception as _e:
        from routes.common import try_err
        try_err('sales', 'digest 物化读取降级(走直算)', _e)
    by_sku, _, _ = load_daily_sales_grouped(days, channel)
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
# 物化只存"慢变日销底料"(快照+订单派生的三窗口日均/σ/nnz); 库存/当天订单直查永不物化——
# 动态安全线实时性由库存直查保证; 当天订单读时直查补足(物化行覆盖到快照, 当天增量现场合并)
# 关键: 不固化 fused(rolling_predict)列——采购用 14/28 自权重、看板/断货用 rolling_predict、
#      补货用削峰+shrink 组合, 各消费方口径不同, 现场 O(1) 计算(三窗口查表+乘加)
# 行维度显式枚举(无 any 哨兵): '__C__'=全国platform合计 / '__BC__'=platform+platform_b 合计
#     / '__ALL__'=全仓合计 / 具体仓名=逐仓(own 仓销量近零, 由消费方按 inventory.warehouse_type 现场筛)

# 2026-10-29: 改为引用 schema_create 单一真源(原为独立副本 → 主键/列与 schema 漂移)
_SKU_STATS_DDL = _table_ddl("sku_sales_stats")

# 行维度白名单(防魔法值): 物化行 warehouse 取值枚举
WH_C = "__C__"      # 全国 platform 合计(bbcc 补货/断货 C)
WH_BC = "__BC__"    # platform+platform_b 合计(bbcc 断货 BC)
WH_ALL = "__ALL__"  # 全仓合计(展示/采购兜底)


def _rebuild_sku_sales_stats(channel, days=90, skus=None):
    """物化表重建(含当天——写操作后限频触发; 快照+当天订单同 load_daily_sales_grouped)

    - 行粒度: __C__/__BC__/__ALL__ 全国聚合三行 + 具体仓名逐仓行
    - plain = calc_sales_multi(默认三窗口) —— 看板/规则/进销存/断货消费
    - cut   = 削峰(smooth_promo_spikes)+ sparse=shrink 三窗口 —— 补货专属(与原 _build_repl 口径一致)
    - 不存 fused: 消费方现场 rolling_predict(own 权重)/滚动融合, 口径零冲突
    - upsert 幂等 ON DUPLICATE; 失败降级(消费方读表 miss 时降级直算)
    """
    from db import execute as _ex, executemany as _em, query as _q
    import time as _tm
    _t0 = _tm.time()
    try:
        _ex(_SKU_STATS_DDL)
        by_sku, by_sku_wh, by_sku_bc, by_sku_wh_bc = load_daily_sales_grouped(
            days, channel, skus=skus, with_bc_wh=True)
        if not by_sku and not by_sku_wh:
            return 0
        now_d = datetime.now(timezone.utc)
        # 仓型集合(与补货/断货同口径: inventory warehouse_type)
        try:
            _wh_type = {}
            for r in _q("SELECT DISTINCT warehouse, warehouse_type FROM inventory "  # no-barcode-ok: 仓型维度枚举
                        "WHERE channel=%s AND warehouse!=''", [channel]):
                _wh_type[str(r.get("warehouse") or "")] = str(r.get("warehouse_type") or "")
        except Exception:
            _wh_type = {}
        _c_whs = {w for w, t in _wh_type.items() if t == "platform"}
        _b_whs = {w for w, t in _wh_type.items() if t == "platform_b"}

        # 2026-10-29 精准: 按码 × 仓型 聚合 → 让 __C__/__BC__ 也有按码行
        #   (原只有 __ALL__ 有按码行 → bbcc 下多码 SKU 只能共用整盘合计日销)
        by_c_bc = {}
        for _k, _wd in (by_sku_wh_bc or {}).items():
            _p = _k.rsplit("|", 2)
            if len(_p) < 3:
                continue
            _key2 = "%s|%s" % (_p[0], _p[1])
            if _p[2] in _c_whs:
                _m2 = by_c_bc.setdefault(_key2, {})
                for _d, _q in _wd.items():
                    _m2[_d] = _m2.get(_d, 0) + _q

        # 全国聚合: __C__(platform) / __BC__(platform+platform_b) / __ALL__(全仓)
        by_all = dict(by_sku)
        # 2026-10-30: 删 by_bc(C+B 日销)——B 仓不产生销售, BC 盘的日销统一读 __C__
        by_c = {}
        for wk, wd in by_sku_wh.items():
            base, wh = wk.rsplit("|", 1) if "|" in wk else (wk, "")
            if not base:
                continue
            if wh in _c_whs:
                m = by_c.setdefault(base, {})
                for d, q in wd.items():
                    m[d] = m.get(d, 0) + q

        def _nnz(daily, win):
            n = 0
            for k, v in daily.items():
                try:
                    if v > 0 and (now_d - datetime.strptime(str(k)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days < win:
                        n += 1
                except Exception:
                    continue
            return n

        def _to_rows(key_daily, wh_label):
            """批量三窗口(plain+cut) → 行列表; 不存 fused(消费方现场算)"""
            rows_out = []
            if not key_daily:
                return rows_out
            m = calc_sales_multi(key_daily, windows=[7, 14, 28])
            _cut = {k: smooth_promo_spikes(d) for k, d in key_daily.items()}
            mc = calc_sales_multi(_cut, windows=[7, 14, 28], sparse="shrink")
            for k, daily in key_daily.items():
                sigma = 0.0
                # 2026-10-30 口径保护: sigma 固定用**最近 60 天**样本(原口径), 不随取数窗口(days)
                #   扩大而变 —— 否则数据满 90 天后 sigma 变大 → 动态安全库存 SS 变化 → 断货判定漂移
                vals = []
                for _dk, _dv in daily.items():
                    try:
                        if (now_d - datetime.strptime(str(_dk)[:10], "%Y-%m-%d")
                                .replace(tzinfo=timezone.utc)).days < _sig_days:
                            vals.append(_dv)
                    except Exception:
                        continue
                if len(vals) >= 7:
                    mm = sum(vals) / len(vals)
                    sigma = (sum((x - mm) ** 2 for x in vals) / len(vals)) ** 0.5
                rows_out.append({
                    "channel": channel, "sku": str(k), "warehouse": wh_label,
                    "ds7": round(m[7].get(k, 0.0), 3), "ds14": round(m[14].get(k, 0.0), 3),
                    "ds28": round(m[28].get(k, 0.0), 3), "sigma": round(sigma, 3),
                    "ds7_cut": round(mc[7].get(k, 0.0), 3), "ds14_cut": round(mc[14].get(k, 0.0), 3),
                    "ds28_cut": round(mc[28].get(k, 0.0), 3),
                    "nnz7": _nnz(daily, 7), "nnz14": _nnz(daily, 14), "nnz28": _nnz(daily, 28),
                    "last_sale": max(daily.keys()) if daily else None})
            return rows_out

        # 2026-10-30 sigma 样本天数参数化: 与看板断货卡/规则引擎同源(规则 params.sigma_days, 默认 60)
        #   一次读取(不在 _to_rows 内重复查库), 失败回退 60(原口径)
        try:
            from routes.dashboard_stock_risk import _rule_params as _rp_sig
            _sig_days = int(float((_rp_sig("stockout") or {}).get("sigma_days", 60) or 60))
            if _sig_days < 7:
                _sig_days = 60      # 样本过小(σ 需 ≥7 个有效销售日) → 回退默认
        except Exception as _esg:
            try_err('sales', 'sigma 样本天数读取降级(用 60)', _esg)
            _sig_days = 60
        # 2026-10-30 删除 __BC__ 日销行(用户定案方案B): B 仓是调拨中转, **不产生销售** ——
        #   BC 盘的"可售"是库存维度(inventory 聚合), 日销一律读 __C__; 保留 __BC__ 日销会让
        #   B 仓将来出现杂单时把安全线抬高 → 断货漏判
        rows = (_to_rows(by_c, WH_C)
                + _to_rows(by_all, WH_ALL)
                + _to_rows(by_sku_wh, ""))  # 逐仓行(wh_label='' 表示真仓名在 key 内——由调用侧拆分)
        # 2026-10-29 按 69码 行(barcode=X, warehouse=WH_ALL 跨仓合计):
        #   与 barcode='' 的全码合计行并存 —— 消费方不传 barcode 时只读 '' 行(语义不变),
        #   传 barcode=X 时读该码行(补货/断货按码)
        for _r in rows:
            _r["barcode"] = ""
        if by_sku_bc:
            _bc_rows = (_to_rows(by_sku_bc, WH_ALL) + _to_rows(by_c_bc, WH_C))
            for _r in _bc_rows:
                _r["barcode"] = str(_r.get("sku") or "").rsplit("|", 1)[-1]
                _r["sku"] = str(_r.get("sku") or "").rsplit("|", 1)[0]
            rows += _bc_rows
        # 逐仓行 sku 含 |仓名 → 拆分回 sku/warehouse 两列
        for r in rows:
            if r["warehouse"] == "" and "|" in r["sku"]:
                _s, _w = r["sku"].rsplit("|", 1)
                r["sku"], r["warehouse"] = _s, _w
        if rows:
            # 2026-10-29 先删该渠道旧行(替代 ON DUPLICATE —— 旧主键不含 barcode 会与
            # 含码唯一键冲突, 且同 SKU 不同码的行会被旧主键合并覆盖)
            try:
                _ex("DELETE FROM sku_sales_stats WHERE channel=%s", [channel])
            except Exception as _edel:
                try_err('sales', '日销旧行清理降级', _edel)
            cols = ["channel", "sku", "barcode", "warehouse", "ds7", "ds14", "ds28", "sigma",
                    "ds7_cut", "ds14_cut", "ds28_cut", "nnz7", "nnz14", "nnz28", "last_sale"]
            for i in range(0, len(rows), 500):
                chunk = rows[i:i + 500]
                _em("INSERT INTO sku_sales_stats(%s) VALUES(%s) ON DUPLICATE KEY UPDATE "
                    "ds7=VALUES(ds7), ds14=VALUES(ds14), ds28=VALUES(ds28), sigma=VALUES(sigma), "
                    "ds7_cut=VALUES(ds7_cut), ds14_cut=VALUES(ds14_cut), ds28_cut=VALUES(ds28_cut), "
                    "nnz7=VALUES(nnz7), nnz14=VALUES(nnz14), nnz28=VALUES(nnz28), "
                    "last_sale=VALUES(last_sale), updated_at=CURRENT_TIMESTAMP(6)" % (
                        ", ".join("`%s`" % c for c in cols),
                        ", ".join(["%s"] * len(cols))),
                    [tuple(r[c] for c in cols) for r in chunk])
        # 2026-10-30 可观测: 每次重建落一条(渠道/行数/耗时/范围), 用于评估"一天重建几次"与 RU 风险
        try:
            _ex("INSERT INTO quality_logs(log_type, level, message, source) "
                "VALUES('mat_rebuild','info',%s,'sales')",
                ("物化重建 %s: rows=%d %.1fs scope=%s" % (
                    channel, len(rows), _tm.time() - _t0,
                    ("skus=%d" % len(skus)) if skus else "ALL"),))
        except Exception as _el:
            try_err('sales', '物化重建日志降级', _el)
        return len(rows)
    except Exception as _e:
        from routes.common import try_err
        try_err('sales', 'sku_sales_stats 重建降级', _e)
        return 0


def get_sku_sales_stats(channel, skus=None, wh=None, days=28, barcode=None):
    """物化读取(消费方入口): 按 sku/wh 查行返回 {wh|sku: {ds7/14/28, sigma, *_cut, nnz*, last_sale}}

    wh 显式枚举(无魔法值): WH_C(__C__ 全国platform) / WH_BC(__BC__ platform+b) /
    WH_ALL(__ALL__ 全仓) / 具体仓名(逐仓) / None(全部仓维度)
    - 表空(首启/重建失败)→ 自动重建(含当天); 消费方读表 miss 时降级直算(调用侧)
    - 不返回 fused——消费方现场 rolling_predict/own 权重, 口径零冲突
    """
    try:
        from db import query as _q, one as _o
        _o(_SKU_STATS_DDL)
        _c = _o("SELECT COUNT(*) c FROM sku_sales_stats WHERE channel=%s", [channel]) or {}
        if int(_c.get("c") or 0) <= 0:
            _rebuild_sku_sales_stats(channel, 90)   # 2026-10-30 固定 90(原跟随调用方 days → 窗口不一致)
        where = "channel=%s"
        params = [channel]
        # 2026-10-29 按 69码: 不传 → 只读全码合计行(barcode='', 语义与改造前一致);
        #   传 barcode=X → 读该码行; 传 '' → 显式读合计行
        if barcode is None or barcode == "":
            where += " AND COALESCE(barcode,'')=''"
        elif barcode == "*":
            # 2026-10-29 批量读全部按码行(断货/健康一次取全, 避免逐码往返)
            where += " AND COALESCE(barcode,'')!=''"
        else:
            where += " AND COALESCE(barcode,'')=%s"
            params += [str(barcode)]
        if skus:
            _ph = ",".join(["%s"] * min(len(list(skus)), 500))
            if _ph:
                where += " AND sku IN (" + _ph + ")"
                params += list(skus)[:500]
        if wh is not None:
            if wh in (WH_C, WH_BC, WH_ALL) or (isinstance(wh, str) and wh):
                where += " AND warehouse=%s"
                params += [wh]
            else:
                where += " AND warehouse IN (%s,%s,%s)"  # 非法 wh → 查全部聚合行
                params += [WH_C, WH_BC, WH_ALL]
        rows = _q("SELECT sku, COALESCE(barcode,'') AS barcode, warehouse, ds7, ds14, ds28, sigma, "
                  "ds7_cut, ds14_cut, ds28_cut, nnz7, nnz14, nnz28, last_sale "
                  "FROM sku_sales_stats WHERE %s" % where, params)
        out = {}
        for r in rows:
            _w = str(r.get("warehouse") or "")
            _b = str(r.get("barcode") or "")
            # 2026-10-29 按 69码: key 加码(原 sku|wh —— 同 SKU 多码同仓会互相覆盖);
            #   返回 dict 也带 barcode, 消费方无需再查库
            wk = "%s|%s|%s" % (r.get("sku") or "", _b, _w)
            out[wk] = {"sku": r.get("sku"), "barcode": _b, "warehouse": _w,
                       "ds7": float(r.get("ds7") or 0), "ds14": float(r.get("ds14") or 0),
                       "ds28": float(r.get("ds28") or 0), "sigma": float(r.get("sigma") or 0),
                       "ds7_cut": float(r.get("ds7_cut") or 0), "ds14_cut": float(r.get("ds14_cut") or 0),
                       "ds28_cut": float(r.get("ds28_cut") or 0),
                       "nnz7": int(r.get("nnz7") or 0), "nnz14": int(r.get("nnz14") or 0),
                       "nnz28": int(r.get("nnz28") or 0), "last_sale": str(r.get("last_sale") or "")[:10]}
        return out
    except Exception as _e:
        from routes.common import try_err
        try_err('sales', 'sku_sales_stats 读取降级', _e)
        return {}
