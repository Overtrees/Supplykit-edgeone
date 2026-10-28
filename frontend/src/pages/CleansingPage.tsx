import {
  IconLightning,
  IconCheck,
  IconAlert,
  IconLoading,
  IconFolder,
  IconClose,
  IconWarning,
  IconClipboard,
} from '../components/Icons'
import React, { useState, useEffect, useRef } from 'react'
import { api, uploadRawBytes } from '../api/client'
import { reportError } from '../api/logger'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import ConfirmDialog from '../components/ConfirmDialog'
import { t } from '../locale'

const INV_FIELDS = [
  { t: 'warehouse', l: '仓库', tp: 'string' },
  { t: 'sku', l: 'SKU', tp: 'string' },
  { t: 'barcode', l: '69码', tp: 'string' },
  { t: 'product_name', l: '商品', tp: 'string' },
  { t: 'channel', l: '平台', tp: 'string' },
  { t: 'brand', l: '品牌', tp: 'string' },
  { t: 'price', l: '单价', tp: 'number' },
  { t: 'beginning_stock', l: '期初库存', tp: 'number' },
  { t: 'in_transit_qty', l: '在途', tp: 'number' },
  { t: 'month_inbound', l: '当月采购入库', tp: 'number' },
  { t: 'month_outbound', l: '当月出库', tp: 'number' },
  { t: 'available_qty', l: '可用', tp: 'number' },
  { t: 'turnover_days', l: '在库周转', tp: 'number' },
  { t: 'c_transit', l: 'B-C调拨在途', tp: 'number' },
  { t: 'locked_qty', l: '锁定库存', tp: 'number' },
  { t: 'safety_qty', l: '安全线', tp: 'number' },
  { t: 'weight', l: '箱重/KG', tp: 'number' },
  { t: 'volume', l: '体积/方', tp: 'number' },
  { t: 'note', l: '备注', tp: 'string' },
]
const PROD_FIELDS = [
  { t: 'sku', l: 'SKU', tp: 'string' },
  { t: 'barcode', l: '69码', tp: 'string' },
  { t: 'channel', l: '平台', tp: 'string' },
  { t: 'product_name', l: '名称', tp: 'string' },
  { t: 'store', l: '店铺', tp: 'string' },
  { t: 'category', l: '分类', tp: 'string' },
  { t: 'brand', l: '品牌', tp: 'string' },
  { t: 'price', l: '单价', tp: 'number' },
  { t: 'box_qty', l: '箱规', tp: 'number' },
  { t: 'unit', l: '单位', tp: 'string' },
  { t: 'weight', l: '箱重/KG', tp: 'number' },
  { t: 'volume', l: '体积/方', tp: 'number' },
  { t: 'status', l: '状态', tp: 'string' },
]
const SYS_FIELDS = [
  { t: 'order_no', l: '订单号', tp: 'string' },
  { t: 'barcode', l: '69码', tp: 'string' },
  { t: 'store', l: '店铺', tp: 'string' },
  { t: 'warehouse', l: '仓库', tp: 'string' },
  { t: 'product_name', l: '商品', tp: 'string' },
  { t: 'total_amount', l: '金额', tp: 'number' },
  { t: 'order_status', l: '状态', tp: 'string' },
  { t: 'ordered_at', l: '下单日期', tp: 'date' },
  { t: 'paid_at', l: '入库日期', tp: 'date' },
  { t: 'prod_date', l: '生产日期(可选,入库记录)', tp: 'date' },
  { t: 'exp_date', l: '到期日期(可选,入库记录)', tp: 'date' },
  { t: 'source_order_id', l: '原始单号', tp: 'string' },
  { t: 'sku', l: 'SKU', tp: 'string' },
  { t: 'quantity', l: '数量', tp: 'number' },
  { t: 'unit_price', l: '单价', tp: 'number' },
  { t: 'supplier', l: '供应商', tp: 'string' },
  { t: 'supplier_code', l: '供应商编码', tp: 'string' },
  { t: 'remark', l: '备注', tp: 'string' },
  { t: 'platform', l: '平台', tp: 'string' },
  { t: 'shipped_at', l: '发货时间', tp: 'date' },
  { t: 'sender', l: '收货人', tp: 'string' },
  { t: 'sender_phone', l: '收货电话', tp: 'string' },
  { t: 'currency', l: '币种', tp: 'string' },
  { t: 'discount', l: '折扣', tp: 'number' },
  { t: 'freight', l: '运费', tp: 'number' },
  { t: 'category', l: '分类', tp: 'string' },
  { t: 'brand', l: '品牌', tp: 'string' },
  { t: 'spec', l: '规格', tp: 'string' },
  { t: 'weight', l: '箱重/KG', tp: 'number' },
  { t: 'volume', l: '体积/方', tp: 'number' },
  // GMV 金额明细(方案A): 运费/补贴/税费/满减/实付
  { t: 'freight_amount', l: '运费(明细)', tp: 'number' },
  { t: 'subsidy_amount', l: '平台补贴', tp: 'number' },
  { t: 'tax_amount', l: '税费', tp: 'number' },
  { t: 'discount_amount', l: '店铺满减', tp: 'number' },
  { t: 'actual_amount', l: '实际支付', tp: 'number' },
  // 供应商导入
  { t: 'supplier_name', l: '供应商名称', tp: 'string' },
  { t: 'contact_person', l: '联系人', tp: 'string' },
  { t: 'contact_phone', l: '联系电话', tp: 'string' },
  { t: 'score', l: '评分', tp: 'number' },
  { t: 'status', l: '状态', tp: 'string' },
  // 进销存B仓
  { t: 'c_transit', l: 'B-C调拨在途', tp: 'number' },
]
const ALIAS = {
  订单号: 'order_no',
  订单编号: 'order_no',
  采购单号: 'order_no',
  原始单号: 'source_order_id',
  外部单号: 'source_order_id',
  平台订单号: 'source_order_id',
  商品编号: 'sku',
  货号: 'sku',
  SKU: 'sku',
  商品名称: 'product_name',
  产品名称: 'product_name',
  名称: 'product_name',
  数量: 'quantity',
  采购数量: 'quantity',
  订货数量: 'quantity',
  原始采购数量: 'quantity',
  单价: 'unit_price',
  价格: 'unit_price',
  采购价格: 'unit_price',
  金额: 'total_amount',
  总金额: 'total_amount',
  采购金额: 'total_amount',
  实收金额: 'total_amount',
  店铺: 'store',
  店铺名: 'store',
  门店: 'store',
  仓库: 'warehouse',
  京东仓库: 'warehouse',
  发货仓: 'warehouse',
  状态: 'order_status',
  订单状态: 'order_status',
  日期: 'ordered_at',
  订购时间: 'ordered_at',
  下单时间: 'ordered_at',
  入库时间: 'paid_at',
  供应商: 'supplier',
  供应商名称: 'supplier_name',
  供应商编码: 'supplier_code',
  供应商编号: 'supplier_code',
  供应商简码: 'supplier_code',
  联系人: 'contact_person',
  联系电话: 'contact_phone',
  评分: 'score',
  运费: 'freight_amount',
  折扣: 'discount_amount',
  平台补贴: 'subsidy_amount',
  税费: 'tax_amount',
  税额: 'tax_amount',
  实际支付: 'actual_amount',
  实付金额: 'actual_amount',
  'B-C调拨在途': 'c_transit',
  调拨在途: 'c_transit',
  商品状态: 'status',
  在售: 'status',
  停用: 'status',
  备注: 'remark',
  平台: 'platform',
  订单来源: 'platform',
  来源: 'platform',
  收货人: 'sender',
  收货负责人: 'sender',
  收货电话: 'sender_phone',
  电话: 'sender_phone',
  币种: 'currency',
  货币: 'currency',
  品牌: 'brand',
  规格: 'spec',
  分类: 'category',
  商品分类: 'category',
}

