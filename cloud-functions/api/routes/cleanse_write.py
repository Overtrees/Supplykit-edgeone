"""cleansing 写入模块(2026-10-11 拆分——导入写入逻辑独立; cleansing.py 减负)
函数: _finalize_import/_adjust_inventory(库存联动+规则评估) / _write_rows/_write_batch/_sync_batches(批量插入/批次效期)
依赖: db/core.rules/cleansing 清洗枚举——本模块独立
"""
from datetime import datetime, timezone

from db import query, one, execute, executemany
from routes.common import try_err



def _finalize_import(target, channel, mp, cleaned_all, success, failed, elapsed):
    """接力导入最后一步: 库存联动 + 规则评估 + 汇总消息(与原 execute-async 同步版同逻辑)"""
    import time as _t
    adjusted = 0
    evaluated = 0
    # 库存联动(A3): inbound+ / outbound- / order(采购单+、销售-)
    try:
        if target in ("inbound", "outbound", "order"):
            # 2026-10-29 架构定案(用户业务模型): 所有数据源=ERP/平台导出的历史快照, 非实时事件流
            # · 订单: 纯数据源(日销/GMV/规则), **不碰任何库存**(平台库存导出已含销售影响)
            # · 入库/出库: 自有仓台账 —— 改为【重算制】(幂等), 不再增量加减
            #   available = beginning_stock(期初, 库存导入写入) + Σ入库 − Σ出库
            #   增量制缺陷: 重复导入翻倍 / 滞后补录顺序错 / 无法对账
            if target in ("inbound", "outbound", "inventory"):
                try:
                    _skus_touch = sorted({c.get("sku") for c in cleaned_all if c.get("sku")})
                    if _skus_touch:
                        # 库存: 期初 + Σ流水(重算制); 批次: FIFO 重建(Σ批次 ≡ available)
                        _recalc_own_inventory(channel, _skus_touch)
                        _rebuild_own_batches(channel, _skus_touch)
                except Exception as _er:
                    try_err('cleansing', '自有仓重算降级', _er)
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    # 2026-10-29 数据新鲜度记录(导入完成 → UPSERT; 供界面"数据截至"提示)
    try:
        from db import execute as _ef
        _bizd = ""
        for _c in cleaned_all[:200]:
            for _k in ("ordered_at", "inbound_date", "outbound_date", "date", "arrived_time"):
                _v = str(_c.get(_k) or "")[:10]
                if _v and _v > _bizd:
                    _bizd = _v
        _ef("INSERT INTO data_freshness(doc_type, channel, last_import_at, last_biz_date, rows_imported) "
            "VALUES(%s,%s,NOW(),%s,%s) ON DUPLICATE KEY UPDATE last_import_at=NOW(), "
            "last_biz_date=VALUES(last_biz_date), rows_imported=rows_imported+VALUES(rows_imported)",
            [target, channel, _bizd, len(cleaned_all)])
    except Exception as _efx:
        try_err('cleansing', '新鲜度记录降级', _efx)
    # 规则引擎评估(批量)
    try:
        from core.rules import evaluate_many, load_rules_for
        if target == "order":
            seen = set()
            skus = [c.get("sku") for c in cleaned_all
                    if c.get("sku") and not (c.get("sku") in seen or seen.add(c.get("sku")))]
            inv_map = {}
            if skus:
                for _i in range(0, len(skus), 500):  # 500/片(曾 200——25 次往返慢)
                    _batch = skus[_i:_i + 200]
                    _ph = ",".join(["%s"] * len(_batch))
                    for r in query("SELECT sku, MAX(available_qty) AS avail FROM inventory "
                                   "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph),
                                   [channel] + _batch):
                        inv_map[r.get("sku")] = int(r.get("avail") or 0)
            # 2026-10-29 超卖口径: 补 order.total_qty(累计未删订单量) —— 单笔口径会系统性漏报
            _cum2 = {}
            _sku_all = [str(c.get("sku")) for c in cleaned_all if c.get("sku")]
            if _sku_all:
                for _i2 in range(0, len(_sku_all), 500):
                    _b2 = _sku_all[_i2:_i2 + 500]
                    _ph3 = ",".join(["%s"] * len(_b2))
                    for _r2 in query("SELECT sku, SUM(quantity) AS q FROM orders WHERE channel=%s "
                                     "AND (deleted_at IS NULL OR deleted_at='') AND sku IN (%s) "
                                     "GROUP BY sku" % ("%s", _ph3), [channel] + _b2):
                        _cum2[_r2.get("sku")] = int(_r2.get("q") or 0)
            # 2026-10-29 SKU 级去重(数学等价: max 覆盖 ">", min 覆盖 "<", total_qty 用于累计超卖)
            _grp2 = {}
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                _q2 = int(c.get("quantity") or 0)
                _g2 = _grp2.setdefault(sku, {"max": _q2, "min": _q2, "n": 0})
                _g2["max"] = max(_g2["max"], _q2)
                _g2["min"] = min(_g2["min"], _q2)
                _g2["n"] += 1
            o_ctxs = []
            for sku, _g2 in _grp2.items():
                oq = _g2["max"]
                o_ctxs.append({"sku": sku, "channel": channel,
                               "order": {"quantity": oq, "max_quantity": _g2["max"],
                                         "min_quantity": _g2["min"], "line_count": _g2["n"],
                                         "total_qty": _cum2.get(sku, oq)},
                               "order_qty": _cum2.get(sku, oq),
                               "available_stock": inv_map.get(sku, 0),
                               "inv": {"available_qty": inv_map.get(sku, 0), "safety_qty": 0,
                                       "in_transit_qty": 0}})
            if o_ctxs:
                evaluate_many("order.created", o_ctxs, channel,
                              load_rules_for("order.created", channel))
                evaluated = len(o_ctxs)
        elif target in ("inventory", "platform_inv", "inventory_b"):
            i_ctxs = []
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                i_ctxs.append({"sku": sku, "channel": channel,
                               "inv": {"available_qty": int(c.get("available_qty") or 0),
                                       "safety_qty": int(c.get("safety_qty") or 0),
                                       "in_transit_qty": int(c.get("in_transit_qty") or 0),
                                       "warehouse_type": c.get("warehouse_type", ""),
                                       "warehouse": c.get("warehouse", ""),
                                       "product_name": c.get("product_name") or sku}})
            if i_ctxs:
                evaluate_many("inventory.changed", i_ctxs, channel,
                              load_rules_for("inventory.changed", channel))
                evaluated = len(i_ctxs)
    except Exception as _ee:
        try:
            import traceback as _tb6
            from db import execute as _e5
            _e5("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('cleansing_eval','error',%s,%s,'cleansing')",
                ("规则评估失败(%s): %s" % (target, str(_ee)[:200]),
                 _tb6.format_exc(limit=20)[-1800:]))
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    # 汇总消息(顺序状态口径 + 混渠道提示)
    _msg = "成功 %d 条, 跳过 %d 条" % (success, failed)
    try:
        if target == "order":
            from collections import Counter as _C
            _sc = _C((c.get("order_status") or "未知") for c in cleaned_all)
            _ignore = {k: v for k, v in _sc.items() if k != "已完成"}
            if _ignore:
                _msg += " · 注意: %s 不计入销量池(仅已完成), 已发货/待确认/退款类不参与补货采购计算" % \
                        ("; ".join("%s×%d" % (k, v) for k, v in _ignore.items()))
        _chs = {c.get("channel") for c in cleaned_all if c.get("channel")}
        if _chs and channel not in _chs:
            _msg += " · 文件含其他渠道数据(%s), 已按当前渠道 %s 写入" % ("/".join(_chs), channel)
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    return adjusted, evaluated, _msg

