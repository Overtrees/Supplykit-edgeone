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
            from collections import defaultdict
            deltas = defaultdict(int)
            _wht = {}
            for c in cleaned_all:
                _sku = c.get("sku")
                if not _sku:
                    continue
                _qty = int(c.get("quantity") or 0)
                _wh = str(c.get("warehouse") or "")
                if target == "inbound":
                    _d, _wt = _qty, "own"
                elif target == "outbound":
                    _d, _wt = -_qty, "own"
                else:
                    _ds = ((mp or {}).get("_meta") or {}).get("data_source", "")
                    _d = _qty if _ds == "jd_po" else -_qty
                    _wt = "platform"
                if _d:
                    deltas[(_sku, _wh, _wt)] += _d
                    _wht[(_sku, _wh, _wt)] = _wt
            if deltas:
                adjusted, _ = _adjust_inventory(channel, {k: v for k, v in deltas.items()})
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    # 规则引擎评估(批量)
    try:
        from core.rules import evaluate_many, load_rules_for
        if target == "order":
            seen = set()
            skus = [c.get("sku") for c in cleaned_all
                    if c.get("sku") and not (c.get("sku") in seen or seen.add(c.get("sku")))]
            inv_map = {}
            if skus:
                for _i in range(0, len(skus), 200):
                    _batch = skus[_i:_i + 200]
                    _ph = ",".join(["%s"] * len(_batch))
                    for r in query("SELECT sku, MAX(available_qty) AS avail FROM inventory "
                                   "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph),
                                   [channel] + _batch):
                        inv_map[r.get("sku")] = int(r.get("avail") or 0)
            o_ctxs = []
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                oq = int(c.get("quantity") or 0)
                o_ctxs.append({"sku": sku, "channel": channel,
                               "order": {"quantity": oq}, "order_qty": oq,
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
                            cleaned, conflict_mode, "order_no", "sku")
    if target in ("inventory", "platform_inv", "inventory_b"):
        wt = _TARGET_WH[target]
        for it in cleaned:
            it["warehouse_type"] = wt
        return _write_batch("inventory",
                            ["sku", "product_name", "store", "warehouse", "warehouse_type",
                             "available_qty", "in_transit_qty", "safety_qty", "channel", "barcode",
                             "beginning_stock", "month_inbound", "month_outbound", "locked_qty",
                             "c_transit", "weight", "volume"],
                            cleaned, conflict_mode, "sku", "warehouse")
    if target == "product":
        return _write_batch("products",
                            ["sku", "product_name", "barcode", "store", "category", "price",
                             "box_qty", "unit", "status", "channel", "brand", "weight", "volume"],
                            cleaned, conflict_mode, "sku")
    if target == "supplier":
        return _write_batch("suppliers",
                            ["supplier_code", "supplier_name", "contact_person", "contact_phone",
                             "score", "status", "channel", "brand"],
                            cleaned, conflict_mode, "supplier_code")
    if target == "inbound":
        s, f, ed = _write_batch("inbound_records",
                            ["sku", "product_name", "quantity", "supplier", "inbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status"],
                            cleaned, conflict_mode, "sku", "inbound_date")
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
        return _write_batch("outbound_records",
                            ["sku", "product_name", "quantity", "target_warehouse", "outbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status"],
                            cleaned, conflict_mode, "sku", "outbound_date")
    return 0, len(cleaned), []

def _write_batch(table, allowed_cols, cleaned, conflict_mode, *key_cols):
    """批量写入: 仅保留存在的列; 冲突策略——
    inbound/outbound(sum/overwrite 前端适配面): sum=数量累加, overwrite=全列覆盖
    其他目标(order/inventory/product/supplier): 统一全列覆盖(对齐 PA ON CONFLICT DO UPDATE)"""
    if not cleaned:
        return 0, 0, []
    keys = list(dict.fromkeys(key_cols))  # 保序 list(曾 set 无序——查重 OR keys[0]/keys[1] 下标崩)
    success = failed = 0
    err_details = []
    qty_cols = [c for c in ("quantity", "available_qty") if c in allowed_cols]
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
            # 也会写入空记录污染数据); 缺 key 的行判失败
            if not row or not all(k in row and row.get(k) for k in keys):
                failed += 1
                continue
            rows.append(row)
        if not rows:
            continue
        # 2026-10-15 已存在跳过(用户记忆机制落地): 同键已在库(非 sum 模式)——跳过该行不覆盖(防白导/防误覆盖)
        # 语义: 重复导入同表——已存在行跳过(完成页显示跳过 N 条); 冲突按钮 overwrite 仍覆盖?——本实现跳过(贴合'提示已存在重复→跳过')
        if not sum_mode:
            _key_rows = []
            _seen = set()
            for _r in rows:
                _key = tuple(str(_r.get(k)) for k in keys)
                if _key in _seen:
                    continue
                _seen.add(_key)
                _key_rows.append((_key, _r))
            if _key_rows:
                _cond = " AND ".join("`%s` IN (%s)" % (k, ",".join(["%s"] * len(_key_rows))) for k in keys)
                _params = []
                for _k, _ in _key_rows:
                    for _v in _k:
                        _params.append(_v)
                # 2026-10-15 批量查重优化(逐键 EXISTS 曾 5000 行~277s——性能灾难): CONCAT 键单列 IN 一次查
                # TiDB 无多列 IN——用 CONCAT(col1,'|',col2) 合成键——IN 500 分片(往返 10 次内)
                _key2 = [str(_k[0]) + "|" + str(_k[1]) if len(_k) > 1 else str(_k[0]) for _k, _ in _key_rows]
                _dup = set()
                _uk2 = list(dict.fromkeys(_key2))
                # 2026-10-15 查重改 OR 条件(走索引——CONCAT 全表扫描 12 万行曾慢): (col1=%s AND col2=%s) OR ... 500/片
                for _i in range(0, len(_uk2), 500):
                    _batch = _uk2[_i:_i + 500]
                    _parsed = [b.split("|") for b in _batch]
                    if len(keys) > 1:
                        # 2026-10-16 精确查重: 查询返回实际存在键集(含 channel)——只标记命中键(曾 one() 任一命中→整片 500 全判 dup 误伤)
                        _or = " OR ".join("(`%s`=%s AND `%s`=%s)" % (keys[0], "%s", keys[1], "%s") for _ in _parsed)
                        _params = [channel]
                        for p in _parsed:
                            _params += [p[0], p[1] if len(p) > 1 else ""]
                        _sql = ("SELECT CONCAT(`%s`,'|',`%s`) AS k FROM `%s` WHERE channel=%s AND (%s)"
                                % (keys[0], keys[1], table, "%s", _or))
                        for _ex in query(_sql, _params):
                            _dup.add((_ex.get("k") or "").split("|")[0], (_ex.get("k") or "").split("|")[1] if len((_ex.get("k") or "").split("|")) > 1 else "")
                    else:
                        _ph = ",".join(["%s"] * len(_batch))
                        for _ex in query("SELECT `%s` AS k FROM `%s` WHERE `%s` IN (%s)" % (keys[0], table, keys[0], _ph), _batch):
                            _dup.add((_ex.get("k"),))
                if _dup:
                    rows = [r for r in rows if tuple(str(r.get(k)) for k in keys) not in _dup]
                    failed += len(_dup)
                    continue
        cols = list(rows[0].keys())
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
                executemany(sql, [tuple(r[c] for c in cols) for r in _b])
                success += len(_b)
            except Exception:
                # 批失败(单语句原子——无部分写入)→ 单行降级(精确计数)
                for r in _b:
                    try:
                        execute(sql, [r[c] for c in cols])
                        success += 1
                    except Exception as e1:
                        failed += 1
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
