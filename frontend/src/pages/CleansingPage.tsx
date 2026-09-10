import React, { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import {IconPackage, IconTrendUp, IconLightning, IconCheck, IconAlert, IconLoading, IconFolder} from '../components/Icons'
import { t } from "../locale"


const INV_FIELDS = [
  {t:'warehouse',l:'仓库',tp:'string'},{t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'product_name',l:'商品',tp:'string'},
  {t:'channel',l:'平台',tp:'string'},{t:'brand',l:'品牌',tp:'string'},{t:'price',l:'单价',tp:'number'},
  {t:'beginning_stock',l:'期初库存',tp:'number'},{t:'in_transit_qty',l:'在途',tp:'number'},
  {t:'month_inbound',l:'当月采购入库',tp:'number'},{t:'month_outbound',l:'当月出库',tp:'number'},
  {t:'available_qty',l:'可用',tp:'number'},{t:'turnover_days',l:'在库周转',tp:'number'},
  {t:'c_transit',l:'B-C调拨在途',tp:'number'},
  {t:'locked_qty',l:'锁定库存',tp:'number'},{t:'safety_qty',l:'安全线',tp:'number'},
  {t:'weight',l:'箱重/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},
  {t:'note',l:'备注',tp:'string'},
]
const PROD_FIELDS = [
  {t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'channel',l:'平台',tp:'string'},
  {t:'product_name',l:'名称',tp:'string'},{t:'store',l:'店铺',tp:'string'},{t:'category',l:'分类',tp:'string'},
  {t:'brand',l:'品牌',tp:'string'},
  {t:'price',l:'单价',tp:'number'},{t:'box_qty',l:'箱规',tp:'number'},{t:'unit',l:'单位',tp:'string'},
  {t:'weight',l:'箱重/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},{t:'status',l:'状态',tp:'string'},
]
const SYS_FIELDS = [
  {t:'order_no',l:'订单号',tp:'string'},{t:'barcode',l:'69码',tp:'string'},
  {t:'store',l:'店铺',tp:'string'},{t:'warehouse',l:'仓库',tp:'string'},
  {t:'product_name',l:'商品',tp:'string'},{t:'total_amount',l:'金额',tp:'number'},
  {t:'order_status',l:'状态',tp:'string'},{t:'ordered_at',l:'下单日期',tp:'date'},
  {t:'paid_at',l:'入库日期',tp:'date'},
  {t:'prod_date',l:'生产日期(可选,入库记录)',tp:'date'},{t:'exp_date',l:'到期日期(可选,入库记录)',tp:'date'},
  {t:'source_order_id',l:'原始单号',tp:'string'},{t:'sku',l:'SKU',tp:'string'},
  {t:'quantity',l:'数量',tp:'number'},{t:'unit_price',l:'单价',tp:'number'},
  {t:'supplier',l:'供应商',tp:'string'},{t:'supplier_code',l:'供应商编码',tp:'string'},
  {t:'remark',l:'备注',tp:'string'},{t:'platform',l:'平台',tp:'string'},
  {t:'shipped_at',l:'发货时间',tp:'date'},
  {t:'sender',l:'收货人',tp:'string'},{t:'sender_phone',l:'收货电话',tp:'string'},
  {t:'currency',l:'币种',tp:'string'},{t:'discount',l:'折扣',tp:'number'},
  {t:'freight',l:'运费',tp:'number'},{t:'category',l:'分类',tp:'string'},
  {t:'brand',l:'品牌',tp:'string'},{t:'spec',l:'规格',tp:'string'},
  {t:'weight',l:'箱重/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},
  // GMV 金额明细(方案A): 运费/补贴/税费/满减/实付
  {t:'freight_amount',l:'运费(明细)',tp:'number'},{t:'subsidy_amount',l:'平台补贴',tp:'number'},
  {t:'tax_amount',l:'税费',tp:'number'},{t:'discount_amount',l:'店铺满减',tp:'number'},
  {t:'actual_amount',l:'实际支付',tp:'number'},
  // 供应商导入
  {t:'supplier_name',l:'供应商名称',tp:'string'},{t:'contact_person',l:'联系人',tp:'string'},
  {t:'contact_phone',l:'联系电话',tp:'string'},{t:'score',l:'评分',tp:'number'},
  {t:'status',l:'状态',tp:'string'},
  // 进销存B仓
  {t:'c_transit',l:'B-C调拨在途',tp:'number'},
]
const ALIAS = {
  "订单号":"order_no","订单编号":"order_no","采购单号":"order_no",
  "原始单号":"source_order_id","外部单号":"source_order_id","平台订单号":"source_order_id",
  "商品编号":"sku","货号":"sku","SKU":"sku",
  "商品名称":"product_name","产品名称":"product_name","名称":"product_name",
  "数量":"quantity","采购数量":"quantity","订货数量":"quantity","原始采购数量":"quantity",
  "单价":"unit_price","价格":"unit_price","采购价格":"unit_price",
  "金额":"total_amount","总金额":"total_amount","采购金额":"total_amount","实收金额":"total_amount",
  "店铺":"store","店铺名":"store","门店":"store",
  "仓库":"warehouse","京东仓库":"warehouse","发货仓":"warehouse",
  "状态":"order_status","订单状态":"order_status",
  "日期":"ordered_at","订购时间":"ordered_at","下单时间":"ordered_at","入库时间":"paid_at",
  "供应商":"supplier","供应商名称":"supplier_name","供应商编码":"supplier_code","供应商编号":"supplier_code","供应商简码":"supplier_code",
  "联系人":"contact_person","联系电话":"contact_phone","评分":"score",
  "运费":"freight_amount","折扣":"discount_amount","平台补贴":"subsidy_amount","税费":"tax_amount","税额":"tax_amount","实际支付":"actual_amount","实付金额":"actual_amount",
  "B-C调拨在途":"c_transit","调拨在途":"c_transit","商品状态":"status","在售":"status","停用":"status",
  "备注":"remark",
  "平台":"platform","订单来源":"platform","来源":"platform",
  "收货人":"sender","收货负责人":"sender",
  "收货电话":"sender_phone","电话":"sender_phone",
  "币种":"currency","货币":"currency",
  "品牌":"brand",
  "规格":"spec",
  "分类":"category","商品分类":"category",
}

// 各导入类型专属字段集(下拉按类型过滤, 避免订单页出现库存/出入库无关字段)
const ORDER_FIELDS = [
  {t:'order_no',l:'订单号',tp:'string'},{t:'source_order_id',l:'原始单号',tp:'string'},
  {t:'store',l:'店铺',tp:'string'},{t:'warehouse',l:'仓库',tp:'string'},
  {t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'product_name',l:'商品',tp:'string'},
  {t:'quantity',l:'数量',tp:'number'},{t:'unit_price',l:'单价',tp:'number'},
  {t:'total_amount',l:'金额',tp:'number'},{t:'discount_amount',l:'折扣',tp:'number'},
  {t:'freight_amount',l:'运费',tp:'number'},{t:'subsidy_amount',l:'平台补贴',tp:'number'},
  {t:'tax_amount',l:'税费',tp:'number'},{t:'actual_amount',l:'实付金额',tp:'number'},
  {t:'order_status',l:'状态',tp:'string'},{t:'ordered_at',l:'下单日期',tp:'date'},
  {t:'paid_at',l:'支付日期',tp:'date'},{t:'shipped_at',l:'发货时间',tp:'date'},
  {t:'platform',l:'平台',tp:'string'},{t:'channel',l:'渠道',tp:'string'},
  {t:'data_source',l:'数据来源',tp:'string'},{t:'supplier',l:'供应商',tp:'string'},
  {t:'sender',l:'收货人',tp:'string'},{t:'sender_phone',l:'收货电话',tp:'string'},
  {t:'remark',l:'备注',tp:'string'},
]
const INBOUND_FIELDS = [
  {t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'product_name',l:'商品',tp:'string'},
  {t:'brand',l:'品牌',tp:'string'},{t:'store',l:'店铺',tp:'string'},{t:'category',l:'分类',tp:'string'},
  {t:'quantity',l:'数量',tp:'number'},{t:'supplier',l:'供应商',tp:'string'},
  {t:'inbound_date',l:'入库日期',tp:'date'},{t:'channel',l:'渠道',tp:'string'},
  {t:'platform',l:'平台',tp:'string'},
  {t:'warehouse',l:'仓库',tp:'string'},{t:'prod_date',l:'生产日期',tp:'date'},
  {t:'exp_date',l:'到期日期',tp:'date'},{t:'price',l:'单价',tp:'number'},
  {t:'box_qty',l:'箱规',tp:'number'},{t:'unit',l:'单位',tp:'string'},
  {t:'weight',l:'重量/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},
  {t:'status',l:'状态',tp:'string'},{t:'remark',l:'备注',tp:'string'},
]
const OUTBOUND_FIELDS = [
  {t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'product_name',l:'商品',tp:'string'},
  {t:'brand',l:'品牌',tp:'string'},{t:'store',l:'店铺',tp:'string'},{t:'category',l:'分类',tp:'string'},
  {t:'quantity',l:'数量',tp:'number'},{t:'target_warehouse',l:'目标仓库',tp:'string'},
  {t:'outbound_date',l:'出库日期',tp:'date'},{t:'channel',l:'渠道',tp:'string'},
  {t:'platform',l:'平台',tp:'string'},
  {t:'warehouse',l:'仓库',tp:'string'},{t:'prod_date',l:'生产日期',tp:'date'},
  {t:'exp_date',l:'到期日期',tp:'date'},{t:'price',l:'单价',tp:'number'},
  {t:'box_qty',l:'箱规',tp:'number'},{t:'unit',l:'单位',tp:'string'},
  {t:'weight',l:'重量/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},
  {t:'status',l:'状态',tp:'string'},{t:'remark',l:'备注',tp:'string'},
]
const SUPPLIER_FIELDS = [
  {t:'supplier_code',l:'供应商编码',tp:'string'},{t:'supplier_name',l:'供应商名称',tp:'string'},
  {t:'contact_person',l:'联系人',tp:'string'},{t:'contact_phone',l:'联系电话',tp:'string'},
  {t:'score',l:'评分',tp:'number'},{t:'status',l:'状态',tp:'string'},
  {t:'channel',l:'渠道',tp:'string'},{t:'brand',l:'品牌',tp:'string'},
]
const TARGET_FIELDS = {order: ORDER_FIELDS, inventory: INV_FIELDS, platform_inv: INV_FIELDS,
                       inventory_b: INV_FIELDS, product: PROD_FIELDS, supplier: SUPPLIER_FIELDS,
                       inbound: INBOUND_FIELDS, outbound: OUTBOUND_FIELDS}
const TARGET_TARGETS = {}
for (const _k in TARGET_FIELDS) TARGET_TARGETS[_k] = TARGET_FIELDS[_k].map(f => f.t)

// 归一化别名扩展(列名兼容: 英文/变体/去空格符号; 供自动识别第二个匹配层)
const _nk = (s) => String(s || '').toLowerCase().replace(/[\s_\-/（）()【】·]/g, '')
const ALIAS_EXT = {
  '订单号':'order_no','订单编号':'order_no','采购单号':'order_no','订单id':'order_no','单号':'order_no','单据编号':'order_no',
  'orderno':'order_no','orderid':'order_no','order_no':'order_no',
  '商品编号':'sku','货号':'sku','商品编码':'sku','sku':'sku','itemid':'sku','item_id':'sku','客服sku':'sku',
  '商品名称':'product_name','产品名称':'product_name','名称':'product_name','品名':'product_name',
  'productname':'product_name','itemname':'product_name','product_name':'product_name','货品名称':'product_name',
  '数量':'quantity','采购数量':'quantity','订货数量':'quantity','件数':'quantity',
  'qty':'quantity','quantity':'quantity','订购数量':'quantity',
  '单价':'unit_price','价格':'unit_price','采购价格':'unit_price','unitprice':'unit_price','price':'unit_price',
  '金额':'total_amount','总金额':'total_amount','采购金额':'total_amount','实收金额':'total_amount','总额':'total_amount','订单金额':'total_amount',
  'totalamount':'total_amount','amount':'total_amount','total':'total_amount',
  '店铺':'store','店铺名':'store','门店':'store','store':'store','店铺名称':'store',
  '仓库':'warehouse','京东仓库':'warehouse','发货仓':'warehouse','发货仓库':'warehouse','所属仓库':'warehouse',
  'warehouse':'warehouse','仓库名称':'warehouse',
  '状态':'order_status','订单状态':'order_status','orderstatus':'order_status','order_status':'order_status',
  'status':'order_status','交易状态':'order_status',
  '日期':'ordered_at','订购时间':'ordered_at','下单时间':'ordered_at','订单日期':'ordered_at','下单日期':'ordered_at','订购日期':'ordered_at','下单时间time':'ordered_at',
  'date':'ordered_at','orderdate':'ordered_at','orderedat':'ordered_at',
  '供应商':'supplier','supplier':'supplier','供应商名称':'supplier_name','供应商编码':'supplier_code','供应商编号':'supplier_code',
  '联系人':'contact_person','联系电话':'contact_phone','评分':'score',
  '备注':'remark','remark':'remark','平台':'platform','platform':'platform','订单来源':'platform',
  '生产日期':'prod_date','生产日':'prod_date','proddate':'prod_date','生产年月':'prod_date',
  '到期日期':'exp_date','到期日':'exp_date','失效日期':'exp_date','有效期至':'exp_date','expdate':'exp_date','保质期至':'exp_date',
  '入库日期':'paid_at','入库时间':'paid_at','支付时间':'paid_at','paidat':'paid_at','实际入库日期':'paid_at',
  '出库日期':'outbound_date','outdate':'outbound_date','出库时间':'outbound_date',
  '品牌':'brand','brand':'brand','分类':'category','商品分类':'category','category':'category','品类':'category',
}

export default function CleansingPage() {
  const toast = useToast()
  const [s,setS] = useState(0)
  const [f,setF] = useState(null)
  const [cols,setCols] = useState([])
  const [tr,setTr] = useState(0)
  const [tt,setTt] = useState('order')
  const {setHammerCleansingTarget, hammerCleansingConflict} = useAppStore()
  useEffect(() => { setHammerCleansingTarget(tt) }, [tt])
  const { hammerCleansingChannel: ch, setHammerCleansingChannel: setCh } = useAppStore()
  const goStep = (n) => { setS(n); useAppStore.getState().setHammerCleansingStep(n) }
  const [mp,setMp] = useState({})
  // 映射页当前映射同步(模板管理弹窗保存用) + 模板应用/自定义字段变更事件
  useEffect(() => { (window as any).__curMp = mp }, [mp])
  useEffect(() => {
    const onApply = (e: any) => { const d = e.detail; if (d && typeof d === 'object') setMp(d) }
    const onCfChanged = () => { try { setCf(JSON.parse(localStorage.getItem('c_cf') || '[]')) } catch {} }
    window.addEventListener('apply-template', onApply as any)
    window.addEventListener('custom-fields-changed', onCfChanged)
    return () => { window.removeEventListener('apply-template', onApply as any); window.removeEventListener('custom-fields-changed', onCfChanged) }
  }, [])
  const [pv,setPv] = useState(null)
  const [res,setRes] = useState(null)
  const [bs,setBs] = useState('')
  const [cf,setCf] = useState(() => { try { return JSON.parse(localStorage.getItem('c_cf')||'[]') } catch { return [] } })
  const [templates, setTemplates] = useState([]) // 模板管理已移至锤子菜单弹窗(此处仅兼容引用)
  const saveCf = (v) => { setCf(v); try { localStorage.setItem('c_cf', JSON.stringify(v)) } catch {} }

  const loadTemplates = async () => { try { const r = await api.get('/api/cleansing/templates'); setTemplates(r.data || []) } catch(e) {} }
  useEffect(() => { loadTemplates() }, [])

  const addField = () => saveCf([...cf, {t:'field_'+Date.now(), l:'自定义字段', tp:'string'}])
  const delField = (i) => saveCf(cf.filter((_,k) => k !== i))

  // 用户自定义别名记忆(核心): 手工映射过的 (列名→目标) 存 localStorage, 下次同列名自动识别
  const getAliasMap = () => { try { return JSON.parse(localStorage.getItem('c_alias_map') || '{}') } catch { return {} } }
  const saveAlias = (colName, target) => {
    try {
      const m = getAliasMap()
      if (target) m[colName] = target
      else delete m[colName]
      localStorage.setItem('c_alias_map', JSON.stringify(m))
    } catch {}
  }
  const detect = async (file) => {
    setF(file); setBs('识别中')
    const fd = new FormData(); fd.append('file', file)
    try {
      const r = await api.post('/api/cleansing/detect', fd)
      const d = r.data
      if (!d.ok) { toast.error(d.error||'识别失败'); setBs(''); return }
      setCols(d.columns||[]); setTr(d.total||0)
      const a = {}
      const customAlias = getAliasMap()
      ;(d.columns||[]).forEach(c => {
        // 识别优先级: 用户自定义别名(历史手工映射记忆) → 系统精确 ALIAS → 归一化别名(英文/变体)
        // 目标必须属于当前导入类型字段集(避免订单页识别出库存/出入库无关字段)
        let key = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
        if (key && TARGET_TARGETS[tt] && !TARGET_TARGETS[tt].includes(key)) key = ''
        if (key) a[c.name] = { target: key, type: 'string' }
      })
      setMp(a)
      goStep(1)  // 停步映射字段界面, 用户确认/补映射后手动预览(智能识别仅辅助)
      setBs('')
      if (Object.keys(a).length > 0) toast.success('已自动识别 ' + Object.keys(a).length + ' 列（未识别的列请在下方手工选择目标字段）')
      else toast('未自动识别到映射列，请手工选择每列目标字段（选择后系统会记住，下次同列名自动识别）')
    } catch(e) { toast.error('请求异常: '+e.message) }
    setBs('')
  }

  const preview = async () => {
    setBs('预览中')
    const fd = new FormData(); fd.append('file', f); fd.append('mapping', JSON.stringify(mp)); fd.append('target', tt); fd.append('channel', ch); fd.append('conflict_mode', hammerCleansingConflict)
    try {
      const r = await api.post('/api/cleansing/preview', fd, {timeout: 60000})
      const d = r.data
      if (!d.ok) { toast.error(d.error||'预览失败'); setBs(''); return }
      setPv(d); goStep(2)
    } catch(e) {
      const msg = e.response?.data?.error || e.message || '请求失败'
      toast.error('预览失败: '+msg)
    }
    setBs('')
  }

  // 表格列状态映射(清洗页锤子菜单入口; 渠道级隔离; 通用列值→档位, 当前 order_status)
  const [colMapOpen, setColMapOpen] = useState(false)
  const [colMap, setColMap] = useState({})
  const [colMapSaving, setColMapSaving] = useState(false)
  const [colMapCol, setColMapCol] = useState('order_status')
  const loadColMap = async () => { try { const r = await api.get('/api/replenishment-config/column-value-map?channel=' + ch + '&t=' + Date.now()); setColMap(r.data && typeof r.data === 'object' ? r.data : {}) } catch(e) {} }
  useEffect(() => {
    const h = () => { loadColMap(); setColMapOpen(true) }
    window.addEventListener('cleansing-colmap-open', h)
    return () => window.removeEventListener('cleansing-colmap-open', h)
  }, [ch])
  const saveColMap = async () => {
    setColMapSaving(true)
    try {
      await api.put('/api/replenishment-config/column-value-map?channel=' + ch, {items: colMap})
      toast.success('列映射已保存，导入时即时生效')
      setColMapOpen(false)
    } catch(e) { toast.error('保存失败: ' + (e.message || '')) }
    setColMapSaving(false)
  }
  const execLock = useRef(false)
  const doExecute = async () => {
    if (execLock.current) return
    execLock.current = true
    try {
    setBs('清洗中...')
    // 库存类型必须映射 warehouse 列（否则传统多仓无法按仓库核算）
    const isInvType = tt === 'inventory' || tt === 'platform_inv' || tt === 'inventory_b'
    if (isInvType) {
      const hasWarehouse = Object.values(mp || {}).some(m => m && (m.target === 'warehouse' || m.t === 'warehouse'))
      if (!hasWarehouse) {
        toast.error('导入库存必须映射「仓库」列，否则传统多仓无法按仓库维度核算')
        setBs(''); execLock.current = false; return
      }
    }
    // 订单类型同样必须映射 warehouse 列（否则传统补货按仓库维度算日销，该订单销量会丢失）
    if (tt === 'order') {
      const hasWh = Object.values(mp || {}).some(m => m && (m.target === 'warehouse' || m.t === 'warehouse'))
      if (!hasWh) {
        toast.error('导入订单必须映射「仓库」列，否则传统补货模式按仓库维度核算日销时会丢失该订单销量')
        setBs(''); execLock.current = false; return
      }
    }
    const fd = new FormData(); fd.append('file', f); fd.append('mapping', JSON.stringify(mp)); fd.append('target', tt); fd.append('channel', ch); fd.append('conflict_mode', hammerCleansingConflict)
    try {
      const r = await api.postHeavy('/api/cleansing/execute-async', fd)  // postHeavy 90s: 大文件上传/提交不超PA 30s
      const d = r.data
      if (!d.ok) { toast.error(d.error||'提交失败'); setBs(''); execLock.current = false; return }
      // 同步结果(<400行后端直接返回success/failed) → 直接显示, 不走轮询
      if (d.success !== undefined) {
        setRes(d); goStep(3); setBs(''); execLock.current = false
        toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
        return
      }
      const totalRows = d.total_rows || '?'
      let finished = false; const threshold = setTimeout(() => {
        if (!finished) {
          finished = true
          toast.add({type:'success', title:'导入任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
          setBs(''); execLock.current = false
        }
      }, 8000)
      // 本地轮询（页面内）
        const poll = setInterval(async () => {
          if (finished) return
          try {
            const sr = await api.get('/api/cleansing/task/'+d.task_id)
            const sd = sr.data
            if (sd.status === 'done') {
              finished = true; clearTimeout(threshold); clearInterval(poll)
              setRes(sd.result); goStep(3); setBs(''); toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
            } else if (sd.status === 'error') {
              finished = true; clearTimeout(threshold); clearInterval(poll)
              toast.error('失败: '+sd.error); setBs('')
            } else if (sd.progress !== undefined) {
              setBs(`清洗中... ${sd.progress}% (${Math.round(sd.progress/100*totalRows)}/${totalRows}条)`)
            }
          } catch { if (!finished) { finished = true; clearTimeout(threshold); clearInterval(poll); setBs('') } }
        }, 1000)
        // 全局持久化
        try { localStorage.setItem('c_cleansing_task', JSON.stringify({task_id: d.task_id, progress: 0})) } catch {}
    } catch(e) { toast.error('请求异常: '+e.message); setBs('') }
    } finally { execLock.current = false }
  }

  const quickExecute = async () => {
    setBs('执行中')
    const fd = new FormData(); fd.append('file', f); fd.append('mapping', JSON.stringify(mp)); fd.append('target', tt); fd.append('channel', ch); fd.append('conflict_mode', hammerCleansingConflict)
    try {
      const r = await api.post('/api/cleansing/preview', fd)
      const d = r.data
      if (!d.ok) { toast.error(d.error||'提交失败'); setBs(''); return }
      setPv(d)
      doExecute()
    } catch(e) { toast.error('请求异常: '+e.message); setBs('') }
  }

  useEffect(() => {
    try { const saved = localStorage.getItem('c_last_tt'); if (saved) setTt(saved) } catch {}
  }, [])
  useEffect(() => {
    try { localStorage.setItem('c_last_tt', tt) } catch {}
  }, [tt])

  const btn = (label, onClick, color='primary') => <button onClick={onClick} disabled={!!bs}
    className={`btn btn-${bs?'ghost':color}`}>{label}</button>

  return <div className="card">
    {s > 0 && <div className="step-indicator">
      {['上传文件','映射字段','预览确认','完成'].map((l,i) => <span key={i} className={'step'+(s===i?' active':'')+(s>i?' done':'')}>{s>i?<IconCheck size={12} style={{display:'inline',verticalAlign:'middle',marginRight:2}} />:''}{l}</span>)}
      {bs && (bs.includes('%') ? <div className="step w-full">
        <div style={{display:'flex',justifyContent:'space-between',fontSize:'var(--font-sm)',marginBottom:4,color:'var(--primary)'}}>
          <span><IconLoading size={12} style={{display:'inline',verticalAlign:'middle',marginRight:4}} />{bs.split('%')[0]}%</span><span>{bs.split('(')[1]?.replace(')','')||''}</span>
        </div>
        <div style={{height:6,background:'var(--border)',borderRadius:'var(--radius-full)',overflow:'hidden'}}>
          <div style={{height:'100%',width:bs.split('%')[0]+'%',background:'var(--primary)',borderRadius:'var(--radius-full)',transition:'width 0.3s'}}></div>
        </div>
      </div> : <span className="step" style={{color:'var(--primary)'}}><IconLoading size={12} style={{display:'inline',verticalAlign:'middle',marginRight:4}} />{bs}...</span>)}
    </div>}

    {s === 0 && <div style={{textAlign:'center',padding:'36px 16px'}}>
      <div style={{fontSize:17,fontWeight:700,marginBottom:6}}>选择导入类型</div>
      <div className="small muted" style={{fontSize:'var(--font-sm)',marginBottom:18}}>订单 / 库存 / 出入库 / 商品 / 供应商</div>
      <div style={{display:'flex',justifyContent:'center',gap:8,marginBottom:24}}>
        <select value={tt} onChange={e=>setTt(e.target.value)} style={{fontSize:'var(--font-15)',padding:'11px 16px',border:'1px solid var(--border)',borderRadius:'var(--radius-full)',outline:'none',background:'var(--card)',minWidth:200,minHeight:46}}>
          <option value='order'>导入订单</option>
          <optgroup label="库存">
            <option value='inventory'>自有仓库存</option>
            <option value='platform_inv'>平台仓库存</option>
            <option value='inventory_b'>B仓库存</option>
          </optgroup>
          <optgroup label="入库出库记录">
            <option value='inbound'>入库记录</option>
            <option value='outbound'>出库记录</option>
          </optgroup>
          <option value='product'>导入商品</option>
          <option value='supplier'>导入供应商</option>
        </select>
      </div>
      <label className="btn btn-primary" style={{minHeight:46,padding:'0 28px',display:'inline-flex',alignItems:'center',gap:6,borderRadius:'var(--radius-full)',fontSize:'var(--font-15)',fontWeight:600,opacity:bs?0.65:1,pointerEvents:bs?'none':'auto'}}>
        {bs?'识别中...':t("cleansing.select_file")}
        <input type="file" accept=".csv,.xlsx" style={{display:'none'}} onChange={e=>{const fi=e.target.files[0];if(fi)detect(fi)}} />
      </label>
      {bs && <div style={{marginTop:14,display:'flex',justifyContent:'center',alignItems:'center',gap:5,color:'var(--primary)',fontSize:'var(--font-13)'}}><IconLoading size={14} /> 正在识别文件...</div>}
      <div className="small muted" style={{marginTop:bs?8:12,fontSize:'var(--font-sm)'}}>支持 CSV / Excel · 智能识别列名 · 手工映射为主</div>
    </div>}

    {s === 1 && <div>
      <div style={{display:'flex',alignItems:'center',gap:6,marginTop:2,marginBottom:10}}>
        <span style={{fontSize:'var(--font-md)',fontWeight:700}}>列映射</span>
        <span className="small muted" style={{fontSize:'var(--font-xs)'}}>选择文件列对应的目标字段 · 未映射列导入时丢弃</span>
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
        return (<div key={c.name} style={{display:'flex',alignItems:'center',gap:10,padding:'10px 14px',border:'1px solid var(--border)',borderRadius:'var(--radius-card)',marginBottom:8}}>
        <div style={{flex:1,fontSize:'var(--font-md)',fontWeight:500,minWidth:0}}>
          <span style={{display:'inline-flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
            {c.name}
            {mp[c.name]?.target
              ? <span className="pill success" style={{fontSize:'var(--font-9)',padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>✓ 已映射</span>
              : <span className="pill warning" style={{fontSize:'var(--font-9)',padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>未映射 · 导入时丢弃</span>}
          </span>
          {mp[c.name]?.target ? (()=>{const sf2=SYS_FIELDS.find(x=>x.t===mp[c.name].target)||cf.find(x=>x.t===mp[c.name].target);return sf2?<span className="small muted" style={{display:'block',fontSize:'var(--font-xs)',marginTop:1}}>→ {sf2.l} ({sf2.t})</span>:null})()
            : (matched && sf ? <span className="small muted" style={{display:'block',fontSize:'var(--font-xs)',marginTop:1}}>智能建议 → {sf.l} ({sf.t})</span> : null)}
        </div>
        <div style={{fontSize:'var(--font-sm)',color:'var(--muted2)',flexShrink:0}}>→</div>
        <select value={mp[c.name]?.target || ''} onChange={e=>{const v=e.target.value;setMp(p=>({...p,[c.name]:{target:v,type:'string'}}));saveAlias(c.name,v)}}
          style={{fontSize:'var(--font-md)',padding:'7px 10px',border:'1px solid var(--border)',borderRadius:'var(--radius-lg)',flex:1,minWidth:130,background:'var(--card)',minHeight:36}}>
          <option value="">-- 不映射 --</option>
          {(TARGET_FIELDS[tt] || SYS_FIELDS).map(f => <option key={f.t} value={f.t}>{f.l}</option>)}
          {cf.filter(f => f.t && f.l).map(f => <option key={f.t} value={f.t}>{f.l}</option>)}
        </select>
        <div style={{fontSize:'var(--font-xs)',width:50,textAlign:'right',flexShrink:0}}>
          {isShared ? <span className="pill warning" style={{fontSize:'var(--font-9)',padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>×{targetCounts[currentTarget]}</span> : null}
        </div>
      </div>)
      })}
      </div>}

    {s === 2 && pv && <div>
        <div className="section-title">
          清洗预览 · 前 {pv.preview?.length||0} 行
          {pv.total > 50 ? <span className="small muted"> · 共 {pv.total} 行，仅展示前 50 行</span> : ''}
          {pv.preview?.length > 0 && <span className="small muted"> · {Object.keys(pv.preview[0]).filter(k=>k!=='_source').length} 列</span>}
        </div>
        {(() => {
          // 脏数据预检: 缺SKU/重复SKU 统计(导入前发现)
          const skuCol = Object.entries(mp).find(([,cfg]) => cfg && cfg.target === 'sku')
          if (!skuCol) return null
          const skuVals = (pv.preview||[]).map(r => String(r[skuCol[1].target]||''))
          const seen = new Map(); const dupSkus = new Set()
          skuVals.forEach(v => { if (v === '') return; seen.set(v, (seen.get(v)||0)+1); if (seen.get(v) > 1) dupSkus.add(v) })
          const missing = skuVals.filter(v => v === '').length
          if (missing === 0 && dupSkus.size === 0) return null
          return <div style={{fontSize:'var(--font-xs)',color:'var(--danger)',background:'rgba(239,68,68,0.06)',border:'1px solid rgba(239,68,68,0.2)',borderRadius:'var(--radius-card)',padding:'8px 12px',marginBottom:8}}>
            ⚠ 预览发现 <b>{missing}</b> 行缺SKU、<b>{dupSkus.size}</b> 个重复SKU（已标红）—— 建议返回修正后导入
          </div>
        })()}
        {(() => {
          if (!pv.preview?.length) return null
          // 按来源列显示：只显示有映射的列（target 非空），unmap 的列直接不出现
          const mappedSources = Object.entries(mp).filter(([, v]) => v && v.target)
          const cols = mappedSources.map(([src, cfg]) => {
            const sf = (TARGET_FIELDS[tt] || SYS_FIELDS).find(x => x.t === cfg.target) || cf.find(x => x.t === cfg.target)
            return {src, target: cfg.target, label: sf ? sf.l : cfg.target}
          })
          // 脏数据预检: 重复 SKU 集合(表体行标红用)
          const _skuCol = cols.find(c => c.target === 'sku')
          const _skuVals = (pv.preview||[]).map(r => _skuCol ? String(r[_skuCol.target]||'') : '')
          const _seen = new Map(); const dupSkus = new Set()
          _skuVals.forEach(v => { if (!v) return; _seen.set(v, (_seen.get(v)||0)+1); if (_seen.get(v) > 1) dupSkus.add(v) })
          if (cols.length === 0) return <div className="small muted" style={{padding:20,textAlign:'center'}}>没有已映射的字段，请返回并设置字段映射</div>
          return <div style={{marginBottom:12}}>
            <div style={{fontSize:'var(--font-xs)',color:'var(--muted2)',marginBottom:4}}>← 左右滑动查看 · 仅显示已映射的 {cols.length} 列 →</div>
            <div style={{overflow:"auto",maxHeight:"calc(100vh - 180px)"}}>
            <table><thead><tr style={{position:"sticky",top:0,background:"var(--card)",zIndex:1}}>{cols.map(col => (
              <th key={col.src} style={{minWidth:80,whiteSpace:'nowrap',verticalAlign:'top'}}>
                {col.label}
                <div className="small muted text-9 font-400">← {col.src}</div>
              </th>
            ))}</tr></thead>
            <tbody>{pv.preview.map((r,i) => {
              // 脏数据预检: 关键字段(sku)缺失/重复 → 行标红(导入前发现, 非执行后才知道)
              const skuCol = cols.find(c => c.target === 'sku')
              const skuVal = skuCol ? String(r[skuCol.target]||'') : ''
              const issue = skuVal === '' ? '缺SKU' : (dupSkus.has(skuVal) ? '重复SKU' : '')
              return <tr key={i} style={issue ? {background:'rgba(239,68,68,0.06)'} : undefined}>
                {cols.map(col => (
                  <td key={col.src} style={{minWidth:80,whiteSpace:'nowrap',maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',color:issue && col.target==='sku' ? 'var(--danger)' : undefined}}>{String(r[col.target]||'')}{issue && col.target==='sku' ? <span style={{fontSize:'var(--font-9)',color:'var(--danger)',marginLeft:4}}>⚠{issue}</span> : null}</td>
                ))}
              </tr>
            })}</tbody></table>
            </div>
          </div>
        })()}
        <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
          {btn('← 返回', ()=>{goStep(1);setPv(null)}, 'ghost')}
          {btn('确认写入 ('+pv.total+' 条)', doExecute, 'success')}
        </div>
      </div>}
    {s === 1 && <div style={{marginTop:16,display:'flex',gap:10}}>
      <button onClick={()=>goStep(0)} className="clickable" style={{flexShrink:0,width:88,padding:'11px 0',fontSize:'var(--font-md)',border:'1px solid var(--border)',borderRadius:'var(--radius-full)',background:'var(--card)',cursor:'pointer',fontWeight:600,minHeight:42,whiteSpace:'nowrap'}}>← 返回</button>
      <button onClick={preview} className="clickable" style={{flex:1,padding:'11px 12px',fontSize:'var(--font-md)',border:'none',borderRadius:'var(--radius-full)',background:'var(--primary)',color:'#fff',cursor:'pointer',fontWeight:600,minHeight:42,display:'inline-flex',alignItems:'center',gap:5,justifyContent:'center',whiteSpace:'nowrap'}}>下一步 · 预览</button>
      <button onClick={quickExecute} className="clickable" style={{flex:1,padding:'11px 12px',fontSize:'var(--font-md)',border:'none',borderRadius:'var(--radius-full)',background:'var(--success)',color:'#fff',cursor:'pointer',fontWeight:600,minHeight:42,display:'inline-flex',alignItems:'center',gap:5,justifyContent:'center',whiteSpace:'nowrap'}}><IconLightning size={14} /> 一键执行</button>
    </div>}

    {s === 3 && res && <div style={{textAlign:'center',padding:40}}>
      <div style={{fontSize:32,marginBottom:4}}>{res.success > 0 ? <IconCheck size={32} style={{color:'var(--success)'}} /> : <IconAlert size={32} style={{color:'var(--warning)'}} />}</div>
      {f?.name ? <div className="small muted" style={{fontSize:'var(--font-sm)',marginBottom:8}}>{f.name}</div> : ''}
      <div style={{fontWeight:700,fontSize:'var(--font-18)',marginBottom:4,color:res.error ? "var(--danger)" : ""}}>
        {res.error ? '清洗失败' : (res.success > 0 ? '清洗完成' : '清洗完成（无新增）')}
      </div>
      <div className="small muted" style={{marginBottom:16}}>{res.error || res.message || ''}{res.error ? <span style={{marginLeft:6,fontSize:'var(--font-sm)',color:'var(--warning)'}}>（侧边栏 <IconAlert size={12} style={{display:'inline',verticalAlign:'middle'}} /> 查看详情）</span> : ''}</div>
      <div style={{display:'flex',justifyContent:'center',gap:24,marginBottom:16}}>
        <div><div style={{fontSize:24,fontWeight:700,color:'var(--success)'}}>{res.success}</div><div className="small muted">成功</div></div>
        <div><div style={{fontSize:24,fontWeight:700,color:res.failed > 0 ? 'var(--danger)' : 'var(--muted2)'}}>{res.failed}</div><div className="small muted">跳过</div></div>
      </div>
      {res.failed > 0 && Array.isArray(res.failed_details) && res.failed_details.length > 0 && (
        <div style={{textAlign:'left',background:'var(--bg)',borderRadius:'var(--radius-card)',padding:'10px 14px',marginBottom:14,maxHeight:180,overflowY:'auto'}}>
          <div style={{fontWeight:600,fontSize:'var(--font-sm)',marginBottom:6,color:'var(--danger)'}}>失败明细（可对照修正后重新导入）</div>
          {res.failed_details.map((d, i) => (
            <div key={i} style={{fontSize:'var(--font-xs)',color:'var(--muted2)',padding:'3px 0',borderBottom:'0.5px solid var(--border)',display:'flex',gap:8}}>
              <span className="mono" style={{color:'var(--text)',flexShrink:0}}>{d.sku || '-'}</span>
              <span style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{d.reason || ''}</span>
            </div>
          ))}
        </div>
      )}
      <div style={{display:'flex',gap:8,justifyContent:'center'}}>
        <button onClick={()=>{goStep(0);setF(null);setCols([]);setTr(0);setMp({});setPv(null);setRes(null)}}
          className="btn btn-ghost">重新开始</button>
        <label className="btn btn-success" style={{display:'inline-flex',alignItems:'center',gap:4}}>
          <IconFolder size={14} /> 导入相同格式
          <input type="file" accept=".csv,.xlsx" style={{display:'none'}} onChange={e=>{
            const fi=e.target.files[0]
            if(fi){setF(fi);setBs('识别中');goStep(1);detect(fi)}
          }}/>
        </label>
      </div>
    </div>}

    {colMapOpen && <div onClick={()=>setColMapOpen(false)} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />}
    {colMapOpen && <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
      <div onClick={(e)=>e.stopPropagation()} className="material-regular" style={{width:'100%',maxWidth:600,borderRadius:'var(--radius-lg)',padding:'18px 14px calc(14px + env(safe-area-inset-bottom))',boxShadow:'var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)',pointerEvents:'auto',maxHeight:'70vh',overflowY:'auto'}}>
        <div style={{fontSize:'var(--font-18)',fontWeight:700,marginBottom:12,textAlign:'center'}}>表格列状态映射</div>
        <div style={{textAlign:'center',fontSize:'var(--font-xs)',color:'var(--muted2)',marginBottom:12}}>按列配置 值→档位，导入时自动归一化/筛选 · 渠道：{ch==='jd'?'京东':'其他渠道'}（全局主体隔离）</div>
        <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:10}}>
          <span style={{fontSize:'var(--font-13)',fontWeight:600,flexShrink:0}}>映射列</span>
          <select value={colMapCol} onChange={e=>setColMapCol(e.target.value)} style={{flex:1,fontSize:'var(--font-md)',padding:'8px 12px',border:'1px solid var(--border)',borderRadius:'var(--radius-lg)',background:'var(--card)',outline:'none'}}>
            <option value="order_status">订单状态（销量池判定）</option>
          </select>
        </div>
        {(colMap[colMapCol]||[]).map((s,i)=>(
          <div key={i} style={{display:'flex',alignItems:'center',gap:8,padding:'8px 12px',border:'1px solid var(--border)',borderRadius:'var(--radius-lg)',marginBottom:6}}>
            <input value={s.name||''} onChange={e=>setColMap(p=>({...p,[colMapCol]:(p[colMapCol]||[]).map((x,j)=>j===i?{...x,name:e.target.value}:x)}))} placeholder='值(如 交易成功)' style={{flex:1,minWidth:100,fontSize:'var(--font-15)',padding:'7px 10px',border:'1px solid var(--border)',borderRadius:'var(--radius-lg)',outline:'none'}}/>
            <select value={s.group||'blocked'} onChange={e=>setColMap(p=>({...p,[colMapCol]:(p[colMapCol]||[]).map((x,j)=>j===i?{...x,group:e.target.value}:x)}))} style={{fontSize:'var(--font-13)',padding:'7px 10px',border:'1px solid var(--border)',borderRadius:'var(--radius-lg)',background:'var(--card)',minHeight:36}}>
              <option value='sale'>✅ 销量池</option><option value='blocked'>⛔ 屏蔽</option>
            </select>
            <button onClick={()=>setColMap(p=>({...p,[colMapCol]:(p[colMapCol]||[]).filter((_,j)=>j!==i)}))} className="clickable" style={{fontSize:'var(--font-sm)',color:'var(--danger)',cursor:'pointer',padding:'4px 8px',border:'none',background:'transparent',flexShrink:0}}>✕</button>
          </div>
        ))}
        <div style={{display:'flex',gap:8,marginTop:4,flexWrap:'wrap'}}>
          <button onClick={()=>setColMap(p=>({...p,[colMapCol]:[...(p[colMapCol]||[]),{name:'',group:'blocked'}]}))} className="btn btn-ghost clickable" style={{fontSize:'var(--font-sm)',padding:'7px 14px',minHeight:36}}>+ 添加值</button>
          <button onClick={()=>setColMap(p=>({...p,order_status:[{name:'已完成',group:'sale'},{name:'交易成功',group:'sale'},{name:'确认收货',group:'sale'},{name:'已签收',group:'sale'},{name:'妥投',group:'sale'},{name:'Closed',group:'sale'},{name:'Completed',group:'sale'},{name:'待发货',group:'blocked'},{name:'已发货',group:'blocked'},{name:'待确认',group:'blocked'},{name:'待付款',group:'blocked'},{name:'已取消',group:'blocked'},{name:'已退款',group:'blocked'},{name:'退款中',group:'blocked'},{name:'申请退款',group:'blocked'},{name:'已退货',group:'blocked'},{name:'运输中',group:'blocked'},{name:'在途',group:'blocked'}]}))} className="btn btn-ghost clickable" style={{fontSize:'var(--font-sm)',padding:'7px 14px',minHeight:36}}>填充内置默认</button>
        </div>
        <div style={{display:'flex',gap:10,marginTop:14}}>
          <div onClick={()=>setColMapOpen(false)} className="clickable" style={{flex:1,borderRadius:'var(--radius-card)',padding:12,background:'var(--card)',textAlign:'center',cursor:'pointer',border:'1px solid var(--border)'}}><span style={{fontSize:'var(--font-15)',fontWeight:600,color:'var(--text)'}}>取消</span></div>
          <div onClick={()=>{if(!colMapSaving)saveColMap()}} className="clickable" style={{flex:1,borderRadius:'var(--radius-card)',padding:12,background:'var(--primary)',textAlign:'center',cursor:'pointer',opacity:colMapSaving?0.6:1}}><span style={{fontSize:'var(--font-15)',fontWeight:600,color:'#fff'}}>{colMapSaving?'保存中...':'保存'}</span></div>
        </div>
      </div>
    </div>}

  </div>
};