def _rebuild_own_batches(channel, skus):
    """自有仓批次重建(2026-10-29 FIFO): 入库按生产日期升序累加 → 出库量按 FIFO 先扣最早批次

    保证 Σ(剩余批次qty) ≡ inventory.available_qty(自有仓恒等式)
    只处理 own 维度; 平台/B 仓批次由库存导入提供, 不在此重算

    仓名策略与 _recalc_own_inventory 一致(2026-10-29 修复):
      · 单自有仓 → 忽略入库记录的 warehouse, 批次统一落在该仓名下
        (曾严格匹配 → 入库记录'北京仓' vs 库存'集货仓' → 80%+ SKU 批次重建失败)
      · 多自有仓 → 严格匹配
    """
    from db import query as _q, execute as _e, executemany as _em
    _ph_wt = ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES))
    _whs = [str(r.get("warehouse") or "") for r in
            _q("SELECT DISTINCT warehouse FROM inventory WHERE channel=%s "
               "AND warehouse_type IN (%s)" % ("%s", _ph_wt),
               [channel] + list(SELF_WAREHOUSE_TYPES))]
    _single = len(_whs) <= 1
    _wh_fixed = _whs[0] if _single else ""
    done = 0
    for i in range(0, len(skus), 100):
        batch_skus = [x for x in skus[i:i + 100] if x]
        if not batch_skus:
            continue
        ph = ",".join(["%s"] * len(batch_skus))
        _wh_cond = "" if _single else (
            " AND warehouse IN (SELECT DISTINCT warehouse FROM inventory "
            "WHERE channel=%s AND warehouse_type IN (%s))" % ("%s", _ph_wt))
        _params = [channel] + batch_skus + ([] if _single else [channel] + list(SELF_WAREHOUSE_TYPES))
        ins = _q("SELECT id, sku, warehouse, prod_date, exp_date, quantity FROM inbound_records "
                 "WHERE channel=%s AND sku IN (%s)" + _wh_cond +
                 " ORDER BY CASE WHEN prod_date='' THEN 1 ELSE 0 END, prod_date, id"
                 % ("%s", ph), _params)
        outs = {}
        for r in _q("SELECT sku, SUM(quantity) AS q FROM outbound_records "
                    "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", ph),
                    [channel] + batch_skus):
            outs[str(r.get("sku"))] = int(r.get("q") or 0)
        by_sku = {}
        for r in ins:
            sk = str(r.get("sku") or "")
            by_sku.setdefault(sk, []).append(r)
        _del_skus, _insert_rows = [], []
        for sk, rows in by_sku.items():
            out_left = outs.get(sk, 0)
            for r in rows:
                qty = int(r.get("quantity") or 0)
                if out_left > 0:
                    take = min(qty, out_left)
                    qty -= take
                    out_left -= take
                if qty > 0:
                    _insert_rows.append({"sku": sk,
                                         "warehouse": _wh_fixed or str(r.get("warehouse") or ""),
                                         "warehouse_type": "own", "channel": channel,
                                         "prod_date": str(r.get("prod_date") or "")[:10],
                                         "exp_date": str(r.get("exp_date") or "")[:10], "qty": qty})
            _del_skus.append(sk)
        if not _del_skus:
            continue
        for j in range(0, len(_del_skus), 200):
            d = _del_skus[j:j + 200]
            try:
                _e("DELETE FROM batches WHERE channel=%s AND warehouse_type IN (%s) AND sku IN (%s)"
                   % ("%s", ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES)), ",".join(["%s"] * len(d))),
                   [channel] + list(SELF_WAREHOUSE_TYPES) + d)
            except Exception as _ed:
                try_err('cleansing', '自有仓批次删除降级', _ed)
        for j in range(0, len(_insert_rows), 300):
            chunk = _insert_rows[j:j + 300]
            cols = ["sku", "warehouse", "warehouse_type", "channel", "prod_date", "exp_date", "qty"]
            try:
                _em("INSERT INTO batches(%s) VALUES(%s)"
                    % (", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols))),
                    [tuple(r[c] for c in cols) for r in chunk])
            except Exception as _ee:
                try_err('cleansing', '自有仓批次插入降级', _ee)
        done += len(_insert_rows)
    return done


def _recalc_own_inventory(channel, skus):
    """自有仓库存重算(2026-10-29 架构定案): available = 期初 + Σ入库 − Σ出库

    幂等: 流水与期初确定 → 结果唯一(重复导入/滞后补录/删除重录均自动正确)
    范围: 仅本次涉及的 SKU(值来自全量流水聚合, 完整性不受影响)

    仓名匹配策略(2026-10-29 修复: 曾严格匹配导致 88% 流水被忽略):
      · 该渠道自有仓 = 1 个 → **忽略 warehouse**, 流水全部归属它
        (业务现实: ERP 导出的入库单仓名常是"收货仓/中转仓", 与库存维度仓名不同;
         单仓时不存在归属歧义)
      · 自有仓 > 1 个 → **严格匹配 warehouse**, 不匹配的流水不计入并记警告
        (多仓时仓名是唯一归属依据, 必须准确; 导入侧应校验仓名)
    """
    from db import query as _q, execute as _e
    _ph_wt = ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES))
    # 该渠道自有仓清单(判断单仓/多仓)
    _whs = [str(r.get("warehouse") or "") for r in
            _q("SELECT DISTINCT warehouse FROM inventory WHERE channel=%s "
               "AND warehouse_type IN (%s)" % ("%s", _ph_wt),
               [channel] + list(SELF_WAREHOUSE_TYPES))]
    _single = len(_whs) <= 1
    out = 0
    for i in range(0, len(skus), 200):
        batch = skus[i:i + 200]
        ph = ",".join(["%s"] * len(batch))
        if _single:
            # 单仓: 忽略 warehouse(含空/异名), 全部计入
            _cond = ""
        else:
            # 多仓: 严格匹配(空仓名仍兜底到唯一仓的场景已由 _single 覆盖, 此处不兜底)
            _cond = " AND r.warehouse = i.warehouse"
        rows = _q(
            "SELECT i.id, i.sku, i.warehouse, "
            "COALESCE(i.beginning_stock, 0) AS beg, "
            "COALESCE((SELECT SUM(r.quantity) FROM inbound_records r "
            "  WHERE r.channel=i.channel AND r.sku=i.sku" + _cond + "), 0) AS in_q, "
            "COALESCE((SELECT SUM(r.quantity) FROM outbound_records r "
            "  WHERE r.channel=i.channel AND r.sku=i.sku" + _cond + "), 0) AS out_q "
            "FROM inventory i WHERE i.channel=%s AND i.warehouse_type IN (%s) "
            "AND i.sku IN (%s)" % ("%s", _ph_wt, ph),
            [channel] + list(SELF_WAREHOUSE_TYPES) + batch)
        upd = []
        for r in rows:
            try:
                val = int(r.get("beg") or 0) + int(r.get("in_q") or 0) - int(r.get("out_q") or 0)
            except Exception:
                continue
            upd.append((max(0, val), r.get("id")))
        if upd:
            from db import executemany as _em
            _em("UPDATE inventory SET available_qty=%s WHERE id=%s", upd)
            out += len(upd)
    return out


