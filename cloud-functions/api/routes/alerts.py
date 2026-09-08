"""原生 alerts 路由(方案 B): 列表(分组配额) + 精确计数"""
from fastapi import APIRouter

from db import query
from routes.common import ok, traced

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
                "FROM inventory WHERE channel=%s AND sku IN (" + _ph + ") "
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
def list_alerts(channel: str = "jd", limit: int = 200):
    """告警列表: 分组配额(low_stock/replenish/other 各组 limit 条, 组内 id DESC)——可见性由分组配额保证"""
    items = []
    groups = [("low_stock", "low_stock"), ("replenish", "replenish"), ("other", None)]
    for gname, atype in groups:
        if atype:
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' AND alert_type=%%s "
                         "ORDER BY id DESC LIMIT %%s" % _FIELDS, [channel, atype, limit])
        else:
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' "
                         "AND alert_type NOT IN ('low_stock','replenish') ORDER BY id DESC LIMIT %%s"
                         % _FIELDS, [channel, limit])
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
        return {"b": b, "c": c, "own": o, "bc": b + c, "unknown": m.get("", 0)}

    rp = by_type.get("replenish", 0)
    return ok({"total": total, "by_type": by_type, "by_severity": by_severity,
               "replenish": rp, "non_replenish": total - rp,
               "by_warehouse": _wmap(by_wh), "ls_warehouse": _wmap(by_wh_ls),
               "slow_warehouse": _wmap(by_wh_slow), "rp_warehouse": _wmap(by_wh_rp)})
