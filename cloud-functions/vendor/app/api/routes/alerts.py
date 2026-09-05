"""告警列表端点

明细列表按分组配额取数: 补货告警(replenish)每次跑建议会重生成数千条, 只按 id DESC 取 limit
条会占满窗口把低库存/滞销挤出列表(看板低库存卡空白, 2026-08-28 18:35 报障)。
原则: 可见性由分组配额保证, 排序只决定组内展示顺序; 计数一律独立 COUNT, 不取自截断列表。
"""
from fastapi import APIRouter, Request
import time
from app.core.database import get_conn
from app.core.response import ok

router = APIRouter()
_alerts_cache = {}
_CACHE_TTL = 60

_ALERT_FIELDS = ("id", "alert_type", "title", "description", "severity", "source",
                 "channel", "status", "related_sku", "warehouse_type", "created_at")
_ALERT_SQL = ", ".join(_ALERT_FIELDS)
_GROUP_CASE = ("CASE WHEN alert_type='replenish' THEN 'replenish' "
               "WHEN alert_type='low_stock' THEN 'low_stock' ELSE 'other' END")


def _version_key():
    """数据变更版本号: 规则操作递增 _rules_version, 库存/订单/清洗/配置递增 _replen_version。
    缓存 key 含版本号 → 任何数据变更即命中失效, 不依赖 TTL(缺一 alerts 缓存不失效)"""
    try:
        conn = get_conn()
        r1 = conn.execute("SELECT value FROM replenishment_config WHERE key='_rules_version'").fetchone()
        r2 = conn.execute("SELECT value FROM replenishment_config WHERE key='_replen_version'").fetchone()
        conn.close()
        return f"{r1[0] if r1 else 0}|{r2[0] if r2 else 0}"
    except Exception:
        return "0|0"


def _alert_channel_where(channel):
    """渠道过滤; 空字符串/未传 = 全部渠道(旧数据 channel='' 也包含在内)"""
    if not channel or channel == 'all':
        return "1=1", []
    return "channel=?", [channel]


def _group_cond(group):
    return f"({_GROUP_CASE} NOT IN ('replenish','low_stock'))" if group == 'other' \
        else f"({_GROUP_CASE}='{group}')"


def _counts(conn, ch_sql, ch_p, status):
    """精确计数: 总数/按类型/按严重度。单次扫描聚合, 不做多次独立查询。
    注: 不再从截断列表 filter 出计数——列表只是配额样本, 总数可能远大于列表长度。"""
    rows = conn.execute(
        f"SELECT {_GROUP_CASE} AS grp, alert_type, severity, "
        f"CASE WHEN warehouse_type IS NULL OR warehouse_type = '' THEN '' ELSE warehouse_type END AS wt, "
        f"COUNT(*) AS n FROM alerts WHERE status=? AND ({ch_sql}) GROUP BY 1,2,3,4", [status] + ch_p).fetchall()
    by_type, by_sev, by_wh = {}, {}, {}
    by_wh_ls = {}    # 低库存(low_stock) 仓库分布
    by_wh_slow = {}  # 滞销(slow_moving) 仓库分布
    by_wh_rp = {}    # 补货(replenish) 仓库分布
    total = 0
    for _grp, atype, sev, wt, n in rows:
        total += n
        by_type[atype] = by_type.get(atype, 0) + n
        by_sev[sev] = by_sev.get(sev, 0) + n
        by_wh[wt or ''] = by_wh.get(wt or '', 0) + n
        _tgt = by_wh_rp if atype == 'replenish' else (by_wh_slow if atype == 'slow_moving' else by_wh_ls)
        _tgt[wt or ''] = _tgt.get(wt or '', 0) + n
    rp = by_type.get('replenish', 0)
    def _wmap(m):
        _b = m.get('platform_b', 0); _c = m.get('platform', 0); _o = m.get('own', 0)
        return {"b": _b, "c": _c, "own": _o, "bc": _b + _c, "unknown": m.get('', 0)}
    return {"total": total, "by_type": by_type, "by_severity": by_sev,
            "replenish": rp, "non_replenish": total - rp,
            "by_warehouse": _wmap(by_wh),
            "ls_warehouse": _wmap(by_wh_ls), "slow_warehouse": _wmap(by_wh_slow), "rp_warehouse": _wmap(by_wh_rp)}


def _grouped_query(conn, channel, per_group_limit, status):
    ch_sql, ch_p = _alert_channel_where(channel)
    rows = []
    for g in ('low_stock', 'replenish', 'other'):
        for r in conn.execute(
            f"SELECT {_ALERT_SQL} FROM alerts "
            f"WHERE status=? AND ({ch_sql}) AND ({_group_cond(g)}) ORDER BY id DESC LIMIT ?",
            [status] + ch_p + [per_group_limit]).fetchall():
            rows.append(dict(zip(_ALERT_FIELDS, r)))
    # warehouse_type 兜底: 规则/补货/滞销等生成路径未写该列的告警, 查询时按 SKU 从库存补
    # (一次查询按"低库存风险仓优先"排序, Python 每 SKU 取最优) —— 看板 B/C/自有 分布据此精确化
    _miss = [r for r in rows if not (r.get('warehouse_type') or '')]
    if _miss:
        try:
            _skus = list(dict.fromkeys(str(r.get('related_sku') or '') for r in _miss if r.get('related_sku')))
            if _skus:
                _p = ",".join('?' * len(_skus))
                _rows = conn.execute(
                    f"SELECT sku, warehouse_type FROM inventory WHERE channel=? AND sku IN ({_p}) "
                    f"ORDER BY (CASE WHEN safety_qty > 0 THEN available_qty * 1.0 / safety_qty ELSE 1 END) ASC, sku, warehouse_type",
                    [channel] + _skus).fetchall()
                _best = {}
                for _s, _w in _rows:
                    if _s not in _best:
                        _best[_s] = _w or ''
                for r in _miss:
                    if r.get('related_sku') in _best:
                        r['warehouse_type'] = _best[r['related_sku']]
        except Exception:
            pass
    return rows, _counts(conn, ch_sql, ch_p, status)


def fetch_alerts_grouped(conn, channel, per_group_limit=100, status='active'):
    """按分组各取 per_group_limit 条。看板每组 200 条, 保证低库存卡/补货卡都有数据。"""
    return _grouped_query(conn, channel, per_group_limit, status)[0]


def alert_counts(conn, channel, status='active'):
    """看板计数用。调用方不得再从截断列表 filter 出计数。"""
    ch_sql, ch_p = _alert_channel_where(channel)
    return _counts(conn, ch_sql, ch_p, status)


@router.get("/api/alerts")
def list_alerts(request: Request, channel: str = '', limit: int = 100, status: str = 'active'):
    # key 含 limit + 版本号: 此前 alerts_{channel}_{status} 不含 limit(不同 limit 共用一份缓存)
    # 也不含版本号(靠 300s TTL, 数据变更后最长 60s 旧数据) —— 双修
    key = f"alerts_{channel}_{status}_{limit}_{_version_key()}"
    cached = _alerts_cache.get(key)
    if cached and cached['ts'] + _CACHE_TTL > time.time():
        return cached['data']
    try:
        conn = get_conn()
        data = fetch_alerts_grouped(conn, channel, per_group_limit=limit, status=status)
        conn.close()
    except Exception:
        data = []
    _alerts_cache[key] = {'data': ok(data), 'ts': time.time()}
    return ok(data)