def _adjust_inventory(channel, deltas, evaluate_skus=None):
    """库存联动: {(sku, warehouse, warehouse_type): delta} → 批量更新库存(下限 0) + 规则批量评估(全量 SKU)

    完整性: 不限量 —— 大文件联动 SKU 全覆盖(原 300 上限曾致 >300 SKU 导入后规则评估缺失, 告警漏报);
    性能: evaluate_many 一次加载规则 + executemany 批量(原单条 evaluate 循环 N×查询)
    """
    if not deltas:
        return 0, []
    # 2026-10-15 批量联动(逐键 SELECT+UPDATE 曾 5000 键 ~10000 往返——210s 大头): 批量 SELECT 一次 + executemany 更新
    n = 0
    touched = []
    _keys = list(deltas.keys())
    _cur_map = {}
    for _i in range(0, len(_keys), 200):
        _batch = _keys[_i:_i + 200]
        _or = " OR ".join("(sku=%s AND warehouse=%s AND channel=%s)" for _ in _batch)
        _params = []
        for (sk, wh, wt) in _batch:
            _params += [sk, wh or "", channel]
        if _or:
            for _c in query("SELECT id, sku, warehouse, available_qty FROM inventory WHERE %s" % _or, _params):
                _cur_map[(_c.get("sku"), _c.get("warehouse") or "", channel)] = _c
    _upd = []
    _ins = []
    for (sku, wh, wht), delta in deltas.items():
        _ck = (sku, wh or "", channel)
        cur = _cur_map.get(_ck)
        if cur:
            new_qty = max(0, int(cur.get("available_qty") or 0) + delta)
            _upd.append((new_qty, cur.get("id")))
        else:
            _ins.append((sku, wh or "", wht or "own", max(0, delta), channel))
        n += 1
        if sku not in touched:
            touched.append(sku)
    if _upd:
        executemany("UPDATE inventory SET available_qty=%s WHERE id=%s", _upd)
    if _ins:
        executemany("INSERT INTO inventory(sku, warehouse, warehouse_type, available_qty, safety_qty, channel) "
                    "VALUES(%s,%s,%s,%s,10,%s)", _ins)
    skus = touched
    try:
        from core.rules import evaluate_many, load_rules_for
        ctxs = []
        for sku in skus:
            inv = one("SELECT sku, warehouse, warehouse_type, product_name, available_qty, safety_qty, "
                      "in_transit_qty FROM inventory WHERE sku=%s AND channel=%s LIMIT 1", [sku, channel])
            if inv:
                ctxs.append({"sku": sku, "channel": channel, "inv": inv})
        if ctxs:
            evaluate_many("inventory.changed", ctxs, channel, load_rules_for("inventory.changed", channel))
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    return n, skus

