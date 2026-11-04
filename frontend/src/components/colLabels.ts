/**
 * 列头词条(与 `components/hammer/configs.ts` 的列 id 一一对应)。
 *
 * 列定义只存 id 与宽度, 标签由 `colLabel(id)` 出双语 —— 表头、列选择面板、锤子菜单
 * 三处共用同一个 id, 改文案只改这里。加列: configs.ts 加一行 + 本文件两张表各加一行,
 * `scripts/i18n_audit.py` 会校验列 id 集合与词条集合同步。
 */
import { t } from '../locale'

export const COL_ZH: Record<string, string> = {
  cat: '分类',
  box: '箱规',
  expiry_managed: '是否效期管理',
  code: '编号',
  contact: '联系人',
  phone: '手机',
  date: '下单日期',
  begin: '期初库存',
  month_in: '当月采购入库',
  month_out: '当月出库',
  batch_days: '总效期/天',
  eff_status: '效期状态',
  over_third: '超1/3',
  avail: '可用',
  keep_days: '库存可撑天数',
  stock_amount: '在库金额',
  b_transit: '供应商-B仓',
  b_stock: 'B仓可用库存',
  b_turn: 'B仓周转',
  c_stock: 'C仓总和可用',
  c_turn: 'C仓周转',
  transit_turn: 'B→C调拨周转',
  suggest: 'C仓建议补',
  b_suggest: 'B仓需补',
  cur_turn: '当前综转',
  after_turn: '补后综转',
  action: '标记操作（用于B仓统计入库批次）',
  // 2026-11-04 语义根治: store=店铺(不是仓库), 与 locale 的 col.store 一致
  store: '店铺',
  safety: '安全线',
  turn: '在库周转',
  sales: '日销',
  sys_available: '可用',
  sys_transit: '在途',
  daily_sales: '日销(融合/14/28)',
  actual_purchase: '建议采购(含箱规取整)',
  after_turnover: '补后周转',
  timing: '采购时机',
  processed: '处理',
  days: '未售天数',
  stock: '库存',
  level: '等级',
  range_to: '至',
}

export const COL_EN: Record<string, string> = {
  cat: 'Category',
  box: 'Per box',
  expiry_managed: 'Expiry managed',
  code: 'Code',
  contact: 'Contact',
  phone: 'Mobile',
  date: 'Order date',
  begin: 'Opening stock',
  month_in: 'Inbound (MTD)',
  month_out: 'Outbound (MTD)',
  batch_days: 'Shelf life (d)',
  eff_status: 'Expiry status',
  over_third: 'Over 1/3',
  avail: 'Available',
  keep_days: 'Days of cover',
  stock_amount: 'Stock value',
  b_transit: 'Supplier → B',
  b_stock: 'B available',
  b_turn: 'B turnover',
  c_stock: 'C total available',
  c_turn: 'C turnover',
  transit_turn: 'B→C transfer turnover',
  suggest: 'Suggest to C',
  b_suggest: 'B needs',
  cur_turn: 'Current turnover',
  after_turn: 'After turnover',
  action: 'Mark (for B inbound batches)',
  // 2026-11-04 语义根治(用户定案): store=店铺, warehouse=仓库, 两列不许再混用。
  //   此表是 col.* 词条缺失时的回落, 原先写 'Warehouse' 会把错语义带回来。
  store: 'Store',
  safety: 'Safety stock',
  turn: 'Turnover',
  sales: 'Daily sales',
  sys_available: 'Available',
  sys_transit: 'In transit',
  daily_sales: 'Daily sales (blend/14/28)',
  actual_purchase: 'Suggested purchase (box rounded)',
  after_turnover: 'After turnover',
  timing: 'Purchase timing',
  processed: 'Handled',
  days: 'Days unsold',
  stock: 'Stock',
  level: 'Level',
  range_to: '–',
}

/** 列头(当前语言); 空 id(序号列)返回空串, 缺词条回落 id 本身 */
export function colLabel(id: string): string {
  return id ? t('col.' + id) : ''
}

/** 列头(指定语言), 供导出/纯数据场景 */
export function colLabelIn(locale: 'zh' | 'en', id: string): string {
  return id ? (locale === 'zh' ? COL_ZH : COL_EN)[id] || id : ''
}