// 各导入类型专属字段集(下拉按类型过滤, 避免订单页出现库存/出入库无关字段)
const ORDER_FIELDS = [
  { t: 'order_no', l: '订单号', tp: 'string' },
  { t: 'source_order_id', l: '原始单号', tp: 'string' },
  { t: 'store', l: '店铺', tp: 'string' },
  { t: 'warehouse', l: '仓库', tp: 'string' },
  { t: 'sku', l: 'SKU', tp: 'string' },
  { t: 'barcode', l: '69码', tp: 'string' },
  { t: 'product_name', l: '商品', tp: 'string' },
  { t: 'quantity', l: '数量', tp: 'number' },
  { t: 'unit_price', l: '单价', tp: 'number' },
  { t: 'total_amount', l: '金额', tp: 'number' },
  { t: 'discount_amount', l: '折扣', tp: 'number' },
  { t: 'freight_amount', l: '运费', tp: 'number' },
  { t: 'subsidy_amount', l: '平台补贴', tp: 'number' },
  { t: 'tax_amount', l: '税费', tp: 'number' },
  { t: 'actual_amount', l: '实付金额', tp: 'number' },
  { t: 'order_status', l: '状态', tp: 'string' },
  { t: 'ordered_at', l: '下单日期', tp: 'date' },
  { t: 'paid_at', l: '入库日期', tp: 'date' },
  { t: 'shipped_at', l: '发货时间', tp: 'date' },
  { t: 'platform', l: '平台', tp: 'string' },
  { t: 'channel', l: '渠道', tp: 'string' },
  { t: 'data_source', l: '数据来源', tp: 'string' },
  { t: 'supplier', l: '供应商', tp: 'string' },
  { t: 'sender', l: '收货人', tp: 'string' },
  { t: 'sender_phone', l: '收货电话', tp: 'string' },
  { t: 'remark', l: '备注', tp: 'string' },
]
const INBOUND_FIELDS = [
  { t: 'sku', l: 'SKU', tp: 'string' },
  { t: 'barcode', l: '69码', tp: 'string' },
  { t: 'product_name', l: '商品', tp: 'string' },
  { t: 'brand', l: '品牌', tp: 'string' },
  { t: 'store', l: '店铺', tp: 'string' },
  { t: 'category', l: '分类', tp: 'string' },
  { t: 'quantity', l: '数量', tp: 'number' },
  { t: 'supplier', l: '供应商', tp: 'string' },
  { t: 'inbound_date', l: '入库日期', tp: 'date' },
  { t: 'channel', l: '渠道', tp: 'string' },
  { t: 'platform', l: '平台', tp: 'string' },
  { t: 'warehouse', l: '仓库', tp: 'string' },
  { t: 'prod_date', l: '生产日期', tp: 'date' },
  { t: 'exp_date', l: '到期日期', tp: 'date' },
  { t: 'price', l: '单价', tp: 'number' },
  { t: 'box_qty', l: '箱规', tp: 'number' },
  { t: 'unit', l: '单位', tp: 'string' },
  { t: 'weight', l: '重量/KG', tp: 'number' },
  { t: 'volume', l: '体积/方', tp: 'number' },
  { t: 'status', l: '状态', tp: 'string' },
  { t: 'remark', l: '备注', tp: 'string' },
]
const OUTBOUND_FIELDS = [
  { t: 'sku', l: 'SKU', tp: 'string' },
  { t: 'barcode', l: '69码', tp: 'string' },
  { t: 'product_name', l: '商品', tp: 'string' },
  { t: 'brand', l: '品牌', tp: 'string' },
  { t: 'store', l: '店铺', tp: 'string' },
  { t: 'category', l: '分类', tp: 'string' },
  { t: 'quantity', l: '数量', tp: 'number' },
  { t: 'target_warehouse', l: '目标仓库', tp: 'string' },
  { t: 'outbound_date', l: '出库日期', tp: 'date' },
  { t: 'channel', l: '渠道', tp: 'string' },
  { t: 'platform', l: '平台', tp: 'string' },
  { t: 'warehouse', l: '仓库', tp: 'string' },
  { t: 'prod_date', l: '生产日期', tp: 'date' },
  { t: 'exp_date', l: '到期日期', tp: 'date' },
  { t: 'price', l: '单价', tp: 'number' },
  { t: 'box_qty', l: '箱规', tp: 'number' },
  { t: 'unit', l: '单位', tp: 'string' },
  { t: 'weight', l: '重量/KG', tp: 'number' },
  { t: 'volume', l: '体积/方', tp: 'number' },
  { t: 'status', l: '状态', tp: 'string' },
  { t: 'remark', l: '备注', tp: 'string' },
]
const SUPPLIER_FIELDS = [
  { t: 'supplier_code', l: '供应商编码', tp: 'string' },
  { t: 'supplier_name', l: '供应商名称', tp: 'string' },
  { t: 'contact_person', l: '联系人', tp: 'string' },
  { t: 'contact_phone', l: '联系电话', tp: 'string' },
  { t: 'score', l: '评分', tp: 'number' },
  { t: 'status', l: '状态', tp: 'string' },
  { t: 'channel', l: '渠道', tp: 'string' },
  { t: 'brand', l: '品牌', tp: 'string' },
]
// 2026-10-16 GMV 回款(资金流水——平台账单/对账单——payout 到账时间归属)
const GMV_FLOW_FIELDS = [
  { t: 'platform', l: '平台', tp: 'string' },
  { t: 'shop', l: '店铺', tp: 'string' },
  { t: 'flow_no', l: '流水号', tp: 'string' },
  { t: 'related_no', l: '关联单号', tp: 'string' },
  { t: 'arrived_time', l: '到账时间', tp: 'string' },
  { t: 'amount', l: '金额', tp: 'number' },
  { t: 'flow_type', l: '流水类型', tp: 'string' },
  { t: 'settled_amount', l: '结算金额', tp: 'number' },
  { t: 'data_source', l: '数据来源', tp: 'string' },
]
// 2026-10-27 单一字段源(导出供模板弹窗): 内置预设映射 + 下载模板表头均由此派生——
// 新列只需加进对应 X_FIELDS, 预设/下载模板自动同步(零漂移)
export const TARGET_FIELDS = {
  order: ORDER_FIELDS,
  inventory: INV_FIELDS,
  platform_inv: INV_FIELDS,
  inventory_b: INV_FIELDS,
  product: PROD_FIELDS,
  supplier: SUPPLIER_FIELDS,
  inbound: INBOUND_FIELDS,
  outbound: OUTBOUND_FIELDS,
  gmv_flow: GMV_FLOW_FIELDS,
}
// 预设映射(内置): label→target 全字段(与下载模板表头同源)
export const presetMapping = (tt: string): Record<string, string> => {
  const fields = (TARGET_FIELDS as Record<string, any>)[tt] || []
  const m: Record<string, string> = {}
  for (const f of fields) m[f.l] = f.t
  return m
}

// 2026-10-27 模板必选字段(按业务导入链: 去重键 + 汇总必需):
// 订单=order_no|sku(+69码)三键去重, quantity 求和, ordered_at 归期;
// 库存=sku|warehouse 唯一键 + available_qty; 商品=sku+名称; 供应商=编码+名称;
// 入库/出库=sku+数量+日期; GMV回款=platform+flow_no 去重键(缺失整批跳过)+amount
export const PRESET_REQUIRED: Record<string, string[]> = {
  order: ['order_no', 'sku', 'quantity', 'ordered_at'],
  inventory: ['sku', 'warehouse', 'available_qty'],
  platform_inv: ['sku', 'warehouse', 'available_qty'],
  inventory_b: ['sku', 'warehouse', 'available_qty'],
  product: ['sku', 'product_name'],
  supplier: ['supplier_code', 'supplier_name'],
  inbound: ['sku', 'quantity', 'inbound_date'],
  outbound: ['sku', 'quantity', 'outbound_date'],
  gmv_flow: ['platform', 'flow_no', 'amount', 'arrived_time'],
}
// 示例行(仅必选列给样例, 可选列留空)——下载模板第二行
const PRESET_EXAMPLE: Record<string, Record<string, string>> = {
  order: {
    order_no: 'JD-20261022-0001',
    sku: 'SKU-0001-J',
    quantity: '2',
    ordered_at: '2026-10-22 10:00:00',
  },
  inventory: { sku: 'SKU-0001-J', warehouse: '华东C仓', available_qty: '100' },
  platform_inv: { sku: 'SKU-0001-J', warehouse: '京东仓', available_qty: '100' },
  inventory_b: { sku: 'SKU-0001-J', warehouse: 'B仓', available_qty: '100' },
  product: { sku: 'SKU-0001-J', product_name: '示例商品' },
  supplier: { supplier_code: 'SUP-001', supplier_name: '示例供应商' },
  inbound: { sku: 'SKU-0001-J', quantity: '50', inbound_date: '2026-10-22' },
  outbound: { sku: 'SKU-0001-J', quantity: '10', outbound_date: '2026-10-22' },
  gmv_flow: {
    platform: '京东',
    flow_no: 'FLOW-20261022-001',
    amount: '99.00',
    arrived_time: '2026-10-22 10:00:00',
  },
}
export const presetExample = (tt: string): Record<string, string> => PRESET_EXAMPLE[tt] || {}
export const presetRequired = (tt: string): string[] => PRESET_REQUIRED[tt] || []
// 导入类型中文名(下载文件名/预设标题)
export const DOC_TYPE_NAMES: Record<string, string> = {
  order: '订单',
  inventory: '库存',
  platform_inv: '平台库存',
  inventory_b: 'B仓库存',
  product: '商品',
  supplier: '供应商',
  inbound: '入库',
  outbound: '出库',
  gmv_flow: 'GMV回款',
}
const TARGET_TARGETS = {}
for (const _k in TARGET_FIELDS) TARGET_TARGETS[_k] = TARGET_FIELDS[_k].map(f => f.t)

// 归一化别名扩展(列名兼容: 英文/变体/去空格符号; 供自动识别第二个匹配层)
const _nk = s =>
  String(s || '')
    .toLowerCase()
    .replace(/[\s_\-/（）()【】·]/g, '')