def _write_rows(target, channel, conflict_mode, cleaned):
    """按 target 分派写入; 返回 (success, failed)
    渠道归属: 导入数据归属导入渠道(除非 mapping 显式映射了 channel 列)——否则未映射时落默认 jd 污染跨渠道"""
    for it in cleaned:
        if "channel" not in it:
            it["channel"] = channel
    if target == "order":
        # 69 码自动查补(2026-09-15): 导入文件未映射/为空时从 products 按 sku 拉取(products.barcode 已全量填充)
        # —— 准确性(sku 1:1 唯一)/完整性(products 全有 barcode)/实时性(导入即补, 不再产生空 69 码)
        try:
            _miss = [r for r in cleaned if not (r.get("barcode") or "").strip()]
            if _miss:
                _skus = list({str(r.get("sku") or "").strip() for r in _miss if r.get("sku")})
                if _skus:
                    _bmap = {}
                    for _b in query("SELECT sku, barcode FROM products WHERE sku IN (%s) "
                                    "AND barcode IS NOT NULL AND barcode != ''"
                                    % ",".join(["%s"] * len(_skus)), _skus):
                        _bmap[str(_b.get("sku"))] = _b.get("barcode")
                    for r in cleaned:
                        _k = str(r.get("sku") or "").strip()
                        if not (r.get("barcode") or "").strip() and _bmap.get(_k):
                            r["barcode"] = _bmap[_k]
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
        return _write_batch("orders",
                            ["order_no", "store", "warehouse", "sku", "product_name", "barcode",
                             "quantity", "unit_price", "total_amount", "order_status",
                             "ordered_at", "paid_at", "platform", "channel", "data_source",
                             "freight_amount", "subsidy_amount", "tax_amount", "discount_amount",
                             "actual_amount", "supplier", "remark"],
                            cleaned, conflict_mode, "order_no", "sku", channel=channel,
                            # 2026-10-17 69码三键优先(order_no|sku|barcode, 空barcode落二键): 同单同SKU同69码多行求和(quantity/金额)
                            agg_key3="barcode",
                            agg_sum=["quantity", "total_amount", "actual_amount", "discount_amount",
                                     "freight_amount", "subsidy_amount", "tax_amount"])
    if target == "gmv_flow":
        # 2026-10-16 GMV 回款(资金流水——表B): platform+flow_no 去重——月度回款/在途数据源
        try_err('cleanse_write', 'gmv_flow 分支进入 cleaned=%d' % len(cleaned))
        try:
            execute("CREATE TABLE IF NOT EXISTS gmv_flows (id BIGINT AUTO_INCREMENT PRIMARY KEY, platform VARCHAR(32) NOT NULL, shop VARCHAR(64) DEFAULT '', flow_no VARCHAR(64) NOT NULL, related_no VARCHAR(64) DEFAULT '', arrived_time DATETIME, amount DECIMAL(14,2) DEFAULT 0, flow_type VARCHAR(32) DEFAULT '', settled_amount DECIMAL(14,2) DEFAULT 0, channel VARCHAR(20) DEFAULT 'jd', data_source VARCHAR(32) DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_platform_flow_ch (channel, platform, flow_no))")
        except Exception as _etb:
            try_err('cleanse_write', 'gmv_flows 建表降级', _etb)
        for it in cleaned:
            it["channel"] = channel
            # 2026-10-16 唯一键补 channel 幂等迁移(跨渠道 platform+flow_no 撞键互阻——jd 与 other
            # 各自平台流水号独立编号, 无 channel 维度第二条渠道 1062 整批失败):
            # 老表 uq_platform_flow(platform,flow_no) → 新 uq_platform_flow_ch(channel,platform,flow_no)
            try:
                from db import query as _qix
                _ix = _qix("SHOW INDEX FROM gmv_flows")
                _uq_cols = [str(r.get("Column_name") or "") for r in (_ix or [])
                            if r.get("Key_name") == "uq_platform_flow" and r.get("Column_name")]
                if _uq_cols and "channel" not in _uq_cols:
                    execute("ALTER TABLE gmv_flows DROP INDEX uq_platform_flow")
                    execute("ALTER TABLE gmv_flows ADD UNIQUE KEY uq_platform_flow_ch (channel, platform, flow_no)")
                    try_err('cleanse_write', 'gmv_flows 唯一键迁移完成(channel 补入)', '')
            except Exception as _eix:
                try_err('cleanse_write', 'gmv_flows 唯一键迁移降级', _eix)
            # 2026-10-16 钉死 1366 根因: amount/settled_amount 为 DECIMAL, 映射类型 string 时
            # 原样直写(如 ¥1,200.50/文本/空态) → TiDB 1366 整批失败。写入前强制 number 清洗
            # (与 _v 同规: 去 ¥,元/逗号/空格 → float; 无法解析归 0, 不炸批)
            for _nc in ("amount", "settled_amount"):
                if it.get(_nc) in (None, ""):
                    continue  # 空值跳过列(DECIMAL DEFAULT 0)
                try:
                    _s = str(it[_nc]).replace("¥", "").replace("元", "").replace(",", "").replace(" ", "").strip()
                    it[_nc] = float(_s) if _s not in ("", "-", "--") else 0.0
                except Exception:
                    it[_nc] = 0.0  # 无法解析归 0(计数成功——不做静默失败)
            # arrived_time 宽松归一(样例可能是 "2026-09-15 10:00:00" / 带 T / 仅日期):
            # 解析失败置 None(空值走缺列跳过——不 1366)
            if it.get("arrived_time") not in (None, ""):
                _at = str(it["arrived_time"]).strip()
                _norm = ""
                try:
                    from datetime import datetime as _dtn
                    for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                                 "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y年%m月%d日"):
                        try:
                            _norm = _dtn.strptime(_at, _fmt).strftime("%Y-%m-%d %H:%M:%S")
                            break
                        except Exception:
                            continue
                except Exception:
                    _norm = ""
                it["arrived_time"] = _norm
        return _write_batch("gmv_flows",
                            ["platform", "shop", "flow_no", "related_no", "arrived_time",
                             "amount", "flow_type", "settled_amount", "channel", "data_source"],
                            cleaned, conflict_mode, "platform", "flow_no",
                            agg_sum=["amount", "settled_amount"])
    if target in ("inventory", "platform_inv", "inventory_b"):
        wt = _TARGET_WH[target]
        for it in cleaned:
            it["warehouse_type"] = wt
            # 2026-10-29 架构定案: 自有仓库存=重算制 —— 导入的 available_qty 作为【期初基线】
            #   available = beginning_stock + Σ入库 − Σ出库(由 _recalc_own_inventory 计算)
            #   平台仓/B仓: 导入即权威快照(available 直接生效, 不重算)
            if wt == "own":
                # own: 导入值作为【期初】(available = 期初 + Σ入 − Σ出, 由重算得出)
                # 兼容两种列: available_qty(当前可用) 或 beginning_stock(期初库存)
                _bs = it.get("beginning_stock")
                if _bs in (None, "", 0):
                    _bs = it.get("available_qty") or 0
                it["beginning_stock"] = _bs
        return _write_batch("inventory",
                            ["sku", "product_name", "store", "warehouse", "warehouse_type",
                             "available_qty", "in_transit_qty", "safety_qty", "channel", "barcode",
                             "beginning_stock", "month_inbound", "month_outbound", "locked_qty",
                             "c_transit", "weight", "volume"],
                            cleaned, conflict_mode, "sku", "warehouse", channel=channel,
                            agg_key3="barcode",  # 69码三键(sku|warehouse|barcode, 空落二键)
                            agg_sum=["available_qty", "in_transit_qty", "c_transit", "locked_qty",
                                     "beginning_stock", "month_inbound", "month_outbound"])
    if target == "product":
        return _write_batch("products",
                            ["sku", "product_name", "barcode", "store", "category", "price",
                             "box_qty", "unit", "status", "channel", "brand", "weight", "volume"],
                            cleaned, conflict_mode, "sku", channel=channel,
                            agg_key3="barcode", agg_overwrite=True)  # 同 SKU+69码 覆盖末行档案
    if target == "supplier":
        return _write_batch("suppliers",
                            ["supplier_code", "supplier_name", "contact_person", "contact_phone",
                             "score", "status", "channel", "brand"],
                            cleaned, conflict_mode, "supplier_code", channel=channel,
                            agg_overwrite=True)  # 同 supplier_code 覆盖末行档案
    if target == "inbound":
        cleaned = _fill_warehouse_type(channel, cleaned, "inbound")
        s, f, ed = _write_batch("inbound_records",
                            ["sku", "product_name", "quantity", "supplier", "inbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status", "remark",
                             "warehouse_type"],
                            cleaned, conflict_mode, "sku", "inbound_date",
                            agg_key3="barcode", agg_sum=["quantity", "price"])
        # 批次效期同步(batches): 入库记录含 prod_date/exp_date(按映射列抓取) → 按 SKU×仓 维护
        # 更新方式联动导入冲突模式(conflict_mode): overwrite=删旧插新(文件即权威) / sum=同效期 qty 累加
        # (效期管控数据源: 进销存批次效期预警/临期处置建议)
        try:
            _br = [c for c in cleaned if (c.get("prod_date") or "") or (c.get("exp_date") or "")]
            if _br:
                _sync_batches(channel, _br, conflict_mode)
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
        return s, f, ed
    if target == "outbound":
        cleaned = _fill_warehouse_type(channel, cleaned, "outbound")
        return _write_batch("outbound_records",
                            ["sku", "product_name", "quantity", "target_warehouse", "outbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status", "remark",
                            "warehouse_type"],
                            cleaned, conflict_mode, "sku", "outbound_date", channel=channel,
                            agg_key3="barcode", agg_sum=["quantity", "price"])
    return 0, len(cleaned), []

