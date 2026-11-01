"""自有仓(集货仓)健康计算模块(2026-11-01 新增)

为什么单独一个口径 —— 自有仓是**供给侧**, 不产生销售:
    库存卡 own_items 的 ds 走 pick_ds(逐仓日销), 而 daily_sales_snapshot 只记平台仓
    (西安仓/成都仓/…), 自有仓名「集货仓」在其中无行 → ds=0 → 动态安全线=0 →
    `0 > 0` 恒假 → 低库存/断货口径在自有仓维度**结构性不可能触发**(不是 bug, 是口径错配)。
    因此自有仓要用「调拨/采购」这套供给侧口径, 见下三类:

  own_transfer_gap  调拨覆盖不足 (error)
      触发: 下游(平台仓)本期真实调拨需求 > 自有仓可用 + 在途 + 采购在途
      含义: 下游要货已在路上, 自有仓备不出 → 会真的缺给 C 仓
      联动: replenishment.py 的 B仓缺口 / purchase_calc 的采购建议

  own_overstock     自有仓积压 (warning)
      触发: 自有仓可用 ÷ 下游日均消耗 > overstock_days(默认 90)
      含义: 压资金 / 占库位, 但没有断供风险 → 只到 warning

  own_expiry_risk   效期风险 (error)
      触发: 自有仓最早到期批次的有效期 < 自有用完这批所需天数 + 安全缓冲
      含义: 会过期报废(直接损失), 且可调拨消化 → error
      门槛: products.expiry_managed=1 才判定(不管效期的品类不报)

数据来源全部是现有表, 不新增采集:
  inventory(按仓库/批次聚合) / batches(exp_date) / products(expiry_managed, supplier_code)
  / replenishment_config(周期参数) / biz.sales(下游日销)
"""
import json
from datetime import datetime, timezone

from db import query, one
from routes.common import try_err


