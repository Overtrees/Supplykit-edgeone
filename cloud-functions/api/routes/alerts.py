"""原生 alerts 路由(方案 B): 列表(分组配额) + 精确计数"""
from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced, try_err

router = APIRouter(tags=["alerts"])

_FIELDS = "id, alert_type, title, description, severity, status, source, related_sku, " \
          "related_order_no, warehouse_type, warehouse, channel, created_at"


def _attach_warehouse(alerts, channel):
    """告警补实际仓库名 —— 仅补存量空值(新告警逐仓生成时已带 warehouse 列)

    规则引擎/seed 已按 SKU×仓 逐仓生成告警(warehouse 列=实际仓名); 存量 SKU 级告警
    warehouse 为空时按 related_sku+warehouse_type 反查 inventory 补(逗号连接, 前端截断)。
    """
    if not alerts:
        return alerts
    missing = [a for a in alerts if not (a.get("warehouse") or "")]
    if not missing:
        return alerts
    skus = sorted({str(a.get("related_sku") or "") for a in missing} - {""})
    if not skus:
        return alerts
    wh_map = {}
    for i in range(0, len(skus), 200):
        _ph = ",".join(["%s"] * len(skus[i:i + 200]))
        for r in query(
                "SELECT sku, warehouse_type, "
                "GROUP_CONCAT(DISTINCT warehouse ORDER BY warehouse SEPARATOR ',') AS whs "
                "FROM inventory WHERE channel=%s AND sku IN (" + _ph + ") "  # no-barcode-ok: 库存补充仅拼接仓库名显示
                "AND warehouse IS NOT NULL AND warehouse!='' "
                "GROUP BY sku, warehouse_type", [channel] + skus[i:i + 200]):
            wh_map[(str(r.get("sku") or ""), str(r.get("warehouse_type") or ""))] = str(r.get("whs") or "")
    for a in missing:
        _k = (str(a.get("related_sku") or ""), str(a.get("warehouse_type") or ""))
        _v = wh_map.get(_k, "")
        if not _v:
            _v = wh_map.get((str(a.get("related_sku") or ""), ""), "")
        a["warehouse"] = _v
    return alerts


@router.get("/alerts")
@traced
def list_alerts(channel: str = "jd", limit: int = 200, exclude: str = "",
                types: str = "", offset: int = 0, paged: int = 0):
    """告警列表: 分组配额(low_stock/replenish/other 各组 limit 条, 组内 id DESC)——可见性由分组配额保证

    exclude: 逗号分隔的 alert_type 黑名单。**2026-11-07 加**: 看板首屏曾用 `limit=20000`
    预拉全量(3.9MB / 4.7s), 只为卡面几行明细 —— 其中 `own_overstock` 一家 7215 行(占 2/3),
    而这批货由「自有仓健康」卡专属承载, 看板的告警列表从来不显示它(aux 里也排除了)。
    首屏 4 个重接口并发时, 这 3.9MB 直接把浏览器同域连接占满, boot 从 1.2s 拖到 6.6s。

    ── 2026-11-07 P2 真分页(`paged=1`) ──────────────────────────────────
    分组配额的语义是"每类各取 N 条", 天生**不能翻页**: 第 2 页要接着第 1 页往下读,
    而不是"每类再各取 N 条"(那会重复)。所以分页模式改成单一全序(id DESC)+ SQL LIMIT/OFFSET,
    `types` 用来收窄到某一类(low_stock / other), 与 `exclude` 可叠加。
    弹窗按需拉一页(300 条), 卡面只读 `/alerts/counts` 的计数 —— 两边各自最省。
    """
    _ex = [x.strip() for x in (exclude or "").split(",") if x.strip()]
    _tp = [x.strip() for x in (types or "").split(",") if x.strip()]
    if paged:
        _w = " AND alert_type NOT IN (" + ",".join(["%s"] * len(_ex)) + ")" if _ex else ""
        if _tp:
            _w += " AND alert_type IN (" + ",".join(["%s"] * len(_tp)) + ")"
        _p = [channel] + _ex + _tp
        _lim = max(1, min(int(limit or 300), 1000))
        _off = max(0, int(offset or 0))
        rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active'" % _FIELDS
                     + _w + " ORDER BY id DESC LIMIT %d OFFSET %d" % (_lim, _off), _p)
        _attach_warehouse(rows, channel)
        _total = -1
        try:
            _c = one("SELECT COUNT(*) AS n FROM alerts WHERE channel=%s AND status='active'" + _w,
                     _p) or {}
            _total = int(_c.get("n") or 0)
        except Exception as _e:
            try_err("alerts", "告警总数统计降级", _e)
        return ok({"items": rows, "total": _total, "limit": _lim, "offset": _off})
    _ex_sql = ""
    if _ex:
        _ex_sql = " AND alert_type NOT IN (" + ",".join(["%s"] * len(_ex)) + ")"
    items = []
    groups = [("low_stock", "low_stock"), ("replenish", "replenish"), ("other", None)]
    for gname, atype in groups:
        if atype:
            if atype in _ex:
                continue          # 该类型已被调用方排除, 不必查
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' AND alert_type=%%s "
                         "ORDER BY id DESC LIMIT %%s" % _FIELDS, [channel, atype, limit])
        else:
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' "
                         "AND alert_type NOT IN ('low_stock','replenish')" + _ex_sql +
                         " ORDER BY id DESC LIMIT %%s" % _FIELDS, [channel] + _ex + [limit])
        items.extend(rows)
    _attach_warehouse(items, channel)
    return ok(items)


