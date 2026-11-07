"""看板 summary 的「六合一」取数 + 还原(红线 A: 新逻辑落独立文件, 页面只编排)

## 为什么要合并

2026-11-06 线上埋点(`db.STATS`)测到: summary 每请求要建 6~8 条 TiDB Serverless 连接。
TiDB Cloud Serverless 的建连含 TLS 握手 + 网关认证, 单次与一次普通 SQL 同价甚至更贵;
沙箱到同区集群 6 次建连累计 99.7s(其中一次挂起 91s, 把 `_q_flows_month` 直接拖到 91s)。
**请求慢与"偶发 30s 不可用"是同一个原因: 在建连上赌博, 且一赌 6 把。**

合并后连接数 6→1, 六段 SQL 与 `_q_agg` / `_q_recent` / `_q_brands` / `_q_flows_month` /
`_q_counts` / `_q_cfg` **逐字同源**(同 `_status_cond`、同 `_FLOW_NET`、同 JOIN、同 deleted_at
过滤), 只把结果拍成一张宽表 `(tag, s1, s2, s3, n1, n2, n3)`, 由 `_split_bundle` 还原成各自
原来的结构 —— 调用方拿到的仍是原来那六个变量, 一行都不用改。

## 列序是契约

宽表只有 7 列, 六种 tag 共用。**列序错位不会报错, 只会让数字悄悄变错**:
2026-11-06 首次实现就把 cfg 的 `key`/`value` 写反了, 结果安全线全部退回默认值,
低库存从 509 变成 4, 而卡片照常渲染。`local_test` 里 5 条断言就是钉这个的。

## 为什么用 UNION ALL 而不是多语句

多语句要开 `CLIENT.MULTI_STATEMENTS`, 改的是连接层行为; UNION ALL 是一条普通参数化 SQL,
语义与安全边界都不变。代价是 TiDB 要串行跑六个分支 —— 实测 0.91s, 而六条独立查询串行
5.92s(同一进程、连接已热), 仍然快 6 倍。
"""
from db import query

from fastapi import APIRouter

from routes.common import ok, traced, try_err


