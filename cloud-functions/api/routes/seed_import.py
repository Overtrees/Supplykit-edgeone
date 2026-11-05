"""演示数据 → 导入文件规格(2026-11-05)

## 为什么要有这一层

seed 原来是"直接 INSERT 进表"。问题是**演示数据不经过任何真实链路验证**:
导入要过列映射、单位换算、69码归码、仓型归属、期初实盘校准、去重/覆盖语义、
库存联动重算、批次重建、规则评估、缓存失效 —— 这些环节 seed 一个都没走。
于是"演示库好好的、真实导入却出错"完全可能, 而且没人能提前发现。

现在改成: seed 仍然**生成**数据(它擅长这个), 但**落库一律走导入链路**:
  生成内存行 → 按导入列重命名/排序 → csv 文本 → 解析 → 列映射清洗 → 按 target 写入
`csv 文本` 这一步是刻意的: 用真的 CSV 往返(而不是内存里直接换个 dict), 才能把
"日期是字符串还是 datetime"、"数值带不带千分位"、"空列写不写"这类问题在演示环境暴露出来。

## 列名为什么用中文

导入页的字段字典(`frontend/src/pages/fields.ts`)按**中文导出列名**做映射, 真实客户
拿到的就是 ERP/平台导出的中文表头。seed 生成的中文表头 + 显式 mapping 一起下发,
等价于"用户上传了这份文件、选了 auto 映射" —— 链路与真实场景同构。

## 只覆盖能走导入的 target

平台的导入 target 有 7 个: order / inventory / platform_inv / inventory_b /
inbound / outbound / product。seed 用到其中 6 个(platform_inv 与 inventory_b 是
同一张表的不同仓型入口, 本文件用 inventory_b 承载 B 仓快照)。

**不在导入链路上的**是纯计算产物, 由 seed 照旧直接写(它们本来就不是"导入"能产生的):
suppliers(供应商档案, 真实客户另行维护)、replenishment_config、rules、alerts、
daily_sales_snapshot、sku_sales_stats、orders_day_agg、inventory_month、ledger_state。
"""
from db import query