@router.get("/alerts/counts")
@traced
def alert_counts(channel: str = "jd"):
    """精确计数(独立 COUNT, 不从截断列表 filter): total/by_type/by_severity/仓库分布(PA 对齐)"""
    rows = query("SELECT alert_type, severity, "
                 "IFNULL(NULLIF(warehouse_type,''),'') AS wt, COUNT(*) AS c "
                 "FROM alerts WHERE channel=%s AND status='active' "
                 "GROUP BY alert_type, severity, wt", [channel])
    by_type, by_severity, by_wh = {}, {}, {}
    by_wh_ls, by_wh_slow, by_wh_rp = {}, {}, {}
    total = 0
    for r in rows:
        at = r.get("alert_type") or "other"
        sev = r.get("severity") or "info"
        wt = r.get("wt") or ""
        c = int(r.get("c") or 0)
        total += c
        by_type[at] = by_type.get(at, 0) + c
        by_severity[sev] = by_severity.get(sev, 0) + c
        by_wh[wt] = by_wh.get(wt, 0) + c
        tgt = by_wh_rp if at == "replenish" else (by_wh_slow if at == "slow_moving" else by_wh_ls)
        tgt[wt] = tgt.get(wt, 0) + c

    def _wmap(m):
        b = m.get("platform_b", 0)
        c = m.get("platform", 0)
        o = m.get("own", 0)
        # 2026-10-30 补 bc 维度: BBCC 合计行的 warehouse_type='bc', 原映射只认
        #   platform/platform_b/own/'' → bc 类型被整类丢弃(ls_warehouse.bc 恒 0, 140 条 low_stock 失踪)
        # 只透传: bc 仅 bbcc 模式产生; traditional 无 bc 类型(b 恒 0, 前端 traditional 也不读 bc)
        return {"b": b, "c": c, "own": o, "bc": m.get("bc", 0), "unknown": m.get("", 0)}

    rp = by_type.get("replenish", 0)
    return ok({"total": total, "by_type": by_type, "by_severity": by_severity,
               "replenish": rp, "non_replenish": total - rp,
               "by_warehouse": _wmap(by_wh), "ls_warehouse": _wmap(by_wh_ls),
               "slow_warehouse": _wmap(by_wh_slow), "rp_warehouse": _wmap(by_wh_rp)})