# ── 2026-11-07 S0: 首屏聚合(boot) ─────────────────────────────────────────────
# 为什么需要它(线上端口实测, 不是本地直连):
#   `GET /api/nonexistent`(不碰库/不鉴权)打 5 次中位 **879ms** —— 这是 EdgeOne Makers
#   函数入口的固定成本(启动 + 网关), 与 SQL 无关。于是"一屏要多久"≈ 0.88s × 请求数 + 重算。
#   看板首屏实际发 8 个请求(store.loadAll 的 summary/orders/logs/alerts/stock-risk +
#   DashboardPage 自拉的 summary(还**不带 part**, 全量含 charts)/aux/stock-risk),
#   其中 summary/stock-risk 各被拉两遍 —— 光入口成本就 7s 量级, 再叠加串行编排就是 10s+。
#   合并成 1 个请求后: 0.88s(一次入口) + 内部并行取数(缓存命中时 <0.5s) ≈ 1.4~1.7s。
#
# 为什么内部只用 3 个 worker:
#   db 连接池是**每实例 10 条**的全局资源(见 db._POOL_LIMIT / heavy)。4 个模块各自再开
#   并行会把名额吃满, 后面的请求在 _acquire 排队 —— 这正是 2026-11-06 那次雪崩的形状。
#   3 路分批能把"冷算 6s 的 stock-risk"和其它快项错开, 又不至于一次抢占 10 条连接。
router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/boot")
@traced
def dashboard_boot(channel: str = "jd", mode: str = "", page: int = 1, page_size: int = 30,
                   search: str = "", status: str = "", start_date: str = "",
                   end_date: str = ""):
    """首屏一次取全: summary(part=main) + aux + orders + stock-risk

    每一项**复用各自原有的路由函数**, 所以缓存键/TTL/失效口径/业务数值与单独调用逐字一致,
    不存在"聚合接口另算一套"的第二真源(红线 B)。任一项失败只让该项为 None, 其余照常返回 ——
    前端按字段缺失走原有兜底, 不会整屏白。
    """
    from concurrent.futures import ThreadPoolExecutor
    from routes.dashboard import dashboard_summary, dashboard_aux, stock_risk
    from routes.orders import list_orders

    _res = {}
    _swrs = {}   # §15.162: 子项 __swr 收集 → 聚合响应顶层冒泡(data 形状逐字不变)

    def _one(_name, _fn, **kw):
        try:
            _r = _fn(**kw)
            # 各路由返回 {ok, data}; 这里解包成前端直接可用的结构, 与单独请求的 response.data 同形
            _d = (_r or {}).get("data") if isinstance(_r, dict) and "data" in _r else _r
            # 2026-11-07 §15.162 P2: 子项冷态 SWR 的播报冒泡到聚合顶层 —— 子项 data
            #   里的 __swr 在这里摘掉, 前端拿到的 data 与单独请求完全同形
            if isinstance(_d, dict):
                _s = _d.pop("__swr", None)
                if _s:
                    _swrs[_name] = _s
            _res[_name] = _d
        except Exception as _e:
            _res[_name] = None
            try_err("boot", "首屏聚合项失败: " + _name, _e)

    # 2026-11-07 S0.1: max_workers 必须 = 任务数(4)。原来写 3 却提交 4 个任务,
    #   第 4 个(stock_risk)要等前一批腾出线程 —— 冷态下 aux 6.8s 先跑, stock_risk
    #   从第 6.8s 才开始再跑 4.8s → boot 实测 11.1s(线上 slow_alert 11:46/11:48)。
    #   并行度 4 仍远小于 DB_POOL_LIMIT=10; heavy 名额 3 由 summary(不占)/aux/stock-risk 分配,
    #   恰好 3 个, 不会互相踢成 HeavyBusy。
    with ThreadPoolExecutor(max_workers=4) as _ex:
        _futs = [
            _ex.submit(_one, "summary", dashboard_summary, channel=channel,
                       start_date=start_date, end_date=end_date, mode=mode, part="main"),
            _ex.submit(_one, "orders", list_orders, channel=channel, page=page,
                       page_size=page_size, search=search, status=status),
            _ex.submit(_one, "aux", dashboard_aux, channel=channel, mode=mode),
            _ex.submit(_one, "stock_risk", stock_risk, channel=channel, full=0),
        ]
        for _f in _futs:
            _f.result()
    # §15.162 P2: 任一子项在冷态 SWR → 聚合顶层播报(freshness 取最旧, parts 带各子项明细);
    # 没有子项冷态 → 响应与旧版逐字相同(顶层无 __swr, data 形状不变)。
    if _swrs:
        _ages = [int(v.get("freshness_s") if v.get("freshness_s") is not None else -1)
                 for v in _swrs.values()]
        return ok(_res, __swr={"state": "serving_previous",
                               "freshness_s": max(_ages) if _ages else -1,
                               "fp_changed": True, "parts": _swrs})
    return ok(_res)


def _deps():
    """延迟取 dashboard 的口径常量 —— 顶层 import 会与 dashboard.py 形成循环"""
    from routes.dashboard import _status_cond, _FLOW_NET
    return _status_cond, _FLOW_NET