def _cfg(channel):
    """补货配置(与补货/采购同源) + 规则 params 覆盖 → dict"""
    cfg = {}
    try:
        for r in query("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel]):
            cfg[r.get("key")] = r.get("value")
    except Exception as _e:
        try_err("own_health", "补货配置读取降级", _e)
    # 规则 params 优先(告警规则页可调) —— 复用 _rule_params 的既有语义
    try:
        from routes.dashboard_stock_risk import _rule_params
        params = _rule_params("own_transfer_gap", channel) or {}
        for k, v in params.items():
            cfg[k] = v
    except Exception as _e:
        try_err("own_health", "规则参数读取降级", _e)
    return cfg


def _cf(cfg, key, default):
    try:
        return float(cfg.get(key, default))
    except Exception:
        return float(default)


def own_health(channel, full: int = 0, limit: int = 10):
    """自有仓三类告警: 调拨覆盖 / 积压 / 效期

    返回 {
      items: [{sku, barcode, product_name, warehouse, alert_type, level, title, description,
               available_qty, need_qty, gap_qty, cover_days, days_to_expiry, batch_no, exp_date}],
      total, transferGap, overstock, expiryRisk,   # 三类计数
      critical, warning,                            # error / warning 计数
      limits: {overstock_days, expiry_buffer_days, ...}   # 回传生效阈值(前端展示口径)
    }
    """
    from biz.sales import get_sku_sales_stats, WH_C, rolling_predict

    cfg = _cfg(channel)
    # 阈值(可被规则 params 覆盖): 积压天数 / 效期缓冲天数 / 下游覆盖要求倍数
    overstock_days = _cf(cfg, "overstock_days", 90)
    expiry_buffer_days = _cf(cfg, "expiry_buffer_days", 15)
    transfer_cover_mult = _cf(cfg, "transfer_cover_mult", 1.0)  # 覆盖要求倍数(1=刚好够)
    # 下游调拨周期: 与补货同源(b_to_c_days = B→C 调拨前置期), 用于把"日销"换算成"本期要货"
    b2c_days = _cf(cfg, "mode_bbcc_b_to_c_days", 0) or _cf(cfg, "b_to_c_days", 3)
    if b2c_days <= 0:
        b2c_days = 3

    # ── 自有仓库存(逐 SKU×仓 汇总; own 类型仓可能多行/多批次) ──
    own = query(
        "SELECT sku, IFNULL(barcode,'') AS barcode, warehouse, "
        "SUM(IFNULL(available_qty,0)) AS avail, "
        "SUM(IFNULL(in_transit_qty,0)) AS transit, "
        "MIN(IFNULL(warehouse,'')) AS wh_min "
        "FROM inventory WHERE channel=%s AND warehouse_type='own' "
        "GROUP BY sku, IFNULL(barcode,''), warehouse", [channel])
    own_by_sku = {}
    for r in own:
        sku = str(r.get("sku") or "")
        if not sku:
            continue
        st = own_by_sku.setdefault(sku, {"avail": 0, "transit": 0, "wh": "", "barcode": ""})
        st["avail"] += int(r.get("avail") or 0)
        st["transit"] += int(r.get("transit") or 0)
        if not st["wh"]:
            st["wh"] = str(r.get("warehouse") or "")
        if not st["barcode"]:
            st["barcode"] = str(r.get("barcode") or "")
    if not own_by_sku:
        return {"items": [], "total": 0, "transferGap": 0, "overstock": 0, "expiryRisk": 0,
                "critical": 0, "warning": 0,
                "limits": {"overstock_days": overstock_days,
                           "expiry_buffer_days": expiry_buffer_days,
                           "b2c_days": b2c_days}}

    skus = list(own_by_sku.keys())
    pmap = {}
    for i in range(0, len(skus), 300):
        chunk = skus[i:i + 300]
        ph = ",".join(["%s"] * len(chunk))
        for r in query(
                "SELECT sku, product_name, barcode, supplier_code, IFNULL(expiry_managed,'') AS expiry_managed "
                "FROM products WHERE channel=%s AND sku IN (" + ph + ") "
                "AND (deleted_at IS NULL OR deleted_at='')", [channel] + chunk):
            pmap[str(r.get("sku") or "")] = r

    # ── 下游(平台仓)日销 —— 自有仓的"消耗"= 调拨给平台仓的量 ──
    ds_map = {}
    try:
        rows = get_sku_sales_stats(channel, wh=WH_C, days=28) or {}
        for _k, r in rows.items():
            sku = str(r.get("sku") or "")
            ds = rolling_predict(float(r.get("ds7") or 0), float(r.get("ds14") or 0),
                                 float(r.get("ds28") or 0))
            if sku and ds > 0:
                ds_map[sku] = ds
    except Exception as _e:
        try_err("own_health", "下游日销读取降级(积压/覆盖判定将不出数)", _e)

    # ── 采购在途(供应商已发未到) —— 覆盖缺口时可抵扣 ──
    po_transit = {}
    try:
        for r in query(
                "SELECT sku, IFNULL(SUM(IFNULL(qty,0)-IFNULL(received_qty,0)),0) AS t "
                "FROM purchase_orders WHERE channel=%s AND status IN ('pending','in_transit','partial') "
                "GROUP BY sku", [channel]):
            po_transit[str(r.get("sku") or "")] = int(r.get("t") or 0)
    except Exception as _e:
        try_err("own_health", "采购在途读取降级(按 0 处理)", _e)

    # ── 自有仓批次效期(仅 expiry_managed 管效期的品类) ──
    # 注意: products.expiry_managed 是 **字符串 'yes'/'no'**(实测 874 yes / 126 no),
    #   不是 0/1 —— 曾写 int(...) 直接 ValueError。这里统一按真值集判定。
    _yes = ("yes", "1", "true", "y", "on")
    exp_map = {}
    _expiry_skus = [s for s in skus
                    if str((pmap.get(s) or {}).get("expiry_managed") or "").strip().lower() in _yes]
    if _expiry_skus:
        try:
            for i in range(0, len(_expiry_skus), 300):
                chunk = _expiry_skus[i:i + 300]
                ph = ",".join(["%s"] * len(chunk))
                # 注意: batches 表**无 batch_no 列**(实测字段: id/sku/warehouse/warehouse_type/
                #   channel/prod_date/exp_date/qty/barcode) —— 批次标识用 id, 展示用 id+仓库。
                for r in query(
                        "SELECT b.id AS batch_id, b.sku, b.warehouse, b.exp_date, IFNULL(b.qty,0) AS qty "
                        "FROM batches b JOIN inventory inv ON inv.sku=b.sku AND inv.channel=b.channel "
                        "WHERE b.channel=%s AND inv.warehouse_type='own' "
                        "AND b.exp_date IS NOT NULL AND b.exp_date<>'' "
                        "AND b.sku IN (" + ph + ") ORDER BY b.exp_date ASC", [channel] + chunk):
                    sku = str(r.get("sku") or "")
                    if sku not in exp_map:  # 最早批次(已按 exp_date 升序)
                        exp_map[sku] = r
        except Exception as _e:
            try_err("own_health", "批次效期读取降级", _e)

    _today = datetime.now(timezone.utc).date()
    items = []

    for sku, st in own_by_sku.items():
        avail = st["avail"]
        transit = st["transit"]
        p = pmap.get(sku) or {}
        name = str(p.get("product_name") or sku)
        bc = str(p.get("barcode") or st["barcode"] or "")
        wh = st["wh"] or "自有仓"
        ds = ds_map.get(sku, 0.0)
        po = po_transit.get(sku, 0)

        # ---- ① 调拨覆盖不足: 下游本期要货 vs 自有可发(在仓+在途+采购在途) ----
        if ds > 0:
            need = ds * b2c_days * transfer_cover_mult      # 下游一个调拨周期要的量
            can = avail + transit + po                       # 自有仓能发出的量
            gap = need - can
            if gap > 0:
                items.append({
                    "sku": sku, "barcode": bc, "product_name": name, "warehouse": wh,
                    "alert_type": "own_transfer_gap", "level": "error",
                    "title": "%s 调拨覆盖不足" % name,
                    "description": "下游 %.0f/日×%g天需 %.0f 件, 自有可发 %.0f 件(在仓 %d/在途 %d/采购 %d), 缺 %.0f 件"
                                   % (ds, b2c_days, need, can, avail, transit, po, gap),
                    "available_qty": avail, "need_qty": round(need), "gap_qty": round(gap),
                    "cover_days": round(can / ds, 1) if ds > 0 else None,
                    "days_to_expiry": None, "batch_no": "", "exp_date": "",
                })

        # ---- ② 自有仓积压: 自有可用 ÷ 下游日耗 > overstock_days ----
        if ds > 0 and avail > 0:
            cover = avail / ds
            if cover > overstock_days:
                items.append({
                    "sku": sku, "barcode": bc, "product_name": name, "warehouse": wh,
                    "alert_type": "own_overstock", "level": "warning",
                    "title": "%s 自有仓积压" % name,
                    "description": "自有可用 %d 件, 下游 %.1f/日, 可撑 %.0f 天(阈值 %g 天), 建议加速调拨"
                                   % (avail, ds, cover, overstock_days),
                    "available_qty": avail, "need_qty": 0, "gap_qty": 0,
                    "cover_days": round(cover, 1),
                    "days_to_expiry": None, "batch_no": "", "exp_date": "",
                })

        # ---- ③ 效期风险: 最早到期批次会在用完前过期 ----
        b = exp_map.get(sku)
        if b:
            try:
                _ed = str(b.get("exp_date") or "")[:10]
                exp_day = datetime.strptime(_ed, "%Y-%m-%d").date()
                left = (exp_day - _today).days
                # 自有仓这批货吃完需要多久: 优先按下游日耗(会调拨走), 无日耗则只按绝对天数判
                # 2026-11-01 修正: 2 条业务判据 + 1 条数据卫生判据
                #   ① left < buffer           —— 已经/即将到期, 必须处置(调拨或报损)
                #   ② left < consume+buffer   —— 按当前消耗速度吃不完全部可用量 → 剩余会过期
                #   ③ 已过期(exp_date<今天)   —— 属数据卫生问题(呆滞/未报损), 同样报出但不与①重复计数
                # 说明: 原实现只在 ① 上判 `avail>0`, 已过期批次因 left 为负必然命中 ①,
                #   数量取决于"最早批次是否已过期" —— 实测最早批次 2026-10-17(已过期 15 天), 符合预期。
                consume_days = (avail / ds) if ds > 0 else None
                expired = left < 0
                hit = expired or (left < expiry_buffer_days) or \
                      (consume_days is not None and left < consume_days + expiry_buffer_days)
                if hit and avail > 0:
                    items.append({
                        "sku": sku, "barcode": bc, "product_name": name, "warehouse": wh,
                        "alert_type": "own_expiry_risk", "level": "error",
                        "title": "%s %s" % (name, "批次已过期" if expired else "效期风险"),
                        "description": ("批次 #%s 已过期 %d 天(到期 %s), 自有可用 %d 件待报损/急调"
                                        if expired else
                                        "批次 #%s 剩余 %d 天到期(缓冲 %g 天), 自有可用 %d 件%s")
                                       % ((str(b.get("batch_id") or "-"), -left, _ed, avail)
                                          if expired else
                                          (str(b.get("batch_id") or "-"), left, expiry_buffer_days, avail,
                                           ("、可撑 %.0f 天" % consume_days) if consume_days is not None else "")),
                        "available_qty": avail, "need_qty": 0, "gap_qty": 0,
                        "cover_days": round(consume_days, 1) if consume_days is not None else None,
                        "days_to_expiry": left, "batch_no": ("#%s" % b.get("batch_id")) if b.get("batch_id") else "",
                        "exp_date": _ed,
                    })
            except Exception as _e:
                try_err("own_health", "效期判定跳过(%s)" % sku, _e)

    # 排序: error 先, 同级按"最紧迫"排 —— 缺口大的/到期近的/积压久的
    def _key(x):
        lv = 0 if x.get("level") == "error" else 1
        if x["alert_type"] == "own_transfer_gap":
            urg = -(x.get("gap_qty") or 0)
        elif x["alert_type"] == "own_expiry_risk":
            urg = x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 9999
        else:
            urg = -(x.get("cover_days") or 0)
        return (lv, urg)

    items.sort(key=_key)
    tg = sum(1 for i in items if i["alert_type"] == "own_transfer_gap")
    os_ = sum(1 for i in items if i["alert_type"] == "own_overstock")
    er = sum(1 for i in items if i["alert_type"] == "own_expiry_risk")
    crit = sum(1 for i in items if i.get("level") == "error")
    warn = sum(1 for i in items if i.get("level") == "warning")
    return {
        "items": items if full else items[:limit],
        "total": len(items), "transferGap": tg, "overstock": os_, "expiryRisk": er,
        "critical": crit, "warning": warn,
        "limits": {"overstock_days": overstock_days, "expiry_buffer_days": expiry_buffer_days,
                   "b2c_days": b2c_days},
    }
