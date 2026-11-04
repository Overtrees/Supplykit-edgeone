"""列头词条(仓库类型 / 商品 / 供应商 / 订单 / 进销存 / 建议四张表) —— zh/en 两份。

为什么集中在这里: 列定义 (`components/hammer/configs.ts`) 是纯数据, 页面与锤子菜单
都读它的 `id`; 标签若写死在数据里, 切 EN 时表头与列选择面板同时变中文。
所以数据只存 `id`, 显示一律 `colLabel(id)`。
"""
import os

ZH = {
    # 商品
    "cat": "分类", "box": "箱规", "expiry_managed": "是否效期管理",
    # 供应商
    "code": "编号", "contact": "联系人", "phone": "手机",
    # 订单
    "date": "下单日期",
    # 进销存 · 通用
    "begin": "期初库存", "month_in": "当月采购入库", "month_out": "当月出库",
    "batch_days": "总效期/天", "eff_status": "效期状态", "over_third": "超1/3",
    "avail": "可用", "keep_days": "库存可撑天数", "stock_amount": "在库金额",
    "b_transit": "供应商-B仓", "b_stock": "B仓可用库存", "b_turn": "B仓周转",
    "c_stock": "C仓总和可用", "c_turn": "C仓周转", "transit_turn": "B→C调拨周转",
    "suggest": "C仓建议补", "b_suggest": "B仓需补", "cur_turn": "当前综转",
    "after_turn": "补后综转", "action": "标记操作（用于B仓统计入库批次）",
    # 建议 · 补货(traditional)
    "store": "仓库", "safety": "安全线", "turn": "在库周转", "sales": "日销",
    # 建议 · 采购
    "sys_available": "可用", "sys_transit": "在途", "daily_sales": "日销(融合/14/28)",
    "actual_purchase": "建议采购(含箱规取整)", "after_turnover": "补后周转", "timing": "采购时机",
    # 建议 · 滞销
    "processed": "处理", "days": "未售天数", "stock": "库存", "level": "等级",
    # 月份区间(表头第二行)
    "range_to": "至",
}

EN = {
    "cat": "Category", "box": "Per box", "expiry_managed": "Expiry managed",
    "code": "Code", "contact": "Contact", "phone": "Mobile",
    "date": "Order date",
    "begin": "Opening stock", "month_in": "Inbound (MTD)", "month_out": "Outbound (MTD)",
    "batch_days": "Shelf life (d)", "eff_status": "Expiry status", "over_third": "Over 1/3",
    "avail": "Available", "keep_days": "Days of cover", "stock_amount": "Stock value",
    "b_transit": "Supplier → B", "b_stock": "B available", "b_turn": "B turnover",
    "c_stock": "C total available", "c_turn": "C turnover", "transit_turn": "B→C transfer turnover",
    "suggest": "Suggest to C", "b_suggest": "B needs", "cur_turn": "Current turnover",
    "after_turn": "After turnover", "action": "Mark (for B inbound batches)",
    "store": "Warehouse", "safety": "Safety stock", "turn": "Turnover", "sales": "Daily sales",
    "sys_available": "Available", "sys_transit": "In transit", "daily_sales": "Daily sales (blend/14/28)",
    "actual_purchase": "Suggested purchase (box rounded)", "after_turnover": "After turnover",
    "timing": "Purchase timing",
    "processed": "Handled", "days": "Days unsold", "stock": "Stock", "level": "Level",
    "range_to": "–",
}


def emit(name, obj):
    lines = [f"export const {name}: Record<string, string> = {{"]
    for k, v in obj.items():
        lines.append(f"  {k}: '{v}',")
    lines.append("}")
    return "\n".join(lines)


head = '''/**
 * 列头词条(与 `components/hammer/configs.ts` 的列 id 一一对应)。
 *
 * 列定义只存 id 与宽度, 标签由 `colLabel(id)` 出双语 —— 表头、列选择面板、锤子菜单
 * 三处共用同一个 id, 改文案只改这里。加列: configs.ts 加一行 + 本文件两张表各加一行,
 * `scripts/i18n_audit.py` 会校验列 id 集合与词条集合同步。
 */
import { t } from '../../locale'

'''
out = head + emit("COL_ZH", ZH) + "\n\n" + emit("COL_EN", EN) + '''

/** 列头(当前语言); 空 id(序号列)返回空串, 缺词条回落 id 本身 */
export function colLabel(id: string): string {
  return id ? t('col.' + id) : ''
}

/** 列头(指定语言), 供导出/纯数据场景 */
export function colLabelIn(locale: 'zh' | 'en', id: string): string {
  return id ? (locale === 'zh' ? COL_ZH : COL_EN)[id] || id : ''
}
'''
open('frontend/src/components/colLabels.ts', 'w', encoding='utf-8').write(out)

# 词条并入 locale
for fname, table in (("locale.zh.ts", ZH), ("locale.en.ts", EN)):
    p = os.path.join('frontend/src', fname)
    s = open(p, encoding='utf-8').read()
    if "'col.begin'" in s:
        continue
    add = "  // ── 表格列头(与 components/hammer/configs.ts 列 id 同名)──\n"
    add += "".join(f"  'col.{k}': '{v}',\n" for k, v in table.items())
    idx = s.rstrip().rfind("}")
    s = s[:idx] + add + s[idx:]
    open(p, 'w', encoding='utf-8').write(s)
print("cols:", len(ZH), len(EN))
