"""原生 alerts 路由(方案 B): 列表(分组配额) + 精确计数"""
from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced

router = APIRouter(tags=["alerts"])

_FIELDS = "id, alert_type, title, description, severity, status, source, related_sku, " \
          "related_order_no, warehouse_type, channel, created_at"


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
    return ok(items)


@router.get("/alerts/counts")
@traced
def alert_counts(channel: str = "jd"):
    """精确计数(独立 COUNT, 不从截断列表 filter)"""
    rows = query("SELECT alert_type, severity, COUNT(*) AS c FROM alerts "
                 "WHERE channel=%s AND status='active' GROUP BY alert_type, severity", [channel])
    by_type = {}
    by_severity = {}
    total = 0
    for r in rows:
        at = r.get("alert_type") or "other"
        sev = r.get("severity") or "info"
        c = int(r.get("c") or 0)
        total += c
        by_type[at] = by_type.get(at, 0) + c
        by_severity[sev] = by_severity.get(sev, 0) + c
    return ok({"total": total, "by_type": by_type, "by_severity": by_severity})