# 2026-10-29 自有类仓型白名单(可扩展): 出入库只允许这些仓型
# 未来新增自有仓类型(如区域仓/前置仓)只需在此追加, 其余逻辑自动跟随
SELF_WAREHOUSE_TYPES = ("own",)


def _is_self_wt(wt):
    return str(wt or "").strip() in SELF_WAREHOUSE_TYPES


def _fill_warehouse_type(channel, cleaned, target):
    """仓型归属(2026-10-29 业务定案): **出入库只属于自有仓维度**

    业务事实: 平台仓/京东B仓的数据只能从平台后台导出, 没有"出入库单据"概念;
    它们的在途(供应商→B仓 / B→C调拨)由【库存】类型导入的 in_transit_qty 承载。
    → 出入库导入一律归 own(未来多自有仓仍为 own, 用 warehouse 区分);
      文件里填了其它仓型会被纠正并留痕(避免误把平台数据当自有仓台账)
    """
    if target in ("inbound", "outbound"):
        fixed = 0
        for c in cleaned:
            wt0 = str(c.get("warehouse_type") or "").strip()
            if wt0 and not _is_self_wt(wt0):
                fixed += 1
                c["warehouse_type"] = SELF_WAREHOUSE_TYPES[0]  # 非自有类 → 纠正为首个自有仓型
            elif not wt0:
                c["warehouse_type"] = SELF_WAREHOUSE_TYPES[0]  # 未填 → 默认自有仓
            # 已在白名单内(如未来 own_a) → 原样保留
        if fixed:
            try_err('cleansing', '出入库仓型纠正(非自有类 %d 行 → %s)' % (fixed, SELF_WAREHOUSE_TYPES[0]),
                    ValueError('warehouse_type corrected'))
        return cleaned
    return cleaned


