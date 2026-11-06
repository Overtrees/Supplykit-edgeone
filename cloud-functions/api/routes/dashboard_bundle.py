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