# ── 规格: 每个 target 一份 ────────────────────────────────────────────────
# cols: 导出文件表头(中文, 与 fields.ts 的 label 对齐)
# mp:   列映射 {中文表头: {"target": 目标列, "type": 类型}} —— 与导入页 auto 映射同构
# keys: 该 target 的业务键(用于去重; 出入库无唯一键, 不参与查重)
# dup:  是否在导入入口做"同键已存在"预检(与 cleansing_execute 的 _dup_cfg 口径一致)
# agg:  None=不聚合 / "sum"=同键数量金额累加 / "overwrite"=同键覆盖末行
# key3: 69码作为第三键(空码自动落二键)
_SPEC = {
    "product": {
        "table": "products",
        "cols": ["SKU编码", "商品名称", "69码", "店铺", "分类", "品牌", "售价", "箱规", "单位",
                 "重量", "体积", "状态", "是否做效期管理", "供应商编码"],
        "mp": {"SKU编码": {"target": "sku", "type": "string"},
               "商品名称": {"target": "product_name", "type": "string"},
               "69码": {"target": "barcode", "type": "string"},
               "店铺": {"target": "store", "type": "string"},
               "分类": {"target": "category", "type": "string"},
               "品牌": {"target": "brand", "type": "string"},
               "售价": {"target": "price", "type": "number"},
               "箱规": {"target": "box_qty", "type": "number"},
               "单位": {"target": "unit", "type": "string"},
               "重量": {"target": "weight", "type": "number"},
               "体积": {"target": "volume", "type": "number"},
               "状态": {"target": "status", "type": "string"},
               "是否做效期管理": {"target": "expiry_managed", "type": "string"},
               "供应商编码": {"target": "supplier_code", "type": "string"}},
        "keys": ("sku",), "dup": True, "agg": "overwrite", "key3": "barcode",
        "conflict_mode": "overwrite",
    },
    "order": {
        "table": "orders",
        "cols": ["订单号", "店铺", "仓库", "SKU编码", "商品名称", "69码", "数量", "单价",
                 "订单金额", "订单状态", "下单时间", "支付时间", "平台", "渠道",
                 "数据来源", "运费", "补贴", "税额", "优惠金额", "实付金额", "供应商", "备注"],
        "mp": {"订单号": {"target": "order_no", "type": "string"},
               "店铺": {"target": "store", "type": "string"},
               "仓库": {"target": "warehouse", "type": "string"},
               "SKU编码": {"target": "sku", "type": "string"},
               "商品名称": {"target": "product_name", "type": "string"},
               "69码": {"target": "barcode", "type": "string"},
               "数量": {"target": "quantity", "type": "number"},
               "单价": {"target": "unit_price", "type": "number"},
               "订单金额": {"target": "total_amount", "type": "number"},
               "订单状态": {"target": "order_status", "type": "string"},
               "下单时间": {"target": "ordered_at", "type": "string"},
               "支付时间": {"target": "paid_at", "type": "string"},
               "平台": {"target": "platform", "type": "string"},
               "渠道": {"target": "channel", "type": "string"},
               "数据来源": {"target": "data_source", "type": "string"},
               "运费": {"target": "freight_amount", "type": "number"},
               "补贴": {"target": "subsidy_amount", "type": "number"},
               "税额": {"target": "tax_amount", "type": "number"},
               "优惠金额": {"target": "discount_amount", "type": "number"},
               "实付金额": {"target": "actual_amount", "type": "number"},
               "供应商": {"target": "supplier", "type": "string"},
               "备注": {"target": "remark", "type": "string"}},
        "keys": ("order_no", "sku"), "dup": True, "agg": "sum", "key3": "barcode",
        "conflict_mode": "sum",
    },
    "inventory": {
        "table": "inventory",
        "cols": ["SKU编码", "商品名称", "69码", "店铺", "仓库", "仓库类型", "可用库存",
                 "在途库存", "调拨在途", "安全库存", "锁定库存", "期初库存",
                 "本月入库", "本月出库", "本周入库", "本周出库", "重量", "体积", "渠道", "快照可用"],
        "mp": {"SKU编码": {"target": "sku", "type": "string"},
               "商品名称": {"target": "product_name", "type": "string"},
               "69码": {"target": "barcode", "type": "string"},
               "店铺": {"target": "store", "type": "string"},
               "仓库": {"target": "warehouse", "type": "string"},
               "仓库类型": {"target": "warehouse_type", "type": "string"},
               "可用库存": {"target": "available_qty", "type": "number"},
               "在途库存": {"target": "in_transit_qty", "type": "number"},
               "调拨在途": {"target": "c_transit", "type": "number"},
               "安全库存": {"target": "safety_qty", "type": "number"},
               "锁定库存": {"target": "locked_qty", "type": "number"},
               "期初库存": {"target": "beginning_stock", "type": "number"},
               "本月入库": {"target": "month_inbound", "type": "number"},
               "本月出库": {"target": "month_outbound", "type": "number"},
               "本周入库": {"target": "week_inbound", "type": "number"},
               "本周出库": {"target": "week_outbound", "type": "number"},
               "重量": {"target": "weight", "type": "number"},
               "体积": {"target": "volume", "type": "number"},
               "渠道": {"target": "channel", "type": "string"},
               "快照可用": {"target": "snapshot_qty", "type": "number"}},
        "keys": ("sku", "warehouse"), "dup": False, "agg": "sum", "key3": "barcode",
        "conflict_mode": "overwrite",
    },
    "inventory_b": {
        "table": "inventory",
        "cols": ["SKU编码", "商品名称", "69码", "仓库", "仓库类型", "可用库存", "在途库存",
                 "调拨在途", "渠道"],
        "mp": {"SKU编码": {"target": "sku", "type": "string"},
               "商品名称": {"target": "product_name", "type": "string"},
               "69码": {"target": "barcode", "type": "string"},
               "仓库": {"target": "warehouse", "type": "string"},
               "仓库类型": {"target": "warehouse_type", "type": "string"},
               "可用库存": {"target": "available_qty", "type": "number"},
               "在途库存": {"target": "in_transit_qty", "type": "number"},
               "调拨在途": {"target": "c_transit", "type": "number"},
               "渠道": {"target": "channel", "type": "string"}},
        "keys": ("sku", "warehouse"), "dup": False, "agg": "sum", "key3": "barcode",
        "conflict_mode": "overwrite",
    },
    "inbound": {
        "table": "inbound_records",
        "cols": ["SKU编码", "商品名称", "数量", "供应商", "入库日期", "生产日期", "到期日期",
                 "仓库", "69码", "平台", "渠道", "品牌", "店铺", "分类", "单价", "箱规",
                 "单位", "状态", "备注"],
        "mp": {"SKU编码": {"target": "sku", "type": "string"},
               "商品名称": {"target": "product_name", "type": "string"},
               "数量": {"target": "quantity", "type": "number"},
               "供应商": {"target": "supplier", "type": "string"},
               "入库日期": {"target": "inbound_date", "type": "string"},
               "生产日期": {"target": "prod_date", "type": "string"},
               "到期日期": {"target": "exp_date", "type": "string"},
               "仓库": {"target": "warehouse", "type": "string"},
               "69码": {"target": "barcode", "type": "string"},
               "平台": {"target": "platform", "type": "string"},
               "渠道": {"target": "channel", "type": "string"},
               "品牌": {"target": "brand", "type": "string"},
               "店铺": {"target": "store", "type": "string"},
               "分类": {"target": "category", "type": "string"},
               "单价": {"target": "price", "type": "number"},
               "箱规": {"target": "box_qty", "type": "number"},
               "单位": {"target": "unit", "type": "string"},
               "状态": {"target": "status", "type": "string"},
               "备注": {"target": "remark", "type": "string"}},
        "keys": ("sku", "inbound_date"), "dup": True, "agg": "sum", "key3": "barcode",
        "conflict_mode": "sum",   # 与导入页默认一致(入库累加语义)
    },
    "outbound": {
        "table": "outbound_records",
        "cols": ["SKU编码", "商品名称", "数量", "出库日期", "调拨目标仓", "生产日期", "到期日期",
                 "仓库", "69码", "平台", "渠道", "品牌", "店铺", "分类", "单价", "箱规",
                 "单位", "状态", "备注"],
        "mp": {"SKU编码": {"target": "sku", "type": "string"},
               "商品名称": {"target": "product_name", "type": "string"},
               "数量": {"target": "quantity", "type": "number"},
               "出库日期": {"target": "outbound_date", "type": "string"},
               "调拨目标仓": {"target": "target_warehouse", "type": "string"},
               "生产日期": {"target": "prod_date", "type": "string"},
               "到期日期": {"target": "exp_date", "type": "string"},
               "仓库": {"target": "warehouse", "type": "string"},
               "69码": {"target": "barcode", "type": "string"},
               "平台": {"target": "platform", "type": "string"},
               "渠道": {"target": "channel", "type": "string"},
               "品牌": {"target": "brand", "type": "string"},
               "店铺": {"target": "store", "type": "string"},
               "分类": {"target": "category", "type": "string"},
               "单价": {"target": "price", "type": "number"},
               "箱规": {"target": "box_qty", "type": "number"},
               "单位": {"target": "unit", "type": "string"},
               "状态": {"target": "status", "type": "string"},
               "备注": {"target": "remark", "type": "string"}},
        "keys": ("sku", "outbound_date"), "dup": True, "agg": "sum", "key3": "barcode",
        "conflict_mode": "sum",
    },
}