def _q_bundle(channel, days60, d1, st30, today):
    """6 条小查询 → 1 条 UNION ALL(1 条连接 / 1 次往返)

    参数沿用各自原函数的时间界:
      days60/d1  → agg 与 recent
      st30/today → brands 与 flows(与 `_q_brands(channel, min(days60, st30), today)` 一致)
    """
    _status_cond, _FLOW_NET = _deps()
    _sc = _status_cond()
    _sc_o = _status_cond("o.order_status")
    _gmv = ("total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) "
            "+ COALESCE(tax_amount,0)")
    _gmv_o = ("o.total_amount - COALESCE(o.discount_amount,0) + COALESCE(o.freight_amount,0) "
              "+ COALESCE(o.tax_amount,0)")
    sql = (
        "SELECT 'agg' AS tag, DATE(`date`) AS s1, order_status AS s2, store AS s3, "
        "SUM(gmv) AS n1, SUM(subsidy) AS n2, SUM(cnt) AS n3 "
        "FROM orders_day_agg WHERE channel=%s AND `date`>=%s AND `date`<%s "
        "GROUP BY `date`, order_status, store "
        "UNION ALL SELECT 'recent', DATE(ordered_at), order_status, store, "
        "SUM(IF(" + _sc + "," + _gmv + ",0)), "
        "SUM(IF(" + _sc + ",COALESCE(subsidy_amount,0),0)), COUNT(*) "
        "FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at>=%s "
        "GROUP BY DATE(ordered_at), order_status, store "
        "UNION ALL SELECT 'brands', DATE(o.ordered_at), COALESCE(p.brand,''), '', "
        "SUM(IF(" + _sc_o + "," + _gmv_o + ",0)), "
        "SUM(IF(" + _sc_o + ",COALESCE(o.subsidy_amount,0),0)), "
        "SUM(IF(o.order_status='申请退款'," + _gmv_o + ",0)) "
        "FROM orders o LEFT JOIN (SELECT DISTINCT sku, brand, channel FROM products "
        "WHERE brand IS NOT NULL AND brand!='') p ON o.sku=p.sku AND o.channel=p.channel "
        "WHERE o.channel=%s AND (o.deleted_at IS NULL OR o.deleted_at='') AND o.ordered_at>=%s "
        "AND o.ordered_at<=%s GROUP BY DATE(o.ordered_at), COALESCE(p.brand,'') "
        "UNION ALL SELECT 'flows', DATE(arrived_time), shop, '', "
        "COALESCE(SUM(" + _FLOW_NET + "),0), NULL, NULL "
        "FROM gmv_flows WHERE channel=%s AND arrived_time>=%s AND arrived_time<=%s "
        "GROUP BY DATE(arrived_time), shop "
        "UNION ALL SELECT 'counts','','','', a.c, b.c, c.c FROM "
        "(SELECT COUNT(*) c FROM alerts WHERE channel=%s AND status='active') a, "
        "(SELECT COUNT(*) c FROM products WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')) b, "
        "(SELECT COUNT(*) c FROM suppliers WHERE channel=%s) c "
        "UNION ALL SELECT 'cfg', `key`, value, '', NULL, NULL, NULL "
        "FROM replenishment_config WHERE channel=%s OR channel=''"
    )
    _p = [channel, days60, d1,
          channel, d1 + " 00:00:00",
          channel, min(days60, st30) + " 00:00:00", today + " 23:59:59",
          channel, st30 + " 00:00:00", today + " 23:59:59",
          channel, channel, channel,
          channel]
    return query(sql, _p)


def _split_bundle(rows):
    """宽表 → 六个消费方各自的原结构(键名与 `_q_*` 返回值逐字一致)

    返回 (agg, recent, brands, flows, counts, cfg):
      agg/recent  [{'d','order_status','store','g','sub','cnt'}]
      brands      [{'d','brand','g','sb','rf'}]
      flows       (总额, {日: 净额}, {店: 净额}) —— 与 `_q_flows_month` 同构, 无流水给 (None,)*3
      counts      {'alerts','products','suppliers'}
      cfg         {key: value}(value 保持原值, NULL 仍是 None)
    """
    agg, recent, brands, flows = [], [], [], []
    counts, cfg = None, None
    for r in rows:
        _t = r.get("tag")
        s1 = str(r.get("s1") or "")[:10]
        s2 = str(r.get("s2") or "")
        s3 = str(r.get("s3") or "")
        n1, n2, n3 = r.get("n1"), r.get("n2"), r.get("n3")
        if _t == "agg":
            agg.append({"d": s1, "order_status": s2, "store": s3, "g": n1, "sub": n2, "cnt": n3})
        elif _t == "recent":
            recent.append({"d": s1, "order_status": s2, "store": s3, "g": n1, "sub": n2, "cnt": n3})
        elif _t == "brands":
            brands.append({"d": s1, "brand": s2, "g": n1, "sb": n2, "rf": n3})
        elif _t == "flows":
            flows.append({"d": s1, "shop": s2, "v": n1})
        elif _t == "counts":
            counts = {"alerts": int(n1 or 0), "products": int(n2 or 0), "suppliers": int(n3 or 0)}
        elif _t == "cfg":
            cfg = cfg or {}
            # value 保持原值(NULL 仍是 None) —— 与 _q_cfg 的 dict 逐字同构
            cfg[str(r.get("s1") or "")] = r.get("s2")
    if flows:
        _tot = 0.0
        _by_day, _by_shop = {}, {}
        for r in flows:
            _v = float(r.get("v") or 0)
            _dd = str(r.get("d") or "")[:10]
            _sh = str(r.get("shop") or "")
            _tot += _v
            _by_day[_dd] = _by_day.get(_dd, 0.0) + _v
            _by_shop[_sh] = _by_shop.get(_sh, 0.0) + _v
        flows = (round(_tot, 2),
                 {k: round(v, 2) for k, v in _by_day.items()},
                 {k: round(v, 2) for k, v in _by_shop.items()})
    else:
        flows = (None, None, None)
    return agg, recent, brands, flows, counts, cfg
