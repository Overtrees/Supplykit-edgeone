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
from core.clock import now_cn


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


def _compute(channel, debug: int = 0):
    """自有仓三类告警的**全量计算**(唯一公式所在)。

    2026-11-07 P2: 从 `own_health()` 里拆出来 —— 物化重建要算它、现算兜底也要算它,
    两处必须是同一份代码。读侧 `own_health()` 只负责选择"读物化还是现算"。

    返回 {
      items: [{sku, barcode, product_name, warehouse, alert_type, level, title, description,
               available_qty, need_qty, gap_qty, cover_days, days_to_expiry, batch_no, exp_date}],
      total, transferGap, overstock, expiryRisk,   # 三类计数
      critical, warning,                            # error / warning 计数
      limits: {overstock_days, expiry_buffer_days, ...}   # 回传生效阈值(前端展示口径)
    }
    """
    from biz.sales import get_sku_sales_stats, WH_C, rolling_predict, pick_ds

    cfg = _cfg(channel)
    # 阈值(可被规则 params 覆盖): 积压天数 / 效期缓冲天数 / 下游覆盖要求倍数
    overstock_days = _cf(cfg, "overstock_days", 90)
    expiry_buffer_days = _cf(cfg, "expiry_buffer_days", 15)
    transfer_cover_mult = _cf(cfg, "transfer_cover_mult", 1.0)  # 覆盖要求倍数(1=刚好够)
    # 下游调拨周期: **与规则引擎 _lit_bbcc 完全同口径** —— b2c + c_safety
    #   (2026-11-01 对齐: 原只取 b_to_c_days=3, 而引擎用 b_to_c+c_safety=6,
    #    同一缺口会算出两个数 → 看板卡与规则告警对不上。这里改为同一公式。)
    #   mode_ 前缀键优先(补货配置的模式双线), 平铺键兜底
    #   2026-11-06 S3: 公式收敛到 core.metrics.periods(唯一真源) —— 与规则引擎/补货建议同源
    from core import metrics as _M
    b2c_days = float(_M.periods(cfg)["b2c"])
    if b2c_days <= 0:
        b2c_days = 3.0

    # ── 自有仓库存(逐 SKU×69码×仓; own 仓可能多批次多行) ──
    # 键 = (sku, 69码, 仓) —— 与 inventory 的业务键同粒度(uq_inventory_biz: sku+barcode+
    #   warehouse+channel+warehouse_type)。**不再压扁到 SKU**: 压扁后多码 SKU 的可用量被合并、
    #   仓名只留第一个, 告警指向的码/仓与实际行对不上(线上自有仓有 6 个 SKU 多码)。
    # 在途口径(2026-11-02 用户定案): **采购在途 = 库存导入(自有仓维度)填写的 in_transit_qty**,
    #   即这里 SUM 出来的 transit。不再从 purchase_orders 反推 —— 那张表没有 qty/received_qty
    #   列(每次查询 Unknown column, 恒按 0 算), 且导入值已是权威, 再推一遍就是双计。
    own = query(
        "SELECT sku, IFNULL(barcode,'') AS barcode, IFNULL(warehouse,'') AS warehouse, "
        "SUM(IFNULL(available_qty,0)) AS avail, "
        "SUM(IFNULL(in_transit_qty,0)) AS transit "
        "FROM inventory WHERE channel=%s AND warehouse_type='own' "
        "GROUP BY sku, IFNULL(barcode,''), IFNULL(warehouse,'')", [channel])
    own_rows = {}
    for r in own:
        sku = str(r.get("sku") or "")
        if not sku:
            continue
        _k = (sku, str(r.get("barcode") or ""), str(r.get("warehouse") or ""))
        own_rows[_k] = {"avail": int(r.get("avail") or 0), "transit": int(r.get("transit") or 0)}
    if not own_rows:
        return {"items": [], "total": 0, "transferGap": 0, "overstock": 0, "expiryRisk": 0,
                "critical": 0, "warning": 0,
                "limits": {"overstock_days": overstock_days,
                           "expiry_buffer_days": expiry_buffer_days,
                           "b2c_days": b2c_days}}

    skus = list({k[0] for k in own_rows})
    pmap = {}
    _bc_ds, _agg_ds = {}, {}
    exp_map = {}
    _yes = ("yes", "1", "true", "y", "on")

    # ── 三块取数并行(2026-11-06) ─────────────────────────────────────────────
    # 原来串行: products → 下游日销 → 批次效期, 三条互不依赖的查询排队等。
    # 线上 aux 冷算 7s 里, 自有仓这一路独占 9 条 SQL、是最慢分支(其余两路 3~6 条)。
    # 并行的 3 条连接 + aux 外层 3 路 = 峰值 6 < DB_POOL_LIMIT(10), 不挤占其它请求。
    # 每块自带 try + 降级(与原来一致: 单块失败只少一档数据, 不拖垮整卡)。
    def _load_products():
        # 2026-11-02 冷算: 原按 800 分片走 IN 列表(1000 SKU → 2 次往返)。products 单渠道实测
        #   仅 1000 行, 全量取回比"拼 IN + 分片循环"更省(一次往返 + 更小的 SQL 文本)。
        # 2026-11-06 只取真正被读的两列(原取 5 列: barcode/supplier_code 在本函数**从未被读**)。
        _m = {}
        for r in query(
                "SELECT sku, product_name, IFNULL(expiry_managed,'') AS expiry_managed "
                "FROM products WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')",
                [channel]):
            _m[str(r.get("sku") or "")] = r
        return _m

    def _load_ds():
        """下游(平台仓)日销 —— 自有仓的"消耗"= 调拨给平台仓的量

        键与库存行同粒度: ①该 69码(平台仓 __C__ 的按码行) ②该 SKU 合计码(无码行/该码无记录)。
        取值降级规则走 biz.sales.pick_ds(单一真源: 只有"码"可降级, 维度不降级)。
        空码行必须读合计行(barcode=''), 不能拿档案码去借该码的销量。
        一次取"合计行 + 按码行"(barcode="all"): 分两次查只是多一次往返(实测 1.5s vs 2.8s)。
        """
        _bc, _agg = {}, {}
        try:
            # 2026-11-06: 只取 rolling_predict 用到的三列(原 14 列, 2 万行白传 8 列 ≈ 2.5MB)
            rows = get_sku_sales_stats(channel, wh=WH_C, days=28, barcode="all",
                                      fields=("ds7", "ds14", "ds28")) or {}
            for _k, r in rows.items():
                _s = str(r.get("sku") or "")
                if not _s:
                    continue
                _ds = rolling_predict(float(r.get("ds7") or 0), float(r.get("ds14") or 0),
                                      float(r.get("ds28") or 0))
                _bcv = str(r.get("barcode") or "")
                if _bcv:
                    _bc[(_s, _bcv, WH_C)] = _ds
                else:
                    _agg[(_s, WH_C)] = _ds
        except Exception as _e:
            try_err("own_health", "下游日销读取降级(积压/覆盖判定将不出数)", _e)
        return _bc, _agg

    def _load_batches():
        """自有仓批次效期(仅 expiry_managed 管效期的品类真正会被用到)

        注意: products.expiry_managed 是**字符串 'yes'/'no'**(实测 874 yes / 126 no),
          不是 0/1 —— 曾写 int(...) 直接 ValueError。这里统一按真值集判定。
        batches 表**无 batch_no 列**(id/sku/warehouse/warehouse_type/channel/prod_date/
          exp_date/qty/barcode) —— 批次标识用 id, 展示用 id+仓库。
        2026-11-06 只取判定要用的列(qty 在本函数未被读; 15,496 行 × 1 列白传)。
        """
        _m = {}
        try:
            for r in query(
                    "SELECT id AS batch_id, sku, IFNULL(barcode,'') AS barcode, "
                    "IFNULL(warehouse,'') AS warehouse, exp_date "
                    "FROM batches WHERE channel=%s AND warehouse_type='own' "
                    "AND exp_date IS NOT NULL AND exp_date<>'' "
                    "ORDER BY exp_date ASC", [channel]):
                # 2026-11-02 键对齐: 原实现 JOIN inventory 只按 sku+channel → 同 SKU 的
                #   其它码/其它自有仓批次也并进来, 再取"最早到期" → 报出的批次/到期日可能
                #   属于**另一个码或另一个仓**。现在按 (sku, 码, 仓) 分组, 由 _pick_batch
                #   逐级取: ①同码同仓 ②同仓(码缺失) ③该 SKU 最早 —— 只降"码"不跨仓。
                _m.setdefault(str(r.get("sku") or ""), []).append(r)
        except Exception as _e:
            try_err("own_health", "批次效期读取降级", _e)
        return _m

    # 2026-11-07 S0 回退嵌套并行(雪崩根因):
    #   aux 外层已开 3 路, 这里再开 3 路 → 单个 aux 请求峰值占用 3+3=6 条连接;
    #   再叠上 summary 的 3 路 = 9, 而实例连接池上限只有 10 (DB_POOL_LIMIT)。
    #   任何两个重接口同时进来就把池打满, 其余请求排队 20s → 全线 30~40s。
    #   实测证据: aux 单跑 4.5s, 与 stock-risk 并发时 34s / 35.8s (quality_logs slow_request)。
    #   单接口"并行变快"换来的是"整站不可用", 与 §15.8 四维(可靠性)冲突 —— 退回串行。
    #   这 3 条查询单条 0.2~0.9s, 串行只多约 1s, 但不再挤占别人的连接。
    # 2026-11-07 P0.1: 三条取数恢复并行 —— 但这次有**全局预算**兜底。
    #   11-07 S0 回退并行的理由是"aux 外层 3 路 + 这里 3 路 = 6 连接, 两接口并发就打满池";
    #   现在 `db.heavy` 已把重计算全局限制在 3 个名额(HEAVY_CONCURRENCY), aux 占 1 个,
    #   stock-risk 想同时进来只能等名额或返回旧值 —— 不会再出现"两个请求各吃 6 条连接"。
    #   线上实测: own-health 单独 2.07s(串行三条), 是 aux 切物化后的新天花板。
    from concurrent.futures import ThreadPoolExecutor as _TPE
    import time as _t
    _t0 = _t.time()
    with _TPE(max_workers=3) as _ex:
        _fp = _ex.submit(_load_products)
        _fd = _ex.submit(_load_ds)
        _fb = _ex.submit(_load_batches)
        try:
            pmap = _fp.result()
        except Exception as _e:
            try_err("own_health", "商品档案加载降级", _e)
            pmap = {}
        _t1 = _t.time()
        try:
            _bc_ds, _agg_ds = _fd.result()
        except Exception as _e:
            try_err("own_health", "下游日销加载降级", _e)
            _bc_ds, _agg_ds = {}, {}
        _t2 = _t.time()
        try:
            exp_map = _fb.result()
        except Exception as _e:
            try_err("own_health", "批次效期加载降级", _e)
            exp_map = {}
        _t3 = _t.time()

    # 没有管效期的 SKU 就丢掉批次结果(与原来"不查"等价, 只是这次查了才发现用不上)
    _expiry_skus = [s for s in skus
                    if str((pmap.get(s) or {}).get("expiry_managed") or "").strip().lower() in _yes]
    if not _expiry_skus:
        exp_map = {}

    def _pick_batch(_sku, _bc, _wh):
        """该库存行对应的最早到期批次: 精确(码+仓) → 同仓(码缺) → 该 SKU 最早

        只降"码"不跨仓: 多码是数据缺失(可降级), 换仓是换了业务对象(不能降级)。
        """
        _rows = exp_map.get(_sku) or []
        for _r in _rows:
            if str(_r.get("barcode") or "") == _bc and str(_r.get("warehouse") or "") == _wh:
                return _r
        for _r in _rows:
            if str(_r.get("warehouse") or "") == _wh:
                return _r
        return _rows[0] if _rows else None

    _today = now_cn().date()
    items = []

    for (sku, bc, wh_raw), st in own_rows.items():
        avail = st["avail"]
        transit = st["transit"]          # 采购在途 = 库存导入(自有仓)填写的 in_transit_qty
        p = pmap.get(sku) or {}
        name = str(p.get("product_name") or sku)
        wh = wh_raw or "自有仓"
        ds = pick_ds(_bc_ds, _agg_ds, sku, bc, WH_C)

        # ---- ① 调拨覆盖不足: 下游本期要货 vs 自有可发(在仓 + 采购在途) ----
        if ds > 0:
            need = ds * b2c_days * transfer_cover_mult      # 下游一个调拨周期要的量
            can = avail + transit                            # 自有仓能发出的量
            gap = need - can
            # 0.5 件容差: 需量是 ds×天数 的连续值, 与整数可发量比较会出 0.0001 级的伪缺口
            #   (实测出现过 "需 34 件/可发 33 件/缺 0 件" 这种自相矛盾的行)
            if gap > 0.5:
                items.append({
                    "sku": sku, "barcode": bc, "product_name": name, "warehouse": wh,
                    "alert_type": "own_transfer_gap", "level": "error",
                    "title": "%s 调拨覆盖不足" % name,
                    "description": "下游 %.0f/日×%g天需 %.0f 件, 自有可发 %.0f 件(在仓 %d/采购在途 %d), 缺 %.0f 件"
                                   % (ds, b2c_days, need, can, avail, transit, gap),
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

        # ---- ③ 效期风险: 该码该仓的最早到期批次会在用完前过期 ----
        b = _pick_batch(sku, bc, wh_raw)
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
    _t4 = _t.time()
    def _key(x):
        lv = 0 if x.get("level") == "error" else 1
        if x["alert_type"] == "own_transfer_gap":
            urg = -(x.get("gap_qty") or 0)
        elif x["alert_type"] == "own_expiry_risk":
            urg = x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 9999
        else:
            urg = -(x.get("cover_days") or 0)
        # 2026-11-02 确定性定序: 原来只按(级别, 紧急度), 同分项(常见于 gap 相同/效期同日)
        #   的先后由 SQL 返回顺序决定 → 同一份数据不同请求条序不同, 且 full=0 时 [:limit]
        #   截断会取到不同成员。补 (sku, 码, 仓) 作稳定次级键, 数值与集合不变。
        return (lv, urg, str(x.get("sku") or ""), str(x.get("barcode") or ""),
                str(x.get("warehouse") or ""))

    items.sort(key=_key)
    tg = sum(1 for i in items if i["alert_type"] == "own_transfer_gap")
    os_ = sum(1 for i in items if i["alert_type"] == "own_overstock")
    er = sum(1 for i in items if i["alert_type"] == "own_expiry_risk")
    crit = sum(1 for i in items if i.get("level") == "error")
    warn = sum(1 for i in items if i.get("level") == "warning")
    # 2026-11-07 P0.1 分段埋点(仅 debug=1): 串行时 2.07s、并行后仍 2.04s ——
    #   必须先看清是"哪条查询钉住了"还是"1 万行 × 3 类判定的 Python 组装"吃掉了。
    #   实测(2026-11-07 P2): products 0.07 / ds 0.59 / batches 0.59 / 组装 0.11 / 收尾 0.02,
    #   另加 own_rows 那条 GROUP BY(未埋点)约 0.9s —— 结论是**三次取数**, 不是 Python。
    if debug:
        try:
            from db import execute as _et
            _t5 = _t.time()
            _et("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('perf_debug','info',%s,%s,'own_health')",
                ("own_health 计时: products %.2fs ds %.2fs batches %.2fs 组装 %.2fs 收尾 %.2fs"
                 % (_t1 - _t0, _t2 - _t0, _t3 - _t0, _t4 - _t3, _t5 - _t4),
                 "own_rows=%d items=%d" % (len(own_rows), len(items))))
        except Exception:
            pass
    return {
        "items": items,
        "total": len(items), "transferGap": tg, "overstock": os_, "expiryRisk": er,
        "critical": crit, "warning": warn,
        "limits": {"overstock_days": overstock_days, "expiry_buffer_days": expiry_buffer_days,
                   "b2c_days": b2c_days},
    }


def own_health(channel, full: int = 0, limit: int = 10, offset: int = 0,
               per_type: int = 0, only: str = "", debug: int = 0):
    """自有仓三类告警: 调拨覆盖 / 积压 / 效期（读模型优先）

    2026-11-07 P2: 先读 `own_health_snapshot`（重建时算好、按 seq 排好）。
      命中 → 一页只读 200 行 + head 里的 9 个计数, 实测 1.92s → 0.4s；
      未命中（fp 变了/从未构建/上次校验没过）→ **现算**（今天的行为，慢但正确）并起后台重建。

    为什么不在这里缓存整包: 1.4 万条 / 5.9MB 曾塞进共享表 → 超 TiDB 单事务 6MB 上限 →
    永远写不进去 → 每个请求重算两遍。条目的"全量可见"由分页保证，不是由整包保证。
    """
    if not debug:
        try:
            from core import own_store as _OS
            _p = _OS.page_of(channel, full=full, limit=limit, offset=offset,
                             per_type=per_type, only=only)
            if _p is not None:
                return _p
            _OS.ensure_async(channel)
        except Exception as _e:
            try_err("own_health", "读模型读取降级(退回现算)", _e)
    d = _compute(channel, debug=debug)
    d["items"] = _page(d["items"], full=full, limit=limit, offset=offset,
                       per_type=per_type, only=only)
    return d


def _page(items, full=0, limit=10, offset=0, per_type=0, only=""):
    """把排序后的全量条目裁成**有界**的一段(2026-11-06 结构改造)

    为什么必须有这个函数: 这张卡的条目数随规模线性增长, 线上实测 13,615 条 / 5.9MB。
    它原来被整包塞进 /dashboard/aux 的响应里(full=1), 于是:
      · 响应 5.6MB, 浏览器解析 + React 渲染 1.3 万行;
      · 写共享表缓存时超出 TiDB 单条事务 6MB 上限 → 缓存**永远存不进去**(cache_error 6 次),
        每个请求都得重算一遍 —— 这才是"切页面就加载不出"的直接原因;
      · 重算又反过来拖慢同实例上所有其它接口。
    数据的"全量可见"没有取消: 弹窗改成按页拉(见路由 /dashboard/own-health),
    翻到底就是全量。聚合接口只发"够卡片渲染"的一小段 —— 这是把大对象从热路径上拿掉,
    不是把行藏起来。
    """
    if only:
        items = [x for x in items if x.get("alert_type") == only]
        if full and not per_type:
            return items[int(offset or 0):int(offset or 0) + int(limit or 10)]
    if full:
        if per_type:
            return _per_type(items, int(per_type))
        _o = max(0, int(offset or 0))
        return items[_o:_o + max(1, int(limit or 10))]
    if per_type:
        return _per_type(items, int(per_type))
    return items[:max(1, int(limit or 10))]


def _per_type(items, n):
    """每类各取前 n 条(卡片要"三类都露头", 不能整段截断)"""
    _c = {}
    out = []
    for x in items:
        _k = x.get("alert_type")
        _i = _c.get(_k, 0)
        if _i >= n:
            continue
        _c[_k] = _i + 1
        out.append(x)
    return out
