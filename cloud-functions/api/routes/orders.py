"""原生 orders 路由(方案 B): 分页+搜索+状态筛选+单条软删/恢复/永久删除(契约与旧 backend 一致)"""
from fastapi import APIRouter, Request
from routes.analysis_cache import cache_get as _cache_get

def _evaluate_restored(ids, channel=None):
    """restore 联动(2026-10-15): 恢复订单→order.created 规则重评估(超卖/低库存——恢复后重新生成告警——对称于删单联动关)

    2026-11-02 修复: 原函数体引用未定义的 `channel`(函数签名只有 ids), NameError 被底部
    `except Exception` 吞成"降级"留痕 → 恢复订单的规则重评估**从未真正执行**。
    现按订单实际 channel 分组评估(批量恢复可能跨渠道); 显式传 channel 时跳过查库。
    """
    if not ids:
        return
    if channel:
        _evaluate_restored_ch(ids, channel)
        return
    try:
        _ph = ",".join(["%s"] * len(ids))
        _chans = [r.get("channel") for r in
                  query("SELECT DISTINCT channel FROM orders WHERE id IN (%s)" % _ph, ids)]
    except Exception as _e:
        try_err('orders', 'restore 联动取 channel 降级', _e)
        return
    for _ch in _chans:
        if _ch:
            _evaluate_restored_ch(ids, _ch)


def _evaluate_restored_ch(ids, channel):
    """单渠道的 restore 联动评估(逻辑同原 _evaluate_restored, 只负责一个 channel)"""
    try:
        from core.rules import evaluate_many, load_rules_for
        _ph = ",".join(["%s"] * len(ids))
        rows = query("SELECT sku, SUM(quantity) AS q FROM orders WHERE id IN (%s) "
                     "AND (deleted_at IS NULL OR deleted_at='') GROUP BY sku" % _ph, ids)
        if not rows:
            return
        _skus = [r.get("sku") for r in rows if r.get("sku")]
        _qty = {r.get("sku"): r.get("q") or 0 for r in rows}
        _inv_map = {}
        for _i in range(0, len(_skus), 200):
            _b = _skus[_i:_i + 200]
            _ph2 = ",".join(["%s"] * len(_b))
            for r2 in query("SELECT sku, SUM(available_qty) AS avail FROM inventory "  # no-barcode-ok: 超卖按 SKU 汇总(与 close_oversell 同口径)
                            "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph2),
                            [channel] + _b):
                _inv_map[r2.get("sku")] = int(r2.get("avail") or 0)
        # 超卖口径(2026-11-06 收敛到 core.metrics): 判定量 = **未发货订单量**
        #   (待确认/待发货 且 近 N 天), 与每日全量评估、删单联动同一个数。
        #   原来是全历史累计销量 —— 卖过一阵就恒大于当前可用, 每个 SKU 都"超卖";
        #   而且恢复订单后按它重评, 一句"历史卖得多"就能把告警一直续上。
        #   顺带把金额/批量字段并进同一条条件聚合(原先是另一次按 200 分批的全量扫描)。
        from core import metrics as _M
        _ow_cond = _M.oversell_sql_cond(*_M.oversell_window(_M.rule_params_of(channel, "oversell")))
        _cum3 = {}
        _agg3 = {}
        for _i3 in range(0, len(_skus), 200):
            _b3 = _skus[_i3:_i3 + 200]
            _ph4 = ",".join(["%s"] * len(_b3))
            _osql3 = (
                "SELECT sku, "
                "SUM(CASE WHEN {c} THEN quantity ELSE 0 END) AS q, "
                "SUM(CASE WHEN {c} THEN total_amount ELSE 0 END) AS amt, "
                "MAX(CASE WHEN {c} THEN quantity ELSE 0 END) AS mx, "
                "MIN(CASE WHEN {c} THEN quantity END) AS mn, "
                "SUM(CASE WHEN {c} THEN 1 ELSE 0 END) AS lc, "
                "MAX(CASE WHEN {c} THEN unit_price ELSE 0 END) AS up "
                "FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') "
                "AND sku IN (%s) GROUP BY sku"
            ).replace("{c}", _ow_cond)
            for _r4 in query(_osql3 % ("%s", _ph4), [channel] + _b3):
                _sk4 = _r4.get("sku")
                _cum3[_sk4] = int(_r4.get("q") or 0)
                _agg3[_sk4] = _r4
        _o_ctxs = [{"sku": sk, "channel": channel,
                    "order": {"quantity": _qty.get(sk, 0),
                              "open_qty": _cum3.get(sk, 0),
                              "total_amount": float((_agg3.get(sk) or {}).get("amt") or 0),
                              "max_quantity": int((_agg3.get(sk) or {}).get("mx") or 0),
                              "min_quantity": int((_agg3.get(sk) or {}).get("mn") or 0),
                              "line_count": int((_agg3.get(sk) or {}).get("lc") or 0),
                              "unit_price": float((_agg3.get(sk) or {}).get("up") or 0)},
                    "order_qty": _cum3.get(sk, 0),
                    "available_stock": _inv_map.get(sk, 0),
                    "inv": {"available_qty": _inv_map.get(sk, 0), "safety_qty": 0, "in_transit_qty": 0}}
                   for sk in _skus]
        if _o_ctxs:
            evaluate_many("order.created", _o_ctxs, channel, load_rules_for("order.created", channel))
    except Exception as _e:
        try_err('orders', 'restore 联动评估降级', _e)