# 落账层按 (渠道, 仓型) 切段时, 每段要发的文件
#   inventory = 库存快照(自有仓段才发, 平台段只发 B 仓快照)
_SEG_FILES = {
    ("jd", "platform"): ("inventory_b",),
    ("jd", "own"): ("inventory",),
    ("other", "platform"): (),
    ("other", "own"): ("inventory",),
}


def spec(target):
    """取某 target 的文件规格(缺 target 直接抛 —— 拼错名字不该静默生成空文件)"""
    if target not in _SPEC:
        raise KeyError("未定义的导入规格: %s (可选: %s)" % (target, "/".join(sorted(_SPEC))))
    return _SPEC[target]


def cols_of(target):
    return list(spec(target)["cols"])


def mapping_of(target):
    return spec(target)["mp"]


def files_for(channel, wh_group):
    """该落账段要发哪些导入文件"""
    return tuple(_SEG_FILES.get((channel, wh_group), ()))


# ── 行 → CSV 文本 ────────────────────────────────────────────────────────
def to_csv(target, rows):
    """内存行(目标列名) → CSV 文本(bytes)

    · 按规格的**中文表头顺序**输出, 行内缺列写空串(不跳到下一列) —— 真实导出文件就是这样,
      而且只有这样才能验证"缺列不影响映射";
    · 一律转成字符串由 csv 模块负责引号/转义(逗号、引号、换行都在商品名里真实存在);
    · 数值保持原始精度(不预先 round), 让导入侧的 _v 去做类型清洗 —— 这是链路的一部分。
    """
    import csv
    import io as _io
    sp = spec(target)
    cols = sp["cols"]
    mp = sp["mp"]
    out = []
    for r in rows:
        rec = []
        for c in cols:
            _tgt = str((mp.get(c) or {}).get("target") or "")
            _v = r.get(_tgt) if _tgt else None
            rec.append("" if _v is None else str(_v))
        out.append(rec)
    buf = _io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(out)
    return buf.getvalue().encode("utf-8")


