#!/usr/bin/env python3
"""本地化"名词表": 字段标签 / 列头 / 渠道 / 仓库 / 订单状态 / 效期档位。

为什么单独一份: 这些词在两处被消费 —— ① 页面表头与下拉(渲染即用) ② 清洗字段字典
(100+ 字段, 逐条塞 t() 会让 locale.zh.ts 膨胀到不可维护)。所以词条仍进 locale,
但**取词统一走本文件的函数**, 页面不再各自写 `t('fld.xxx')` 字面量。
"""
import os

ZH = {
    # ── 清洗导入字段(67 个字段名, 与 fields.ts 的 t 一一对应) ──
    "actual_amount": "实际支付",
    "amount": "金额",
    "arrived_time": "到账时间",
    "available_qty": "可用(自有仓=期初)",
    "barcode": "69码",
    "beginning_stock": "期初库存(自有仓)",
    "box_qty": "箱规",
    "brand": "品牌",
    "c_transit": "B-C调拨在途",
    "category": "分类",
    "channel": "渠道",
    "contact_person": "联系人",
    "contact_phone": "联系电话",
    "currency": "币种",
    "data_source": "数据来源(仅记录)",
    "discount": "折扣",
    "discount_amount": "店铺满减",
    "exp_date": "到期日期",
    "expiry_managed": "是否做效期管理",
    "flow_no": "流水号",
    "flow_type": "流水类型",
    "freight": "运费",
    "freight_amount": "运费(明细)",
    "in_transit_qty": "在途",
    "inbound_date": "入库日期",
    "locked_qty": "锁定库存",
    "month_inbound": "当月采购入库",
    "month_outbound": "当月出库",
    "note": "备注",
    "order_no": "订单号",
    "order_status": "订单状态",
    "ordered_at": "下单日期",
    "outbound_date": "出库日期",
    "paid_at": "入库日期",
    "platform": "平台",
    "price": "单价",
    "prod_date": "生产日期",
    "product_name": "商品名称",
    "quantity": "数量",
    "related_no": "关联单号",
    "remark": "备注",
    "safety_qty": "安全线",
    "score": "评分",
    "sender": "收货人",
    "sender_phone": "收货电话",
    "settled_amount": "结算金额",
    "shipped_at": "发货时间",
    "shop": "店铺",
    "sku": "SKU",
    "source_order_id": "原始单号",
    "spec": "规格",
    "status": "状态",
    "store": "店铺",
    "subsidy_amount": "平台补贴",
    "supplier": "供应商",
    "supplier_code": "供应商编码",
    "supplier_name": "供应商名称",
    "target_warehouse": "目标仓库",
    "tax_amount": "税费",
    "total_amount": "金额",
    "turnover_days": "在库周转",
    "unit": "单位",
    "unit_price": "单价",
    "volume": "体积/方",
    "warehouse": "仓库",
    "warehouse_type": "仓库类型",
    "weight": "箱重/KG",
}

EN = {
    "actual_amount": "Actual paid",
    "amount": "Amount",
    "arrived_time": "Arrived at",
    "available_qty": "Available (own = opening)",
    "barcode": "Barcode",
    "beginning_stock": "Opening stock (own)",
    "box_qty": "Units per box",
    "brand": "Brand",
    "c_transit": "B→C in transit",
    "category": "Category",
    "channel": "Channel",
    "contact_person": "Contact",
    "contact_phone": "Phone",
    "currency": "Currency",
    "data_source": "Source (log only)",
    "discount": "Discount",
    "discount_amount": "Store discount",
    "exp_date": "Expiry date",
    "expiry_managed": "Expiry managed",
    "flow_no": "Flow No.",
    "flow_type": "Flow type",
    "freight": "Freight",
    "freight_amount": "Freight (detail)",
    "in_transit_qty": "In transit",
    "inbound_date": "Inbound date",
    "locked_qty": "Locked",
    "month_inbound": "Purchased in (MTD)",
    "month_outbound": "Shipped out (MTD)",
    "note": "Note",
    "order_no": "Order No.",
    "order_status": "Order status",
    "ordered_at": "Order date",
    "outbound_date": "Outbound date",
    "paid_at": "Stock-in date",
    "platform": "Platform",
    "price": "Unit price",
    "prod_date": "Production date",
    "product_name": "Product name",
    "quantity": "Quantity",
    "related_no": "Related No.",
    "remark": "Remark",
    "safety_qty": "Safety stock",
    "score": "Score",
    "sender": "Recipient",
    "sender_phone": "Recipient phone",
    "settled_amount": "Settled amount",
    "shipped_at": "Shipped at",
    "shop": "Shop",
    "sku": "SKU",
    "source_order_id": "Source order No.",
    "spec": "Spec",
    "status": "Status",
    "store": "Store",
    "subsidy_amount": "Platform subsidy",
    "supplier": "Supplier",
    "supplier_code": "Supplier code",
    "supplier_name": "Supplier name",
    "target_warehouse": "Target warehouse",
    "tax_amount": "Tax",
    "total_amount": "Amount",
    "turnover_days": "Stock turnover",
    "unit": "Unit",
    "unit_price": "Unit price",
    "volume": "Volume (m³)",
    "warehouse": "Warehouse",
    "warehouse_type": "Warehouse type",
    "weight": "Weight (kg)",
}


def emit(name: str, obj: dict, header: str) -> str:
    lines = [header, f"export const {name}: Record<string, string> = {{"]
    for k, v in obj.items():
        lines.append(f"  {k}: '{v}',")
    lines.append("}")
    return "\n".join(lines)


zh_src = emit("FLD_ZH", ZH, "")
en_src = emit("FLD_EN", EN, "")

out = '''/**
 * 名词表: 清洗导入字段标签(67 个字段名 × 中英)。
 *
 * 与 `pages/cleansing/fields.ts` 的 `t` 字段一一对应 —— 那边 `l` 只存字段名, 渲染时用
 * `fieldLabel(name)` 出双语。加字段: ① fields.ts 加一行 ② 本文件两个表各加一行,
 * `scripts/i18n_audit.py` 会校验三处(字段集合 ⊆ 词条集合)是否对齐。
 *
 * 不放进 locale.zh.ts 的原因: 字段名是**数据字典**不是界面文案, 混进通用字典会让
 * 词条表被 130 行字段条目淹没, 且新增导入字段时必须改通用字典, 容易漏。
 */

''' + zh_src + "\n\n" + en_src + '''

/** 字段标签: 键 = 字段名(fields.ts 的 t), 缺条目时回落到字段名本身(不静默显示中文) */
export function fieldLabel(name: string): string {
  return t('fld.' + name)
}

/** 取当前语言表中的字段标签(供不便引 t() 的纯数据场景) */
export function fieldLabelIn(locale: 'zh' | 'en', name: string): string {
  return (locale === 'zh' ? FLD_ZH : FLD_EN)[name] || name
}
'''

# 把 fld.* 词条同步进 locale.zh.ts / locale.en.ts
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for fname, table, flag in (("locale.zh.ts", ZH, "中文"), ("locale.en.ts", EN, "英文")):
    p = os.path.join(root, "frontend/src", fname)
    src = open(p, encoding="utf-8").read()
    add = "".join(f"  'fld.{k}': '{v}',\n" for k, v in table.items())
    marker = "  // ── 清洗导入字段(67 个, 与 pages/cleansing/fields.ts 同名)──\n"
    if "fld." not in src:
        idx = src.rstrip().rfind("}")
        src = src[:idx] + marker + add + src[idx:]
    open(p, "w", encoding="utf-8").write(src)

print("字段词条:", len(ZH), len(EN))