def _skus_of(ids):
    """按渠道分组的 SKU 集合 —— 返回 {channel: [sku]}

    2026-11-06 审计 P1-20: 原实现只取 sku 丢掉 channel, 下游 close_orphan_for_skus 的
    channel 参数走默认值 "jd" → 删 other 渠道的订单会把 **jd 渠道同 SKU 的告警**拿去重评估,
    other 的告警反而没被关(跨渠道误关 + 该关的没关)。批量操作可能跨渠道, 必须分组。
    """
    if not ids:
        return {}
    _ph = ",".join(["%s"] * len(ids))
    try:
        rows = query("SELECT DISTINCT channel, sku FROM orders WHERE id IN (%s)" % _ph, ids)
        out = {}
        for r in rows:
            _ch = str(r.get("channel") or "")
            _sk = r.get("sku")
            if _ch and _sk:
                out.setdefault(_ch, []).append(_sk)
        return out
    except Exception:
        return {}


def _days_of(ids):
    """变更**之前**取受影响的 (channel, 业务日期) 集合(2026-11-02 S2-2)

    软删/永久删让这些行从物化里消失, 恢复让它们重新出现 —— 两种都要重建对应日期。
    必须先查后改: 删完再查, 行已经不可见, 就永远不知道该重建哪天。
    """
    if not ids:
        return []
    out = []
    for _i in range(0, len(ids), 2000):
        _b = ids[_i:_i + 2000]
        _ph = ",".join(["%s"] * len(_b))
        try:
            rows = query("SELECT DISTINCT channel, DATE(ordered_at) AS d FROM orders "
                         "WHERE id IN (%s)" % _ph, _b)
        except Exception as _e:
            try_err('orders', '取受影响日期降级', _e)
            continue
        for r in (rows or []):
            _ch = str(r.get("channel") or "")
            _d = str(r.get("d") or "")[:10]
            if _ch and _d:
                out.append((_ch, _d))
    return out


def _mark_days(pairs, reason="order_edit"):
    """按渠道分组登记重建(批量操作可能跨渠道, 各自有自己的窗口)"""
    _by = {}
    for _ch, _d in pairs or []:
        _by.setdefault(_ch, set()).add(_d)
    for _ch, _ds in _by.items():
        try:
            mark_agg_days(_ch, _ds, reason=reason)
        except Exception as _e:
            # 失败不影响接口结果: 每日 sweep 会用 synced_at 反查补标记
            try_err('orders', '订单变更后登记重建降级', _e)


def _close_orphan(by_ch):
    """删单联动(2026-10-15 通用化): SKU 级孤儿关闭——core/rules.close_orphan_for_skus(该 SKU active 告警→规则 event 重评估——自定义 alert_type 覆盖 + low_stock 无行特殊)

    2026-11-06 审计 P1-20: 入参改为 {channel: [sku]}, 逐渠道调用 —— 见 _skus_of 注释。
    """
    try:
        from core.rules import close_orphan_for_skus
        for _ch, _sks in (by_ch or {}).items():
            close_orphan_for_skus(_sks, channel=_ch)
    except Exception as _e:
        try_err('orders', '删单联动孤儿关闭降级', _e)




from db import query, one, execute
from routes.common import ok, fail, traced, try_err
from routes.common import mark_agg_days

router = APIRouter(tags=["orders"])

_FIELDS = "o.id, o.order_no, o.sku, o.barcode, o.product_name, " \
          "COALESCE((SELECT pb.brand FROM products pb WHERE pb.channel=o.channel AND o.barcode != '' " \
          "AND pb.barcode=o.barcode LIMIT 1), " \
          "(SELECT ps.brand FROM products ps WHERE ps.channel=o.channel AND ps.sku=o.sku LIMIT 1)) AS brand, " \
          "o.store, o.warehouse, o.quantity, o.unit_price, o.total_amount, o.order_status, " \
          "o.ordered_at, o.paid_at, o.platform, o.channel, o.deleted_at, o.ext_json"
_OJOIN = "FROM orders o"