# ── 实际走导入链路 ────────────────────────────────────────────────────────
def import_rows(target, channel, rows, conflict_sql="", label=""):
    """把内存行**当作导入文件**灌进正式链路, 返回导入侧的回执

    链路(每一段都是线上真实实现, 不是影子):
      1. to_csv        内存行 → 中文表头 CSV bytes
      2. _parse_table  解析(与用户上传同一函数, 同一编码/分隔符推断)
      3. _clean_rows   按 mapping 清洗(类型转换/空串归零/未知列 — 与导入同一函数)
      4. _write_rows   按 target 分派写入 —— 这一层是全链路的汇合点:
         补 69码(按档案)、订单四维键对齐、库存期初实盘校准、自有仓可用重算、
         批次 FIFO 重建、脏日标记、analysis_cache.invalidate(domain)
    返回: {"rows", "success", "failed", "errors"}
    """
    from routes.cleansing import _parse_table, _clean_rows
    from routes.cleanse_write import _write_rows

    if not rows:
        return {"rows": 0, "success": 0, "failed": 0, "errors": []}
    data = to_csv(target, rows)
    fname = "seed_%s_%s.csv" % (target, channel)
    _hdr, parsed = _parse_table(data, fname)
    sp = spec(target)
    cleaned = _clean_rows(parsed, sp["mp"])
    res = _write_rows(target, channel, spec(target)["conflict_mode"], cleaned,
                      conflict_sql=conflict_sql)
    # _write_rows 各分支返回形状不同: 出入库/库存返回 (s, f, ed), 其余 (s, f)
    if isinstance(res, tuple) and len(res) == 3:
        s, f, ed = res
    else:
        s, f = (res if isinstance(res, tuple) else (0, 0))
        ed = []
    if f:
        from routes.common import try_err
        try_err("seed_fill", "导入链路拒写 %s/%s%s: success=%d failed=%d 首个原因=%s"
                % (target, channel, ("/" + label) if label else "", s, f,
                   str((ed or [{}])[0].get("reason") or "")[:120]), None)
    return {"rows": len(parsed), "success": int(s or 0), "failed": int(f or 0),
            "errors": (ed or [])[:10]}


def probe_index(channel):
    """该渠道的档案索引(规格外的小工具: 生成器要给文件补品牌/分类/店铺等冗余列)

    只读一次, 而不是每行查一次 —— 21 万单的行级补字段会变成 21 万次往返。
    """
    idx = {}
    for r in query("SELECT sku, COALESCE(product_name,'') AS nm, COALESCE(barcode,'') AS bc, "
                   "COALESCE(store,'') AS st, COALESCE(category,'') AS ct, "
                   "COALESCE(brand,'') AS br, COALESCE(price,0) AS pr, "
                   "COALESCE(box_qty,0) AS bx, COALESCE(unit,'') AS un, "
                   "COALESCE(supplier_code,'') AS sc, COALESCE(expiry_managed,'') AS em "
                   "FROM products WHERE channel=%s", [channel]):
        idx[str(r.get("sku") or "")] = {
            "nm": r.get("nm"), "bc": r.get("bc"), "st": r.get("st"), "ct": r.get("ct"),
            "br": r.get("br"), "pr": r.get("pr"), "bx": r.get("bx"), "un": r.get("un"),
            "sc": r.get("sc"), "em": r.get("em")}
    return idx