def _write_batch(table, allowed_cols, cleaned, conflict_mode, *key_cols, channel="jd", agg_sum=None, agg_overwrite=False, agg_key3=None):
    """批量写入: 仅保留存在的列; 冲突策略——
    inbound/outbound(sum/overwrite 前端适配面): sum=数量累加, overwrite=全列覆盖
    其他目标(order/inventory/product/supplier): 统一全列覆盖(对齐 PA ON CONFLICT DO UPDATE)
    2026-10-17 文件内同键聚合(新导入数据内部: 同组合键多行 → 合并, 不撞唯一键 1062):
    agg_sum=求和列集(数量/金额类——order/inventory/gmv_flow/inbound/outbound 同键多行求和一条);
    agg_overwrite=True(同键覆盖末行——product/supplier 档案类 upsert 语义); None=不聚合
    agg_key3=69码三键(订单/库存/商品/出入库: 组合键 + 69码 优先, 69码空落二键——用户定)"""
    if not cleaned:
        return 0, 0, []
    keys = list(dict.fromkeys(key_cols))  # 保序 list(曾 set 无序——查重 OR keys[0]/keys[1] 下标崩)
    # 2026-10-17 分派式文件内聚合(按 target 组合键/字段特殊适配——不能一刀切)
    if agg_sum is not None and len(keys) >= 1 and len(cleaned) > 1:
        _agg_sum_cols = [c for c in agg_sum if c in allowed_cols]
        _agg_map = {}
        _agg_order = []
        for it in cleaned:
            _base = tuple(str(it.get(k) or "") for k in keys)
            if not all(_base):
                continue  # 缺键行留 _write_batch 原判失败逻辑
            # 69码三键优先: barcode 非空 → 键+69码; 空 → 落二键(与 dup-check/force_new 同规则)
            _b3 = str(it.get(agg_key3) or "").strip() if agg_key3 else ""
            _k = _base + ((_b3,) if _b3 else ())
            if _k not in _agg_map:
                _agg_map[_k] = dict(it)
                _agg_order.append(_k)
            else:
                _cur = _agg_map[_k]
                for _c in _agg_sum_cols:
                    try:
                        _cur[_c] = float(_cur.get(_c) or 0) + float(it.get(_c) or 0)
                    except Exception:
                        pass  # 非数字保持原值
        if _agg_order:
            cleaned = [_agg_map[k] for k in _agg_order]
    elif agg_overwrite and len(keys) >= 1 and len(cleaned) > 1:
        # 档案类(products/suppliers): 同键覆盖末行(upsert 语义——同 SKU 多行取最后一行档案)
        _agg_map = {}
        _agg_order = []
        for it in cleaned:
            _base = tuple(str(it.get(k) or "") for k in keys)
            if not all(_base):
                continue
            _b3 = str(it.get(agg_key3) or "").strip() if agg_key3 else ""
            _k = _base + ((_b3,) if _b3 else ())
            if _k not in _agg_map:
                _agg_map[_k] = dict(it)
                _agg_order.append(_k)
            else:
                _agg_map[_k] = dict(it)  # 覆盖末行
        if _agg_order:
            cleaned = [_agg_map[k] for k in _agg_order]
    success = failed = 0
    err_details = []
    qty_cols = [c for c in ("quantity", "available_qty") if c in allowed_cols]
    # 2026-10-16: 数字列兜底(缺失列补 0 而非 KeyError——曾空值跳键 → tuple(r[c]) KeyError 整批降级)
    _num_fill = {c for c in ("quantity", "available_qty", "amount", "settled_amount", "price",
                             "total_amount", "unit_price", "freight_amount", "subsidy_amount",
                             "tax_amount", "discount_amount", "actual_amount", "safety_qty",
                             "in_transit_qty", "c_transit", "locked_qty", "weight", "volume",
                             "box_qty", "score", "beginning_stock", "month_inbound",
                             "month_outbound", "turnover_days") if c in allowed_cols}
    # sum 累加仅限出入库记录(HammerCleansing 只在 inbound/outbound 暴露 sum/overwrite 选择)
    sum_mode = table in ("inbound_records", "outbound_records") and conflict_mode == "sum"
    for i in range(0, len(cleaned), 500):
        chunk = cleaned[i:i + 500]
        rows = []
        for it in chunk:
            row = {}
            ext = {}
            for c, v in it.items():
                if c == "_meta" or v is None or str(v) == "":
                    continue
                if c in allowed_cols:
                    row[c] = v
                else:
                    # 自定义列(用户动态新增, 非标准字段) → ext_json 合并存储
                    ext[c] = v
            if ext:
                row["ext_json"] = json.dumps(ext, ensure_ascii=False)
            # 严谨性: key 列必须存在且有值(原 `k in row` 跳过缺失列 → 无有效映射的导入
            # 也会写入空记录污染数据); 缺 key 的行判失败 + 留痕原因(2026-10-16: 曾只 failed++
            # 无明细——gmv_flow 5 次导入 failed=15 全无因可查——用户无法定位缺哪个必填列)
            if not row or not all(k in row and row.get(k) for k in keys):
                failed += 1
                if len(err_details) < 50:
                    _miss = [str(k) for k in keys if not (k in row and row.get(k))]
                    err_details.append({
                        "sku": str(it.get("sku") or it.get("flow_no") or it.get("platform") or "")[:40],
                        "reason": "缺少必填列: %s" % "、".join(_miss),
                        "row": str({k: it.get(k) for k in keys})[:200]})
                continue
            rows.append(row)
        if not rows:
            continue
        # 2026-10-16 去写入层查重(前端提前检测已拦截重复——订单号+sku+69码组合——后端纯写入提速回 seed)
        # 2026-10-16 列并集: 曾 cols=rows[0] 键——首行缺列(空值跳键)时后续行该列静默丢弃——
        # 并集 + .get 兜底(缺失补默认)保列完整(不丢数据)
        cols = list(dict.fromkeys(c for r in rows for c in (r.keys() if isinstance(r, dict) else [])))
        sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (
            table, ", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols)))
        if sum_mode:
            # inbound/outbound + sum: 数量列累加, 其余列覆盖
            upd = []
            for c in cols:
                if c in qty_cols:
                    upd.append("`%s` = `%s` + VALUES(`%s`)" % (c, c, c))
                elif c not in keys and c not in ("id", "deleted_at"):
                    upd.append("`%s` = VALUES(`%s`)" % (c, c))
            if upd:
                sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(upd)
        else:
            # 2026-10-15 性能: 非 sum 模式已做已存在跳过(rows 全新键)——纯 INSERT 无 ON DUPLICATE(executemany+ON DUP 对 TiDB 慢 10 倍——seed 5000/批 36s vs 此处 5.5 分钟)
            pass
        # 批量写入(1000/批 executemany 单语句——TiDB 单语句原子无需事务; 事务(5000行大=持锁慢/500×10小=往返多)均慢——
        # 无事务 + 1000 行/批: 5 批单语句(原子 + 往返少)——5000 行每步 ~5-15s)
        for _bi in range(0, len(rows), 1000):
            _b = rows[_bi:_bi + 1000]
            try:
                executemany(sql, [tuple(r.get(c, 0 if c in _num_fill else "") for c in cols) for r in _b])
                success += len(_b)
            except Exception as _be:
                # 2026-10-16 留痕(曾无痕——批失败被吞——窗口5 计数 4000/1000 无法定位)
                try_err('cleanse_write', '写入批失败降级: %s (表 %s, 批 %d 行)' % (str(_be)[:120], table, len(_b)), _be)
                # 批失败(单语句原子——无部分写入)→ 单行降级(精确计数)
                for r in _b:
                    try:
                        execute(sql, [r[c] for c in cols])
                        success += 1
                    except Exception as e1:
                        failed += 1
                        try_err('cleanse_write', '单行写失败 %s: %s' % (r.get("sku") or "?", str(e1)[:90]), e1)
                        if len(err_details) < 50:
                            err_details.append({"sku": str(r.get("sku") or r.get("supplier_code") or ""),
                                                "reason": str(e1)[:80], "row": str(r)[:200]})
    return success, failed, err_details

def _sync_batches(channel, rows, conflict_mode="overwrite"):
    """批次效期同步(入库记录导入含 prod_date/exp_date 列时): 按 SKU×仓 维护 batches
    (效期管控数据源: 进销存批次效期预警/临期处置建议)
    更新方式联动导入冲突模式:
    - overwrite(默认): 文件即权威 —— 删该 SKU×仓 旧批次, 插入文件批次
    - sum: 累加 —— 同 SKU×仓×效期(prod_date+exp_date)批次 qty 累加(多次入库同批次累加), 不同效期独立行"""
    from db import executemany as _em
    if not rows:
        return 0
    touched = set()
    ins = []
    for c in rows:
        sku = c.get("sku")
        if not sku:
            continue
        wh = str(c.get("warehouse") or "")
        wt = str(c.get("warehouse_type") or "")
        pd = str(c.get("prod_date") or "")[:10]
        ed = str(c.get("exp_date") or "")[:10]
        touched.add((sku, wh, wt))
        ins.append({"sku": sku, "warehouse": wh, "warehouse_type": wt, "channel": channel,
                    "prod_date": pd, "exp_date": ed,
                    "qty": int(c.get("available_qty") or c.get("quantity") or c.get("qty") or 0)})
    if conflict_mode == "sum":
        # 累加模式: 同 SKU×仓×效期批次 qty 累加(不同效期=不同批次独立行)
        for it in ins:
            try:
                exist = one("SELECT id, qty FROM batches WHERE sku=%s AND warehouse=%s "
                            "AND channel=%s AND prod_date=%s AND exp_date=%s",
                            [it["sku"], it["warehouse"], it["channel"],
                             it["prod_date"], it["exp_date"]])
                if exist:
                    execute("UPDATE batches SET qty=%s WHERE id=%s",
                            [int(exist.get("qty") or 0) + it["qty"], exist.get("id")])
                else:
                    execute("INSERT INTO batches(sku, warehouse, warehouse_type, channel, "
                            "prod_date, exp_date, qty) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                            [it["sku"], it["warehouse"], it["warehouse_type"], it["channel"],
                             it["prod_date"], it["exp_date"], it["qty"]])
            except Exception as _e:
                    try_err('cleansing', '静默降级', _e)
        return len(ins)
    for sku, wh, wt in touched:
        try:
            execute("DELETE FROM batches WHERE sku=%s AND warehouse=%s AND channel=%s",
                    [sku, wh, channel])
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    for i in range(0, len(ins), 200):
        chunk = ins[i:i + 200]
        cols = list(chunk[0].keys())
        try:
            _em("INSERT INTO batches(%s) VALUES(%s)" % (
                ", ".join("`%s`" % x for x in cols), ", ".join(["%s"] * len(cols))),
                [tuple(r[c] for c in cols) for r in chunk])
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    return len(ins)