const ALIAS_EXT = {
  订单号: 'order_no',
  订单编号: 'order_no',
  采购单号: 'order_no',
  订单id: 'order_no',
  单号: 'order_no',
  单据编号: 'order_no',
  orderno: 'order_no',
  orderid: 'order_no',
  order_no: 'order_no',
  商品编号: 'sku',
  货号: 'sku',
  商品编码: 'sku',
  sku: 'sku',
  itemid: 'sku',
  item_id: 'sku',
  客服sku: 'sku',
  商品名称: 'product_name',
  产品名称: 'product_name',
  名称: 'product_name',
  品名: 'product_name',
  productname: 'product_name',
  itemname: 'product_name',
  product_name: 'product_name',
  货品名称: 'product_name',
  数量: 'quantity',
  采购数量: 'quantity',
  订货数量: 'quantity',
  件数: 'quantity',
  qty: 'quantity',
  quantity: 'quantity',
  订购数量: 'quantity',
  单价: 'unit_price',
  价格: 'unit_price',
  采购价格: 'unit_price',
  unitprice: 'unit_price',
  price: 'unit_price',
  金额: 'total_amount',
  总金额: 'total_amount',
  采购金额: 'total_amount',
  实收金额: 'total_amount',
  总额: 'total_amount',
  订单金额: 'total_amount',
  totalamount: 'total_amount',
  amount: 'total_amount',
  total: 'total_amount',
  店铺: 'store',
  店铺名: 'store',
  门店: 'store',
  store: 'store',
  店铺名称: 'store',
  仓库: 'warehouse',
  京东仓库: 'warehouse',
  发货仓: 'warehouse',
  发货仓库: 'warehouse',
  所属仓库: 'warehouse',
  warehouse: 'warehouse',
  仓库名称: 'warehouse',
  状态: 'order_status',
  订单状态: 'order_status',
  orderstatus: 'order_status',
  order_status: 'order_status',
  status: 'order_status',
  交易状态: 'order_status',
  日期: 'ordered_at',
  订购时间: 'ordered_at',
  下单时间: 'ordered_at',
  订单日期: 'ordered_at',
  下单日期: 'ordered_at',
  订购日期: 'ordered_at',
  下单时间time: 'ordered_at',
  date: 'ordered_at',
  orderdate: 'ordered_at',
  orderedat: 'ordered_at',
  供应商: 'supplier',
  supplier: 'supplier',
  供应商名称: 'supplier_name',
  供应商编码: 'supplier_code',
  供应商编号: 'supplier_code',
  联系人: 'contact_person',
  联系电话: 'contact_phone',
  评分: 'score',
  备注: 'remark',
  remark: 'remark',
  平台: 'platform',
  platform: 'platform',
  订单来源: 'platform',
  生产日期: 'prod_date',
  生产日: 'prod_date',
  proddate: 'prod_date',
  生产年月: 'prod_date',
  到期日期: 'exp_date',
  到期日: 'exp_date',
  失效日期: 'exp_date',
  有效期至: 'exp_date',
  expdate: 'exp_date',
  保质期至: 'exp_date',
  入库日期: 'paid_at',
  入库时间: 'paid_at',
  支付时间: 'paid_at',
  paidat: 'paid_at',
  实际入库日期: 'paid_at',
  出库日期: 'outbound_date',
  outdate: 'outbound_date',
  出库时间: 'outbound_date',
  品牌: 'brand',
  brand: 'brand',
  分类: 'category',
  商品分类: 'category',
  category: 'category',
  品类: 'category',
}