@router.get("/orders")
@traced
def list_orders(channel: str = "jd", page: int = 1, page_size: int = 30,
                search: str = "", status: str = "", include_deleted: int = 0):
    if include_deleted:
        where = "o.channel=%s"
    else:
        where = "o.channel=%s AND (o.deleted_at IS NULL OR o.deleted_at='')"
    params = [channel]
    if search:
        where += " AND (o.sku LIKE %s OR o.product_name LIKE %s OR o.order_no LIKE %s OR o.barcode LIKE %s)"
        params += ["%%%s%%" % search] * 4
    if status:
        where += " AND o.order_status=%s"
        params.append(status)
    # 2026-10-27 订单页提速: 短 TTL 共享表缓存(5s, key 含全参数)——冷请求消峰(进入页面/切回不再每次 1-8s),
    # 热态 <0.1s; invalidate("orders") 打墓碑 → 导入/删单等写操作即时失效, 实时性不损
    _key = "orders|%s|%s|%s|%s|%s" % (channel, page, search, status, page_size)
    return _cache_get(_key, 5, lambda: _build_orders(where, params, page, page_size, include_deleted))

def _build_orders(where, params, page, page_size, include_deleted):
    total = one("SELECT COUNT(*) AS c %s WHERE %s" % (_OJOIN, where), params) or {}
    total = int(total.get("c") or 0)
    if page > 0 and page_size > 0:
        rows = query("SELECT %s %s WHERE %s ORDER BY o.id DESC LIMIT %s OFFSET %s"
                     % (_FIELDS, _OJOIN, where, page_size, (page - 1) * page_size), params)
        return ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    rows = query("SELECT %s %s WHERE %s ORDER BY o.id DESC" % (_FIELDS, _OJOIN, where), params)
    return ok(rows)


@router.delete("/orders/{oid}")
def soft_delete_order(oid: int):
    """软删除(前端 5s 撤销窗口, 回收站可恢复)"""
    _skus = _skus_of([oid])
    # 2026-11-06 P2-8: 与批量删除对齐 —— 登记物化重建日期。
    #   原来这里只 invalidate("orders"): agg 有 60s 窗口自愈, 但**快照**(7/14/28 日销)要等
    #   每日对账才修正, 这段时间日销偏大(删掉的单仍计入)。批量删/恢复/永久删早已登记,
    #   只有这个单条入口漏了。日期必须在 UPDATE **之前**取 —— _days_of 依赖 deleted_at=''。
    _touch_days = _days_of([oid])
    execute("UPDATE orders SET deleted_at=NOW() WHERE id=%s", [oid])
    _mark_days(_touch_days, reason="order_delete")
    from routes.analysis_cache import invalidate
    invalidate("orders")
    _close_orphan(_skus)
    return ok({})


@router.post("/orders/batch")
@traced
async def orders_batch(request: Request):
    """批量操作: {action: delete|restore|permanent-delete, ids: []}"""
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('orders', '静默降级', _e)
    action = d.get("action") or ""
    ids = [x for x in (d.get("ids") or []) if str(x).isdigit()]
    if action not in ("delete", "restore", "permanent-delete") or not ids:
        return fail("参数无效: 需 action 与 ids")
    _del_skus = _skus_of(ids) if action in ("delete", "permanent-delete") else []
    # 2026-11-02 S2-2: 三种动作都会改变物化(软删=消失 / 恢复=重现 / 永久删=消失),
    # 受影响日期必须在动数据之前取出来
    _touch_days = _days_of(ids)
    # 分批(5000/批)——防大批量单 SQL IN 过大/超时(10万级也稳定)
    for _i in range(0, len(ids), 5000):
        _batch = ids[_i:_i + 5000]
        _ph = ",".join(["%s"] * len(_batch))
        if action == "delete":
            execute("UPDATE orders SET deleted_at=NOW() WHERE id IN (%s)" % _ph, _batch)
        elif action == "restore":
            execute("UPDATE orders SET deleted_at='' WHERE id IN (%s)" % _ph, _batch)
        else:
            execute("DELETE FROM orders WHERE id IN (%s)" % _ph, _batch)
    # 先入队再失效: invalidate("orders") 会立刻起后台线程消费队列, 顺序反了线程会读到空队列
    _mark_days(_touch_days, reason="order_" + action)
    from routes.analysis_cache import invalidate
    invalidate("orders")
    if action == "restore":
        _evaluate_restored(ids)
    elif _del_skus:
        _close_orphan(_del_skus)
    return ok({"updated": len(ids)})


@router.post("/orders/{oid}/restore")
@traced
def restore_order(oid: int):
    _tdays = _days_of([oid])
    execute("UPDATE orders SET deleted_at='' WHERE id=%s", [oid])
    _mark_days(_tdays, reason="order_restore")
    from routes.analysis_cache import invalidate
    invalidate("orders")
    _evaluate_restored([oid])
    return ok({})


@router.post("/orders/{oid}/permanent-delete")
@traced
def permanent_delete_order(oid: int):
    _skus = _skus_of([oid])
    _tdays = _days_of([oid])
    execute("DELETE FROM orders WHERE id=%s", [oid])
    _mark_days(_tdays, reason="order_delete")
    from routes.analysis_cache import invalidate
    invalidate("orders")
    _close_orphan(_skus)
    return ok({})