export default function CleansingPage() {
  const toast = useToast()
  const [s, setS] = useState(0)
  const [f, setF] = useState(null)
  const [fBuf, setFBuf] = useState<ArrayBuffer | null>(null) // 间断 422 根治: iOS multipart body 空——raw bytes 直传
  const [cols, setCols] = useState([])
  const [tr, setTr] = useState(0)
  const [tt, setTt] = useState('order')
  const { setHammerCleansingTarget, hammerCleansingConflict } = useAppStore()
  useEffect(() => {
    setHammerCleansingTarget(tt)
  }, [tt])
  const { hammerCleansingChannel: ch, setHammerCleansingChannel: setCh } = useAppStore()
  const goStep = n => {
    setS(n)
    useAppStore.getState().setHammerCleansingStep(n)
  }
  // 列映射条目(导入清洗): target/t 为映射目标字段, 动态列配置走 unknown
  interface ColMapItem {
    target?: string
    t?: string
    [k: string]: unknown
  }
  const [mp, setMp] = useState<Record<string, ColMapItem>>({})
  // 映射页当前映射同步(模板管理弹窗保存用) + 模板应用/自定义字段变更事件
  useEffect(() => {
    ;(window as any).__curMp = mp
  }, [mp])
  useEffect(() => {
    const onApply = (e: any) => {
      const d = e.detail
      if (d && typeof d === 'object') setMp(d)
    }
    const onCfChanged = () => {
      try {
        setCf(JSON.parse(localStorage.getItem('c_cf') || '[]'))
      } catch {}
    }
    window.addEventListener('apply-template', onApply as any)
    window.addEventListener('custom-fields-changed', onCfChanged)
    return () => {
      window.removeEventListener('apply-template', onApply as any)
      window.removeEventListener('custom-fields-changed', onCfChanged)
    }
  }, [])
  const [pv, setPv] = useState(null)
  const [res, setRes] = useState(null)
  const [bs, setBs] = useState('')
  const [cf, setCf] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('c_cf') || '[]')
    } catch {
      return []
    }
  })
  const [templates, setTemplates] = useState([]) // 模板管理已移至锤子菜单弹窗(此处仅兼容引用)
  const saveCf = v => {
    setCf(v)
    try {
      localStorage.setItem('c_cf', JSON.stringify(v))
    } catch {}
  }

  const loadTemplates = async () => {
    try {
      const r = await api.get('/api/cleansing/templates')
      setTemplates(r.data || [])
    } catch (e) {}
  }
  useEffect(() => {
    loadTemplates()
  }, [])

  const addField = () =>
    saveCf([...cf, { t: 'field_' + Date.now(), l: '自定义字段', tp: 'string' }])
  const delField = i => saveCf(cf.filter((_, k) => k !== i))

  // 用户自定义别名记忆(核心): 手工映射过的 (列名→目标) 存 localStorage, 下次同列名自动识别
  const getAliasMap = () => {
    try {
      return JSON.parse(localStorage.getItem('c_alias_map') || '{}')
    } catch {
      return {}
    }
  }
  const saveAlias = (colName, target) => {
    try {
      const m = getAliasMap()
      if (target) m[colName] = target
      else delete m[colName]
      localStorage.setItem('c_alias_map', JSON.stringify(m))
    } catch {}
  }
  const detect = async (file, buf = null) => {
    // 2026-10-14 422 诊断: 发送前记录 file 状态(前端是否拿到/类型)——console 即时 + reportError 收口
    console.info(
      '[detect] file:',
      file && file.name,
      file && file.size,
      file && file.type,
      '| fisFile:',
      file instanceof File,
    )
    try {
      reportError(
        'detect_debug',
        'detect file: ' +
          (file ? String(file.name) + '|' + String(file.size) + '|' + String(file.type) : 'EMPTY'),
        'isFile=' + String(file instanceof File),
        'info',
      )
    } catch (err) {}
    if (!file) {
      console.warn('[detect] file 为空——未获取到文件对象')
    }
    setF(file)
    setBs('识别中')

    try {
      const r = await uploadRawBytes(
        '/api/cleansing/detect',
        buf || (fBuf ? new Blob([fBuf]) : file),
        60000,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || '识别失败')
        setBs('')
        return
      }
      setCols(d.columns || [])
      setTr(d.total || 0)
      const a = {}
      const customAlias = getAliasMap()
      ;(d.columns || []).forEach(c => {
        // 识别优先级: 用户自定义别名(历史手工映射记忆) → 系统精确 ALIAS → 归一化别名(英文/变体)
        // 目标必须属于当前导入类型字段集(避免订单页识别出库存/出入库无关字段)
        let key = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
        if (key && TARGET_TARGETS[tt] && !TARGET_TARGETS[tt].includes(key)) key = ''
        if (key) a[c.name] = { target: key, type: 'string' }
      })
      // 以用户已选类型为锚点展示对应字段集; 仅当当前类型 0 命中(上次类型残留/选错)时,
      // 按识别字段切到命中最多的类型——防订单文件显示库存字段集
      if (Object.keys(a).length === 0) {
        const _typeHits: Record<string, number> = {}
        for (const _t of Object.keys(TARGET_FIELDS)) _typeHits[_t] = 0
        ;(d.columns || []).forEach(c => {
          const _k0 = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
          if (_k0) {
            for (const _t of Object.keys(TARGET_FIELDS)) {
              if (TARGET_FIELDS[_t].some(_f => _f.t === _k0)) _typeHits[_t] += 1
            }
          }
        })
        const _best = Object.entries(_typeHits).sort((x, y) => y[1] - x[1])[0]
        if (_best && _best[1] >= 2) {
          setTt(_best[0])
          ;(d.columns || []).forEach(c => {
            let key = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
            if (key && TARGET_TARGETS[_best[0]] && !TARGET_TARGETS[_best[0]].includes(key)) key = ''
            if (key) a[c.name] = { target: key, type: 'string' }
          })
        }
      }
      setMp(a)
      goStep(1) // 停步映射字段界面, 用户确认/补映射后手动预览(智能识别仅辅助)
      setBs('')
      if (Object.keys(a).length > 0)
        toast.success(
          '已自动识别 ' + Object.keys(a).length + ' 列（未识别的列请在下方手工选择目标字段）',
        )
      else
        toast.warning(
          '未自动识别到映射列，请手工选择每列目标字段（选择后系统会记住，下次同列名自动识别）',
        )
    } catch (e) {
      toast.error(
        '请求异常: ' +
          e.message +
          (e.response?.data?.detail
            ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
            : ''),
      )
    }
    setBs('')
  }

  const preview = async () => {
    if (!f) {
      toast.error('请先选择文件后再预览')
      setBs('')
      return
    }
    setBs('预览中')
    try {
      const r = await uploadRawBytes(
        '/api/cleansing/preview?q_target=' +
          tt +
          '&q_channel=' +
          ch +
          '&q_conflict=' +
          hammerCleansingConflict +
          '&q_mp=' +
          encodeURIComponent(JSON.stringify(mp)),
        fBuf ? new Blob([fBuf]) : f,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || '预览失败')
        setBs('')
        return
      }
      setPv(d)
      goStep(2)
    } catch (e) {
      const msg = e.response?.data?.error || e.message || '请求失败'
      toast.error('预览失败: ' + msg)
    }
    setBs('')
  }

  // 表格列状态映射(清洗页锤子菜单入口; 渠道级隔离; 通用列值→档位, 当前 order_status)
  const [colMapOpen, setColMapOpen] = useState(false)
  const [colMap, setColMap] = useState({})
  const [colMapSaving, setColMapSaving] = useState(false)
  const [colMapCol, setColMapCol] = useState('order_status')
  const loadColMap = async () => {
    try {
      const r = await api.get(
        '/api/replenishment-config/column-value-map?channel=' + ch + '&t=' + Date.now(),
      )
      setColMap(r.data && typeof r.data === 'object' ? r.data : {})
    } catch (e) {}
  }
  useEffect(() => {
    const h = () => {
      loadColMap()
      setColMapOpen(true)
    }
    window.addEventListener('cleansing-colmap-open', h)
    return () => window.removeEventListener('cleansing-colmap-open', h)
  }, [ch])
  const saveColMap = async () => {
    setColMapSaving(true)
    try {
      await api.put('/api/replenishment-config/column-value-map?channel=' + ch, { items: colMap })
      toast.success('列映射已保存，导入时即时生效')
      setColMapOpen(false)
    } catch (e) {
      toast.error('保存失败: ' + (e.message || ''))
    }
    setColMapSaving(false)
  }
  const execLock = useRef(false)
  const [overlapAsk, setOverlapAsk] = useState<{ message: string } | null>(null)
  const doExecute = async () => {
    if (execLock.current) return
    execLock.current = true
    try {
      setBs('清洗中...')
      // 库存类型必须映射 warehouse 列（否则传统多仓无法按仓库核算）
      const isInvType = tt === 'inventory' || tt === 'platform_inv' || tt === 'inventory_b'
      if (isInvType) {
        const hasWarehouse = Object.values(mp || {}).some(
          m => m && (m.target === 'warehouse' || m.t === 'warehouse'),
        )
        if (!hasWarehouse) {
          toast.error('导入库存必须映射「仓库」列，否则传统多仓无法按仓库维度核算')
          setBs('')
          execLock.current = false
          return
        }
      }
      // 订单类型同样必须映射 warehouse 列（否则传统补货按仓库维度算日销，该订单销量会丢失）
      if (tt === 'order') {
        const hasWh = Object.values(mp || {}).some(
          m => m && (m.target === 'warehouse' || m.t === 'warehouse'),
        )
        if (!hasWh) {
          toast.error(
            '导入订单必须映射「仓库」列，否则传统补货模式按仓库维度核算日销时会丢失该订单销量',
          )
          setBs('')
          execLock.current = false
          return
        }
        // 2026-10-15 SKU 必映射(写入 key order_no+sku——sku 缺失整批跳过——5000 单无明细根因)
        const hasSku = Object.values(mp || {}).some(m => m && (m.target === 'sku' || m.t === 'sku'))
        if (!hasSku) {
          toast.error('导入订单必须映射「SKU/商品编号」列（写入唯一键），否则整批订单将被跳过')
          setBs('')
          execLock.current = false
          return
        }
      }
      // 2026-10-16 GMV 回款必填 key(platform+flow_no 去重键——缺失整批跳过——曾 failed=15 无因可查):
      // platform 平台 + flow_no 流水号 必须映射
      if (tt === 'gmv_flow') {
        const hasPlat = Object.values(mp || {}).some(
          m => m && (m.target === 'platform' || m.t === 'platform'),
        )
        if (!hasPlat) {
          toast.error('导入 GMV 回款必须映射「平台」列（与流水号组成去重键）')
          setBs('')
          execLock.current = false
          return
        }
        const hasFlow = Object.values(mp || {}).some(
          m => m && (m.target === 'flow_no' || m.t === 'flow_no'),
        )
        if (!hasFlow) {
          toast.error('导入 GMV 回款必须映射「流水号」列（与平台组成去重键），否则整批将被跳过')
          setBs('')
          execLock.current = false
          return
        }
      }
      // 2026-10-17 前端提前检测(职责: 检测拦截在前端/后端只写入): 三键优先 order_no|sku|barcode,
      // 空 barcode 落二键兜底——dup-check 纯只读秒级; 全重→拦截提示, 部分重叠→force_new 弹窗
      if (tt === 'order') {
        try {
          const _dc = await uploadRawBytes(
            '/api/cleansing/dup-check?q_target=order&q_channel=' +
              ch +
              '&q_mp=' +
              encodeURIComponent(JSON.stringify(mp)),
            fBuf ? new Blob([fBuf]) : f,
          )
          const dd = _dc.data
          if (dd && dd.ok !== false) {
            if (dd.duplicate) {
              setRes({
                success: 0,
                failed: dd.new + dd.overlap || 0,
                message: '文件已导入过（同键全部已存在），已跳过',
              })
              goStep(3)
              setBs('')
              execLock.current = false
              toast.warning('文件已导入过（同键全部已存在），已跳过')
              return
            }
            if (dd.overlap) {
              setOverlapAsk({
                message:
                  '部分数据已存在（已存在 ' +
                  dd.overlap +
                  ' 条，新增 ' +
                  dd.new +
                  ' 条）——确认只写新增？',
              })
              setBs('')
              execLock.current = false
              return
            }
          }
        } catch (e) {
          /* dup-check 降级放行——后端二键兜底 */
        }
      }
      try {
        const r = await uploadRawBytes(
          '/api/cleansing/execute-async?q_target=' +
            tt +
            '&q_channel=' +
            ch +
            '&q_conflict=' +
            hammerCleansingConflict +
            '&q_mp=' +
            encodeURIComponent(JSON.stringify(mp)),
          fBuf ? new Blob([fBuf]) : f,
        ) // raw 直传 90s
        const d = r.data
        if (!d.ok) {
          toast.error(d.error || '提交失败')
          setBs('')
          execLock.current = false
          return
        }
        // 同步结果(<1000行后端请求内完成返回success/failed——同步分支) → 直显, 不走轮询
        if (d.success !== undefined) {
          // 2026-10-15 重复提前检测: 后端返回 duplicate(全部同键已存在——二次导入同表)→ 跳过提示(非'清洗完成'——秒级拦截)
          if (d.duplicate) {
            setRes({ success: d.success, failed: d.failed, message: d.message || '文件已导入过' })
            goStep(3)
            setBs('')
            execLock.current = false
            toast.warning(d.message || '文件已导入过，跳过 ' + d.failed + ' 条')
            return
          }
          // 2026-10-16 部分重叠(底部弹窗决策): X 条已存在(新增 Y 条)——确认只写新增?
          if (d.overlap_partial) {
            setOverlapAsk({ message: d.message || '存在重复数据' })
            setBs('')
            execLock.current = false
            return
          }
          setRes(d)
          goStep(3)
          setBs('')
          execLock.current = false
          toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
          return
        }
        const totalRows = d.total_rows || d.total || '?'
        // 2026-10-15 异步连贯: 后台任务已创建 → 立即 toast"已提交" + 立即跳完成页(不留在预览页防误点"确认写入")
        // 完成页显示区分: 异步=已提交 N 条(指引任务页查看进度); 同步=成功 N 条(上方 d.success 分支)
        setRes({
          async: true,
          taskId: d.task_id,
          submitted: totalRows,
          message: '已提交 ' + totalRows + ' 条',
        })
        goStep(3)
        setBs('')
        execLock.current = false
        toast.add({
          type: 'success',
          title: '已提交 ' + totalRows + ' 条',
          duration: 6000,
          action: {
            label: '查看进度 →',
            handler: () => {
              window.__setPage && window.__setPage('tasks')
            },
          },
        })
        // 2026-10-15 修复: 此处曾 return 提前(轮询死代码——全靠后端保活 5 分钟)——移除——页面停留轮询小窗推进(前端主导)
        // 离开页面: localStorage 持久化 + 回前台 visibilitychange/保活顺带续跑兜底——两机制并存
        try {
          localStorage.setItem('c_clean_task', JSON.stringify({ task_id: d.task_id, channel: ch }))
        } catch {}
        // 前端轮询驱动标记(后端 continue_tasks 写 params.driver=backend——卡片动态显示)
        try {
          api.post('/api/cleansing/driver?task_id=' + d.task_id + '&driver=frontend&channel=' + ch)
        } catch {}
        let finished = false
        const threshold = setTimeout(() => {
          if (!finished) {
            toast.add({
              type: 'success',
              title: '导入任务已提交，后台续跑中',
              duration: 6000,
              action: {
                label: '查看进度 →',
                handler: () => {
                  window.__setPage && window.__setPage('tasks')
                },
              },
            })
            setBs('')
            execLock.current = false
          }
        }, 8000)
        // 本地轮询（页面内）
        const poll = setInterval(async () => {
          if (finished) return
          try {
            // 接力式: status 端点触发续跑并返回进度(done/error/running+page,total)
            const sr = await api.get(
              '/api/cleansing/status?task_id=' + d.task_id + '&page_size=1000',
              { timeout: 150000 },
            ) // 2026-10-15 回小窗(1000): 四维评估大窗 263s+轮询超时积压(9分钟) vs 小窗每窗<40s 实时进度无积压——小窗优; 联动批量修复已使每窗更快
            // 2026-10-15 driver 每轮标记: 轮询活跃=前端推进(覆盖保活 backend 标签——保活只在无轮询时生效)
            try {
              api.post(
                '/api/cleansing/driver?task_id=' + d.task_id + '&driver=frontend&channel=' + ch,
              )
            } catch {}
            const sd = sr.data
            if (sd.status === 'done') {
              finished = true
              clearTimeout(threshold)
              clearInterval(poll)
              setRes({
                success: sd.success,
                failed: sd.failed,
                message: sd.message,
                failed_details: sd.failed_details || [],
              })
              goStep(3)
              setBs('')
              toast.success(
                '清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道',
              )
            } else if (sd.status === 'error') {
              finished = true
              clearTimeout(threshold)
              clearInterval(poll)
              toast.error('失败: ' + sd.error)
              setBs('')
            } else if (sd.status === 'running' && sd.total) {
              const _pg = Math.min(((sd.page || 0) * 5000 + 5000) / Math.max(sd.total, 1), 1)
              const _pct = Math.round(_pg * 100)
              setBs(
                `清洗中... ${_pct}% (${Math.min((sd.page || 0) * 5000 + 5000, sd.total)}/${sd.total}条)`,
              )
            }
          } catch {
            if (!finished) {
              finished = true
              clearTimeout(threshold)
              clearInterval(poll)
              setBs('')
            }
          }
        }, 1000)
        // 全局持久化
        try {
          localStorage.setItem(
            'c_cleansing_task',
            JSON.stringify({ task_id: d.task_id, progress: 0 }),
          )
        } catch {}
      } catch (e) {
        toast.error(
          '请求异常: ' +
            e.message +
            (e.response?.data?.detail
              ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
              : ''),
        )
        setBs('')
      }
    } finally {
      execLock.current = false
    }
  }

  // 部分重叠确认(底部弹窗): 确认→force_new 只写新增; 取消→停
  const confirmOverlap = async () => {
    if (!overlapAsk) return
    setOverlapAsk(null)
    setBs('执行中')
    execLock.current = false
    try {
      const r = await api.post(
        '/api/cleansing/execute-async?force_new=1&q_target=' +
          tt +
          '&q_channel=' +
          ch +
          '&q_conflict=' +
          hammerCleansingConflict +
          '&q_mp=' +
          encodeURIComponent(JSON.stringify(mp)),
        fBuf ? new Blob([fBuf]) : f,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || '提交失败')
        setBs('')
        return
      }
      if (d.success !== undefined) {
        setRes(d)
        goStep(3)
        setBs('')
        execLock.current = false
        toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
        return
      }
      setRes({
        async: true,
        taskId: d.task_id,
        submitted: d.total_rows || d.total || '?',
        message: '已提交 ' + (d.total_rows || d.total || '?') + ' 条',
      })
      goStep(3)
      setBs('')
      execLock.current = false
      toast.success('已提交，后台续跑中')
    } catch (e) {
      toast.error('请求异常: ' + (e.message || ''))
      setBs('')
    }
  }
  const quickExecute = async () => {
    setBs('执行中')
    try {
      const r = await uploadRawBytes(
        '/api/cleansing/preview?q_target=' +
          tt +
          '&q_channel=' +
          ch +
          '&q_conflict=' +
          hammerCleansingConflict +
          '&q_mp=' +
          encodeURIComponent(JSON.stringify(mp)),
        fBuf ? new Blob([fBuf]) : f,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || '提交失败')
        setBs('')
        return
      }
      setPv(d)
      doExecute()
    } catch (e) {
      toast.error(
        '请求异常: ' +
          e.message +
          (e.response?.data?.detail
            ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
            : ''),
      )
      setBs('')
    }
  }

  useEffect(() => {
    try {
      const saved = localStorage.getItem('c_last_tt')
      if (saved) setTt(saved)
    } catch {}
  }, [])
  useEffect(() => {
    try {
      localStorage.setItem('c_last_tt', tt)
    } catch {}
  }, [tt])

  const btn = (label, onClick, color = 'primary') => (
    <button onClick={onClick} disabled={!!bs} className={`btn btn-${bs ? 'ghost' : color}`}>
      {label}
    </button>
  )

  return (
    <div className="card">
      {s > 0 && (
        <div className="step-indicator">
          {['上传文件', '映射字段', '预览确认', '完成'].map((l, i) => (
            <span key={i} className={'step' + (s === i ? ' active' : '') + (s > i ? ' done' : '')}>
              {s > i ? (
                <IconCheck
                  size={12}
                  style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }}
                />
              ) : (
                ''
              )}
              {l}
            </span>
          ))}
          {bs &&
            (bs.includes('%') ? (
              <div className="step w-full">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 'var(--font-sm)',
                    marginBottom: 4,
                    color: 'var(--primary)',
                  }}
                >
                  <span>
                    <IconLoading
                      size={12}
                      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }}
                    />
                    {bs.split('%')[0]}%
                  </span>
                  <span>{bs.split('(')[1]?.replace(')', '') || ''}</span>
                </div>
                <div
                  style={{
                    height: 6,
                    background: 'var(--border)',
                    borderRadius: 'var(--radius-full)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      width: bs.split('%')[0] + '%',
                      background: 'var(--primary)',
                      borderRadius: 'var(--radius-full)',
                      transition: 'width 0.3s',
                    }}
                  ></div>
                </div>
              </div>
            ) : (
              <span className="step" style={{ color: 'var(--primary)' }}>
                <IconLoading
                  size={12}
                  style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }}
                />
                {bs}...
              </span>
            ))}
        </div>
      )}

      {s === 0 && (
        <div style={{ textAlign: 'center', padding: '28px 16px' }}>
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>选择导入类型</div>
          <div
            style={{
              fontSize: 'var(--font-xs)',
              color: 'var(--muted2)',
              marginBottom: 18,
              letterSpacing: 0.2,
            }}
          >
            订单 / 库存 / 出入库 / 商品 / 供应商 / GMV 回款
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginBottom: 24 }}>
            <select
              value={tt}
              onChange={e => {
                setTt(e.target.value)
                setF(null)
                setFBuf(null)
              }}
              style={{
                fontSize: 'var(--font-15)',
                padding: '11px 16px',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-full)',
                outline: 'none',
                background: 'var(--card)',
                width: 260,
                minHeight: 48,
              }}
            >
              <option value="order">导入订单</option>
              <optgroup label="库存">
                <option value="inventory">自有仓库存</option>
                <option value="platform_inv">平台仓库存</option>
                <option value="inventory_b">B仓库存</option>
              </optgroup>
              <optgroup label="入库出库记录">
                <option value="inbound">入库记录</option>
                <option value="outbound">出库记录</option>
              </optgroup>
              <option value="product">导入商品</option>
              <option value="supplier">导入供应商</option>
              <option value="gmv_flow">GMV 回款（资金流水）</option>
            </select>
          </div>
          <label
            className="btn btn-primary"
            style={{
              minHeight: 48,
              width: 260,
              padding: 0,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
              borderRadius: 'var(--radius-full)',
              fontSize: 'var(--font-15)',
              fontWeight: 600,
              opacity: bs ? 0.65 : 1,
              pointerEvents: bs ? 'none' : 'auto',
            }}
          >
            {bs ? '识别中...' : t('cleansing.select_file')}
            <input
              type="file"
              accept=".csv,.xlsx"
              style={{ display: 'none' }}
              onChange={e => {
                const fi = e.target.files[0]
                if (fi) {
                  const rd = new FileReader()
                  rd.onload = () => {
                    const buf = rd.result as ArrayBuffer
                    setFBuf(buf)
                    detect(fi, buf)
                  }
                  rd.readAsArrayBuffer(fi)
                }
              }}
            />
          </label>
          {bs && (
            <div
              style={{
                marginTop: 14,
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                gap: 5,
                color: 'var(--primary)',
                fontSize: 'var(--font-13)',
              }}
            >
              <IconLoading size={14} /> 正在识别文件...
            </div>
          )}
          <div
            style={{
              marginTop: bs ? 8 : 14,
              fontSize: 'var(--font-xs)',
              color: 'var(--muted2)',
              letterSpacing: 0.2,
            }}
          >
            支持 CSV / Excel · 智能识别列名 · 手工映射为主
          </div>
        </div>
      )}

      {s === 1 && (
        <div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              marginTop: 2,
              marginBottom: 10,
            }}
          >
            <span style={{ fontSize: 'var(--font-md)', fontWeight: 700 }}>列映射</span>
            <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
              选择文件列对应的目标字段 · 未映射列导入时丢弃
            </span>
          </div>
          {cols.map(c => {
            const matched = ALIAS[c.name]
            const sf = SYS_FIELDS.find(x => x.t === matched)
            // 统计每个目标被几个来源列映射
            const targetCounts = {}
            for (const [, cfg] of Object.entries(mp)) {
              if (cfg && cfg.target) targetCounts[cfg.target] = (targetCounts[cfg.target] || 0) + 1
            }
            const currentTarget = mp[c.name]?.target
            const isShared = currentTarget && targetCounts[currentTarget] > 1
            return (
              <div key={c.name} className="map-row">
                <div className="map-head">
                  <span className="map-name">{c.name}</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {mp[c.name]?.target ? (
                      <span
                        className="pill success"
                        style={{
                          fontSize: 'var(--font-9)',
                          padding: '1px 6px',
                          minHeight: 'auto',
                          lineHeight: '16px',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 3,
                        }}
                      >
                        <IconCheck size={11} style={{ verticalAlign: -1 }} /> 已映射
                      </span>
                    ) : (
                      <span
                        className="pill warning"
                        style={{
                          fontSize: 'var(--font-9)',
                          padding: '1px 6px',
                          minHeight: 'auto',
                          lineHeight: '16px',
                        }}
                      >
                        未映射 · 导入时丢弃
                      </span>
                    )}
                  </span>
                  {mp[c.name]?.target ? (
                    (() => {
                      const sf2 =
                        SYS_FIELDS.find(x => x.t === mp[c.name].target) ||
                        cf.find(x => x.t === mp[c.name].target)
                      return sf2 ? (
                        <span className="map-sub">
                          → {sf2.l} ({sf2.t})
                        </span>
                      ) : null
                    })()
                  ) : matched && sf ? (
                    <span className="map-sub">
                      智能建议 → {sf.l} ({sf.t})
                    </span>
                  ) : null}
                </div>
                <div className="map-arrow">→</div>
                <div className="map-controls">
                  <select
                    value={mp[c.name]?.target || ''}
                    onChange={e => {
                      const v = e.target.value
                      setMp(p => ({ ...p, [c.name]: { target: v, type: 'string' } }))
                      saveAlias(c.name, v)
                    }}
                    className="tpl-field"
                    style={{ flex: 1, minWidth: 0, fontSize: 'var(--font-13)' }}
                  >
                    <option value="">-- 不映射 --</option>
                    {(TARGET_FIELDS[tt] || SYS_FIELDS).map(f => (
                      <option key={f.t} value={f.t}>
                        {f.l}
                      </option>
                    ))}
                    {cf
                      .filter(f => f.t && f.l)
                      .map(f => (
                        <option key={f.t} value={f.t}>
                          {f.l}
                        </option>
                      ))}
                  </select>
                  <div className="map-tag">
                    {isShared ? (
                      <span
                        className="pill warning"
                        style={{
                          fontSize: 'var(--font-9)',
                          padding: '1px 6px',
                          minHeight: 'auto',
                          lineHeight: '16px',
                        }}
                      >
                        ×{targetCounts[currentTarget]}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {s === 2 && pv && (
        <div>
          <div className="section-title">
            清洗预览 · 前 {pv.preview?.length || 0} 行
            {pv.total > 50 ? (
              <span className="small muted"> · 共 {pv.total} 行，仅展示前 50 行</span>
            ) : (
              ''
            )}
            {pv.preview?.length > 0 && (
              <span className="small muted">
                {' '}
                · {Object.keys(pv.preview[0]).filter(k => k !== '_source').length} 列
              </span>
            )}
          </div>
          {(() => {
            // 脏数据预检: 缺SKU/重复SKU 统计(导入前发现)
            const skuCol = Object.entries(mp).find(([, cfg]) => cfg && cfg.target === 'sku')
            if (!skuCol) return null
            const skuVals = (pv.preview || []).map(r => String(r[skuCol[1].target] || ''))
            const seen = new Map()
            const dupSkus = new Set()
            skuVals.forEach(v => {
              if (v === '') return
              seen.set(v, (seen.get(v) || 0) + 1)
              if (seen.get(v) > 1) dupSkus.add(v)
            })
            const missing = skuVals.filter(v => v === '').length
            if (missing === 0 && dupSkus.size === 0) return null
            return (
              <div
                style={{
                  fontSize: 'var(--font-xs)',
                  color: 'var(--danger)',
                  background: 'rgba(239,68,68,0.06)',
                  border: '1px solid rgba(239,68,68,0.2)',
                  borderRadius: 'var(--radius-card)',
                  padding: '9px 12px',
                  marginBottom: 10,
                  display: 'flex',
                  gap: 6,
                  alignItems: 'flex-start',
                  lineHeight: 1.5,
                }}
              >
                <IconWarning size={13} style={{ flexShrink: 0, marginTop: 1 }} />
                <span>
                  预览发现 <b>{missing}</b> 行缺SKU、<b>{dupSkus.size}</b> 个重复SKU（已标红）——
                  建议返回修正后导入
                </span>
              </div>
            )
          })()}
          {(() => {
            if (!pv.preview?.length) return null
            // 按来源列显示：只显示有映射的列（target 非空），unmap 的列直接不出现
            const mappedSources = Object.entries(mp).filter(([, v]) => v && v.target)
            const cols = mappedSources.map(([src, cfg]) => {
              const sf =
                (TARGET_FIELDS[tt] || SYS_FIELDS).find(x => x.t === cfg.target) ||
                cf.find(x => x.t === cfg.target)
              return { src, target: cfg.target, label: sf ? sf.l : cfg.target }
            })
            // 脏数据预检: 重复 SKU 集合(表体行标红用)
            const _skuCol = cols.find(c => c.target === 'sku')
            const _skuVals = (pv.preview || []).map(r =>
              _skuCol ? String(r[_skuCol.target] || '') : '',
            )
            const _seen = new Map()
            const dupSkus = new Set()
            _skuVals.forEach(v => {
              if (!v) return
              _seen.set(v, (_seen.get(v) || 0) + 1)
              if (_seen.get(v) > 1) dupSkus.add(v)
            })
            if (cols.length === 0)
              return (
                <div className="small muted" style={{ padding: 20, textAlign: 'center' }}>
                  没有已映射的字段，请返回并设置字段映射
                </div>
              )
            return (
              <div style={{ marginBottom: 12 }}>
                <div
                  style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', marginBottom: 4 }}
                >
                  ← 左右滑动查看 · 仅显示已映射的 {cols.length} 列 →
                </div>
                {/* 2026-10-28 安全区根治: 50 行表格曾把页面撑高 → iOS 地址栏联动 → 顶部露出 chrome 纯色条;
                    改表格内部限高滚动(避免整页高度突变), 纵向滚 table / 横向滚外层 */}
                <div
                  className="clean-preview-scroll"
                  style={{
                    overflowX: 'auto',
                    maxHeight: 'calc(100dvh - 340px)',
                    overflowY: 'auto',
                    WebkitOverflowScrolling: 'touch',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-md)',
                  }}
                >
                  <table>
                    <thead>
                      <tr
                        style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}
                      >
                        {cols.map(col => (
                          <th
                            key={col.src}
                            style={{ minWidth: 80, whiteSpace: 'nowrap', verticalAlign: 'top' }}
                          >
                            {col.label}
                            <div
                              style={{
                                fontSize: 10,
                                fontWeight: 400,
                                color: 'var(--muted2)',
                                marginTop: 1,
                              }}
                            >
                              ← {col.src}
                            </div>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pv.preview.map((r, i) => {
                        // 脏数据预检: 关键字段(sku)缺失/重复 → 行标红(导入前发现, 非执行后才知道)
                        const skuCol = cols.find(c => c.target === 'sku')
                        const skuVal = skuCol ? String(r[skuCol.target] || '') : ''
                        const issue = skuVal === '' ? '缺SKU' : dupSkus.has(skuVal) ? '重复SKU' : ''
                        return (
                          <tr
                            key={i}
                            style={issue ? { background: 'rgba(239,68,68,0.06)' } : undefined}
                          >
                            {cols.map(col => (
                              <td
                                key={col.src}
                                style={{
                                  minWidth: 80,
                                  whiteSpace: 'nowrap',
                                  maxWidth: 200,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  color:
                                    issue && col.target === 'sku' ? 'var(--danger)' : undefined,
                                }}
                              >
                                {String(r[col.target] || '')}
                                {issue && col.target === 'sku' ? (
                                  <span
                                    style={{
                                      fontSize: 'var(--font-9)',
                                      color: 'var(--danger)',
                                      marginLeft: 4,
                                    }}
                                  >
                                    <IconWarning size={11} style={{ verticalAlign: -2 }} />
                                    {issue}
                                  </span>
                                ) : null}
                              </td>
                            ))}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })()}
          {/* 2026-10-27: 底部操作对齐其余步骤(左返回固定宽 + 右主操作 primary——写入非'成功'语义) */}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button
              className="clickable"
              onClick={() => {
                goStep(1)
                setPv(null)
              }}
              disabled={!!bs}
              style={{
                flex: '0 0 96px',
                minHeight: 44,
                borderRadius: 'var(--radius-full)',
                border: '1px solid var(--border)',
                background: 'var(--card)',
                color: 'var(--text)',
                fontSize: 'var(--font-md)',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              ← 返回
            </button>
            <button
              className="clickable"
              onClick={() => doExecute()}
              disabled={!!bs}
              style={{
                flex: 1,
                minHeight: 44,
                borderRadius: 'var(--radius-full)',
                border: 'none',
                background: 'var(--primary)',
                color: '#fff',
                fontSize: 'var(--font-md)',
                fontWeight: 600,
                cursor: 'pointer',
                opacity: bs ? 0.6 : 1,
              }}
            >
              确认写入 {pv.total} 条
            </button>
          </div>
        </div>
      )}
      {s === 1 && (
        <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
          <button
            onClick={() => goStep(0)}
            className="clickable"
            style={{
              flexShrink: 0,
              width: 88,
              padding: '11px 0',
              fontSize: 'var(--font-md)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-full)',
              background: 'var(--card)',
              cursor: 'pointer',
              fontWeight: 600,
              minHeight: 42,
              whiteSpace: 'nowrap',
            }}
          >
            ← 返回
          </button>
          <button
            onClick={preview}
            className="clickable"
            style={{
              flex: 1,
              padding: '11px 12px',
              fontSize: 'var(--font-md)',
              border: 'none',
              borderRadius: 'var(--radius-full)',
              background: 'var(--primary)',
              color: '#fff',
              cursor: 'pointer',
              fontWeight: 600,
              minHeight: 42,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              justifyContent: 'center',
              whiteSpace: 'nowrap',
            }}
          >
            下一步 · 预览
          </button>
          <button
            onClick={quickExecute}
            className="clickable"
            style={{
              flex: 1,
              padding: '11px 12px',
              fontSize: 'var(--font-md)',
              border: 'none',
              borderRadius: 'var(--radius-full)',
              background: 'var(--success)',
              color: '#fff',
              cursor: 'pointer',
              fontWeight: 600,
              minHeight: 42,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              justifyContent: 'center',
              whiteSpace: 'nowrap',
            }}
          >
            <IconLightning size={14} /> 一键执行
          </button>
        </div>
      )}

      {s === 3 && res && (
        <div style={{ textAlign: 'center', padding: '20px 0' }}>
          {/* 2026-10-27: 图标 32px → 56px 圆形底(与看板空态同构, 语义色底) */}
          {(() => {
            const _ok = !res.async && res.success > 0
            const _pend = !!res.async
            const _bg = _pend
              ? 'rgba(0,122,255,0.12)'
              : _ok
                ? 'rgba(5,150,105,0.12)'
                : 'rgba(245,158,11,0.14)'
            const _c = _pend ? 'var(--primary)' : _ok ? 'var(--success)' : 'var(--warning)'
            return (
              <div
                style={{
                  width: 56,
                  height: 56,
                  borderRadius: '50%',
                  background: _bg,
                  color: _c,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginBottom: 10,
                }}
              >
                {_ok || _pend ? <IconCheck size={30} /> : <IconAlert size={30} />}
              </div>
            )
          })()}
          {/* 标题语义化(原 res.message 直出) */}
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
            {res.async ? '已提交后台处理' : res.success > 0 ? '导入完成' : '导入未完成'}
          </div>
          {f?.name ? (
            <div className="small muted" style={{ fontSize: 'var(--font-sm)', marginBottom: 8 }}>
              {f.name}
            </div>
          ) : (
            ''
          )}

          <div className="small muted" style={{ marginBottom: 16 }}>
            {res.error || res.message || ''}
            {res.error ? (
              <span style={{ marginLeft: 6, fontSize: 'var(--font-sm)', color: 'var(--warning)' }}>
                （侧边栏{' '}
                <IconAlert size={12} style={{ display: 'inline', verticalAlign: 'middle' }} />{' '}
                查看详情）
              </span>
            ) : (
              ''
            )}
          </div>
          {res.async ? (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginBottom: 16 }}>
              <div>
                <div
                  style={{
                    fontSize: 28,
                    fontWeight: 700,
                    color: 'var(--primary)',
                    fontVariantNumeric: 'tabular-nums',
                    lineHeight: 1.1,
                  }}
                >
                  {res.submitted}
                </div>
                <div className="small muted">已提交</div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: 28,
                    fontWeight: 700,
                    lineHeight: 1.1,
                    color: 'var(--muted2)',
                  }}
                >
                  —
                </div>
                <div className="small muted">处理中</div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginBottom: 16 }}>
              <div>
                <div
                  style={{
                    fontSize: 28,
                    fontWeight: 700,
                    color: 'var(--success)',
                    fontVariantNumeric: 'tabular-nums',
                    lineHeight: 1.1,
                  }}
                >
                  {res.success}
                </div>
                <div className="small muted">成功</div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: 28,
                    fontWeight: 700,
                    lineHeight: 1.1,
                    fontVariantNumeric: 'tabular-nums',
                    color: res.failed > 0 ? 'var(--danger)' : 'var(--muted2)',
                  }}
                >
                  {res.failed}
                </div>
                <div className="small muted">跳过</div>
              </div>
            </div>
          )}
          {res.async && (
            <button
              className="btn btn-primary"
              style={{ marginTop: 14, marginBottom: 14 }}
              onClick={() => window.__setPage && window.__setPage('tasks')}
            >
              前往任务管理页查看进度 →
            </button>
          )}
          {res.failed > 0 && Array.isArray(res.failed_details) && res.failed_details.length > 0 && (
            <div
              style={{
                textAlign: 'left',
                background: 'var(--bg)',
                borderRadius: 'var(--radius-card)',
                padding: '10px 14px',
                marginBottom: 14,
                maxHeight: 180,
                overflowY: 'auto',
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  fontSize: 'var(--font-sm)',
                  marginBottom: 6,
                  color: 'var(--danger)',
                }}
              >
                失败明细（可对照修正后重新导入）
              </div>
              {res.failed_details.map((d, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 'var(--font-xs)',
                    color: 'var(--muted2)',
                    padding: '3px 0',
                    borderBottom: '1px solid var(--border)',
                    display: 'flex',
                    gap: 8,
                  }}
                >
                  <span className="mono" style={{ color: 'var(--text)', flexShrink: 0 }}>
                    {d.sku || '-'}
                  </span>
                  <span
                    style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  >
                    {d.reason || ''}
                  </span>
                </div>
              ))}
            </div>
          )}
          {/* 2026-10-27: 底部按钮统一 44px 胶囊(与首屏/映射/预览步骤一致) */}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => {
                goStep(0)
                setF(null)
                setCols([])
                setTr(0)
                setMp({})
                setPv(null)
                setRes(null)
              }}
              className="clickable"
              style={{
                flex: '0 0 96px',
                minHeight: 44,
                borderRadius: 'var(--radius-full)',
                border: '1px solid var(--border)',
                background: 'var(--card)',
                color: 'var(--text)',
                fontSize: 'var(--font-md)',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              重新开始
            </button>
            <label
              style={{
                flex: 1,
                minHeight: 44,
                borderRadius: 'var(--radius-full)',
                border: 'none',
                background: 'var(--success)',
                color: '#fff',
                fontSize: 'var(--font-md)',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 5,
              }}
              className="clickable"
            >
              <IconFolder size={14} /> 导入相同格式
              <input
                type="file"
                accept=".csv,.xlsx"
                style={{ display: 'none' }}
                onChange={e => {
                  const fi = e.target.files[0]
                  if (fi) {
                    const rd = new FileReader()
                    rd.onload = () => {
                      const buf = rd.result as ArrayBuffer
                      setFBuf(buf)
                      detect(fi, buf)
                    }
                    rd.readAsArrayBuffer(fi)
                    detect(fi)
                  }
                }}
              />
            </label>
          </div>
        </div>
      )}

      {colMapOpen && (
        <div
          onClick={() => setColMapOpen(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }}
        />
      )}
      {colMapOpen && (
        <div
          style={{
            position: 'fixed',
            left: 0,
            right: 0,
            bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
            zIndex: 9999,
            display: 'flex',
            justifyContent: 'center',
            padding: '0 14px',
            pointerEvents: 'none',
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            className="material-regular"
            style={{
              width: '100%',
              maxWidth: 600,
              borderRadius: 'var(--radius-lg)',
              padding: '18px 14px calc(14px + env(safe-area-inset-bottom))',
              boxShadow: 'var(--shadow-sheet)',
              pointerEvents: 'auto',
              maxHeight: '70vh',
              overflowY: 'auto',
            }}
          >
            <div
              style={{
                fontSize: 'var(--font-18)',
                fontWeight: 700,
                marginBottom: 12,
                textAlign: 'center',
              }}
            >
              表格列状态映射
            </div>
            <div
              style={{
                textAlign: 'center',
                fontSize: 'var(--font-xs)',
                color: 'var(--muted2)',
                marginBottom: 12,
              }}
            >
              按列配置 值→档位，导入时自动归一化 · 渠道：{ch === 'jd' ? '京东' : '其他渠道'}
              （全局主体隔离） 计入销量池=日销/GMV 都算；仅订单记录=写订单可见，GMV
              按状态（已发货类算），不计日销补货
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <span style={{ fontSize: 'var(--font-13)', fontWeight: 600, flexShrink: 0 }}>
                映射列
              </span>
              <select
                value={colMapCol}
                onChange={e => setColMapCol(e.target.value)}
                style={{
                  flex: 1,
                  fontSize: 'var(--font-md)',
                  padding: '8px 12px',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-lg)',
                  background: 'var(--card)',
                  outline: 'none',
                }}
              >
                <option value="order_status">订单状态（销量池判定）</option>
              </select>
            </div>
            {!(colMap[colMapCol] || []).length && (
              <div className="tpl-empty" style={{ padding: '12px 2px' }}>
                还没有配置：点击「补全内置默认」快速生成，或「+ 添加值」手动添加
              </div>
            )}
            {(colMap[colMapCol] || []).map((s, i) => (
              <div
                key={i}
                style={{
                  // 2026-10-27 UX: 去外层胶囊框 + 去行分隔线; 加横向滚动(窄屏控件不挤压删除按钮贴边)
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '5px 0',
                  overflowX: 'auto',
                  WebkitOverflowScrolling: 'touch',
                }}
              >
                <input
                  value={s.name || ''}
                  onChange={e =>
                    setColMap(p => ({
                      ...p,
                      [colMapCol]: (p[colMapCol] || []).map((x, j) =>
                        j === i ? { ...x, name: e.target.value } : x,
                      ),
                    }))
                  }
                  placeholder="值(如 交易成功)"
                  className="tpl-field"
                  title={
                    (colMap[colMapCol] || []).filter(x => x.name && x.name === s.name).length > 1
                      ? '值重复：导入归一化时后者覆盖前者'
                      : ''
                  }
                  style={{
                    flex: '1 0 100px',
                    minWidth: 100,
                    // 2026-10-27 重复值校验: 同列表重名红边警示(归一化 dict 后者覆盖, 结果不确定)
                    borderColor:
                      s.name && (colMap[colMapCol] || []).filter(x => x.name === s.name).length > 1
                        ? 'var(--danger)'
                        : undefined,
                  }}
                />
                {/* 2026-10-27 emoji→SVG: <option> 无法内嵌 SVG, 改分段控件(两态 pill, 语义图标+文字) */}
                <div
                  style={{
                    display: 'flex',
                    gap: 2,
                    padding: 2,
                    borderRadius: 99,
                    background: 'var(--bg)',
                    flexShrink: 0,
                  }}
                >
                  {(
                    [
                      {
                        v: 'sale',
                        Icon: IconCheck,
                        label: '销量池',
                        tip: '计入销量池（日销+GMV）',
                      },
                      {
                        v: 'blocked',
                        Icon: IconClipboard,
                        label: '仅记录',
                        tip: '仅订单记录（GMV 按状态，不计日销）',
                      },
                    ] as const
                  ).map(({ v, Icon, label, tip }) => {
                    const act = (s.group || 'blocked') === v
                    return (
                      <span
                        key={v}
                        title={tip}
                        className="clickable"
                        onClick={() =>
                          setColMap(p => ({
                            ...p,
                            [colMapCol]: (p[colMapCol] || []).map((x, j) =>
                              j === i ? { ...x, group: v } : x,
                            ),
                          }))
                        }
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 4,
                          padding: '7px 10px',
                          borderRadius: 99,
                          fontSize: 'var(--font-12)',
                          fontWeight: act ? 600 : 400,
                          whiteSpace: 'nowrap',
                          cursor: 'pointer',
                          background: act ? 'var(--card)' : 'transparent',
                          color: act
                            ? v === 'sale'
                              ? 'var(--success)'
                              : 'var(--muted)'
                            : 'var(--muted2)',
                          boxShadow: act ? 'var(--inset-glass)' : 'none',
                        }}
                      >
                        <Icon size={12} />
                        {label}
                      </span>
                    )
                  })}
                </div>
                <button
                  onClick={() =>
                    setColMap(p => ({
                      ...p,
                      [colMapCol]: (p[colMapCol] || []).filter((_, j) => j !== i),
                    }))
                  }
                  className="clickable tpl-btn tpl-btn-ghost"
                  style={{ color: 'var(--danger)', width: 40, padding: 0, flexShrink: 0 }}
                >
                  <IconClose size={16} />
                </button>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
              <button
                onClick={() =>
                  setColMap(p => ({
                    ...p,
                    [colMapCol]: [...(p[colMapCol] || []), { name: '', group: 'blocked' }],
                  }))
                }
                className="btn btn-ghost clickable"
                style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
              >
                + 添加值
              </button>
              <button
                onClick={() =>
                  setColMap(p => {
                    // 2026-10-27 合并填充(曾整表覆盖——用户手工配置全丢): 保留已有, 仅补缺失内置项
                    const _def = [
                      { name: '已完成', group: 'sale' },
                      { name: '交易成功', group: 'sale' },
                      { name: '确认收货', group: 'sale' },
                      { name: '已签收', group: 'sale' },
                      { name: '妥投', group: 'sale' },
                      { name: 'Closed', group: 'sale' },
                      { name: 'Completed', group: 'sale' },
                      { name: '待发货', group: 'blocked' },
                      { name: '已发货', group: 'blocked' },
                      { name: '待确认', group: 'blocked' },
                      { name: '待付款', group: 'blocked' },
                      { name: '已取消', group: 'blocked' },
                      { name: '已退款', group: 'blocked' },
                      { name: '退款中', group: 'blocked' },
                      { name: '申请退款', group: 'blocked' },
                      { name: '已退货', group: 'blocked' },
                      { name: '运输中', group: 'blocked' },
                      { name: '在途', group: 'blocked' },
                    ]
                    const _cur: any[] = (p as any).order_status || []
                    const _names = new Set(_cur.map((x: any) => x.name))
                    const _merged = [..._cur, ..._def.filter((d: any) => !_names.has(d.name))]
                    return { ...p, order_status: _merged }
                  })
                }
                className="btn btn-ghost clickable"
                style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
              >
                补全内置默认
              </button>
              <button
                onClick={() =>
                  setColMap(p => ({
                    ...p,
                    [colMapCol]: (p[colMapCol] || []).map((x: any) => ({ ...x, group: 'sale' })),
                  }))
                }
                className="btn btn-ghost clickable"
                style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
              >
                全部销量池
              </button>
              <button
                onClick={() =>
                  setColMap(p => ({
                    ...p,
                    [colMapCol]: (p[colMapCol] || []).map((x: any) => ({ ...x, group: 'blocked' })),
                  }))
                }
                className="btn btn-ghost clickable"
                style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
              >
                全部仅记录
              </button>
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <div
                onClick={() => setColMapOpen(false)}
                className="clickable sheet-cancel"
                style={{ flex: 1 }}
              >
                <span style={{ fontSize: 'var(--font-15)', fontWeight: 600 }}>取消</span>
              </div>
              <div
                onClick={() => {
                  if (!colMapSaving) saveColMap()
                }}
                className="clickable sheet-close"
                style={{ flex: 1, opacity: colMapSaving ? 0.6 : 1 }}
              >
                <span style={{ fontSize: 'var(--font-15)', fontWeight: 600 }}>
                  {colMapSaving ? '保存中...' : '保存'}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
      {overlapAsk && (
        <ConfirmDialog
          open={!!overlapAsk}
          title="检测到重复数据"
          desc={overlapAsk.message}
          confirmLabel="确认导入新增"
          onCancel={() => setOverlapAsk(null)}
          onConfirm={() => {
            confirmOverlap()
          }}
        />
      )}
    </div>
  )
}
