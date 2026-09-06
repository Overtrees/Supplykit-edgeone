import React, { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import { IconClipboard, IconPackage, IconImport, IconExport, IconTrendUp, IconLightning, IconCheck, IconAlert, IconLoading, IconFolder, IconTag } from '../components/Icons'
import { t } from "../locale"

const API = import.meta.env.VITE_API_BASE_URL || ''
const INV_FIELDS = [
  {t:'warehouse',l:'仓库',tp:'string'},{t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'product_name',l:'商品',tp:'string'},
  {t:'channel',l:'平台',tp:'string'},
  {t:'beginning_stock',l:'期初库存',tp:'number'},{t:'in_transit_qty',l:'在途',tp:'number'},
  {t:'month_inbound',l:'当月采购入库',tp:'number'},{t:'month_outbound',l:'当月出库',tp:'number'},
  {t:'available_qty',l:'可用',tp:'number'},{t:'turnover_days',l:'在库周转',tp:'number'},
  {t:'c_transit',l:'B-C调拨在途',tp:'number'},
  {t:'locked_qty',l:'锁定库存',tp:'number'},{t:'safety_qty',l:'安全线',tp:'number'},
  {t:'weight',l:'箱重/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},
]
const PROD_FIELDS = [
  {t:'sku',l:'SKU',tp:'string'},{t:'barcode',l:'69码',tp:'string'},{t:'channel',l:'平台',tp:'string'},
  {t:'product_name',l:'名称',tp:'string'},{t:'store',l:'店铺',tp:'string'},{t:'category',l:'分类',tp:'string'},
  {t:'price',l:'单价',tp:'number'},{t:'box_qty',l:'箱规',tp:'number'},{t:'unit',l:'单位',tp:'string'},
  {t:'weight',l:'箱重/KG',tp:'number'},{t:'volume',l:'体积/方',tp:'number'},{t:'status',l:'状态',tp:'string'},
]
const SYS_FIELDS = [
  {t:'order_no',l:'订单号',tp:'string'},{t:'barcode',l:'69码',tp:'string'},
  {t:'store',l:'店铺',tp:'string'},{t:'warehouse',l:'仓库',tp:'string'},
  {t:'product_name',l:'商品',tp:'string'},{t:'total_amount',l:'金额',tp:'number'},
  {t:'order_status',l:'状态',tp:'string'},{t:'ordered_at',l:'下单日期',tp:'date'},
  {t:'paid_at',l:'入库日期',tp:'date'},
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

export default function CleansingPage() {
  const toast = useToast()
  const [s,setS] = useState(0)
  const [f,setF] = useState(null)
  const [cols,setCols] = useState([])
  const [tr,setTr] = useState(0)
  const [tt,setTt] = useState('order')
  const { hammerCleansingTarget, setHammerCleansingTarget, hammerCleansingConflict } = useAppStore()
  useEffect(() => { setHammerCleansingTarget(tt) }, [tt])
  const { hammerCleansingChannel: ch, setHammerCleansingChannel: setCh } = useAppStore()
  const [mp,setMp] = useState({})
  const [pv,setPv] = useState(null)
  const [res,setRes] = useState(null)
  const [bs,setBs] = useState('')
  const [cf,setCf] = useState(() => { try { return JSON.parse(localStorage.getItem('c_cf')||'[]') } catch { return [] } })
  const [templates, setTemplates] = useState([])
  const saveCf = (v) => { setCf(v); try { localStorage.setItem('c_cf', JSON.stringify(v)) } catch {} }

  const loadTemplates = async () => { try { const r = await api.get('/api/cleansing/templates'); setTemplates(r.data || []) } catch(e) {} }
  useEffect(() => { loadTemplates() }, [])

  const addField = () => saveCf([...cf, {t:'field_'+Date.now(), l:'自定义字段', tp:'string'}])
  const delField = (i) => saveCf(cf.filter((_,k) => k !== i))

  const detect = async (file) => {
    setF(file); setBs('识别中')
    const fd = new FormData(); fd.append('file', file)
    try {
      const r = await api.post('/api/cleansing/detect', fd)
      const d = r.data
      if (!d.ok) { toast.error(d.error||'识别失败'); setBs(''); return }
      setCols(d.columns||[]); setTr(d.total||0)
      const a = {}
      let mappedCount = 0
      ;(d.columns||[]).forEach(c => {
        const key = ALIAS[c.name]
        if (key) { a[c.name] = { target: key, type: 'string' }; mappedCount++ }
      })
      if (Object.keys(a).length > 0) setMp(a)
      setS(1)
      if ((d.columns||[]).length > 0 && mappedCount === (d.columns||[]).length) {
        setMp(a)
        setBs('预览中')
        const fd2 = new FormData(); fd2.append('file', file); fd2.append('mapping', JSON.stringify(a)); fd2.append('target', tt)
        try {
          const r2 = await api.post('/api/cleansing/preview', fd2)
          const d2 = r2.data
          if (!d2.ok) { toast.error(d2.error||'预览失败'); setBs(''); return }
          setPv(d2); setS(2)
        } catch(e) { toast.error('请求异常: '+e.message) }
        setBs('')
      }
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
      setPv(d); setS(2)
    } catch(e) {
      const msg = e.response?.data?.error || e.message || '请求失败'
      toast.error('预览失败: '+msg)
    }
    setBs('')
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
        setRes(d); setS(3); setBs(''); execLock.current = false
        toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
        return
      }
      const totalRows = d.total_rows || '?'
      let finished = false; let threshold = setTimeout(() => {
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
              setRes(sd.result); setS(3); setBs(''); toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
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
    <div className="step-indicator">
      {['上传文件','字段映射','预览确认','完成'].map((l,i) => <span key={i} className={'step'+(s===i?' active':'')+(s>i?' done':'')}>{s>i?<IconCheck size={12} style={{display:'inline',verticalAlign:'middle',marginRight:2}} />:''}{l}</span>)}
      {bs && (bs.includes('%') ? <div className="step w-full">
        <div style={{display:'flex',justifyContent:'space-between',fontSize:12,marginBottom:4,color:'var(--primary)'}}>
          <span><IconLoading size={12} style={{display:'inline',verticalAlign:'middle',marginRight:4}} />{bs.split('%')[0]}%</span><span>{bs.split('(')[1]?.replace(')','')||''}</span>
        </div>
        <div style={{height:6,background:'var(--border)',borderRadius:99,overflow:'hidden'}}>
          <div style={{height:'100%',width:bs.split('%')[0]+'%',background:'var(--primary)',borderRadius:99,transition:'width 0.3s'}}></div>
        </div>
      </div> : <span className="step" style={{color:'var(--primary)'}}><IconLoading size={12} style={{display:'inline',verticalAlign:'middle',marginRight:4}} />{bs}...</span>)}
    </div>

    {s === 0 && <div style={{textAlign:'center',padding:40}}>
      <div style={{display:'flex',justifyContent:'center',gap:8,marginBottom:16}}>
        <select value={tt} onChange={e=>setTt(e.target.value)} style={{fontSize:16,padding:'8px 16px',border:'1px solid var(--border)',borderRadius:32,outline:'none',background:'var(--card)',minWidth:180}}>
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
      <label className="btn btn-primary">
        {bs?'识别中...':t("cleansing.select_file")}
        <input type="file" accept=".csv,.xlsx" style={{display:'none'}} onChange={e=>{const fi=e.target.files[0];if(fi)detect(fi)}} />
      </label>
      <div className="small muted" style={{marginTop:8}}>CSV / Excel · 中文列名自动匹配</div>
    </div>}

    {s === 1 && <div>
      <div style={{fontSize:13,marginBottom:12}}>已识别 {cols.length} 列 · {tr} 行 · 目标: {tt}{tt==='order' && <span style={{marginLeft:8,display:'inline-flex',gap:4,verticalAlign:'middle'}}>
        <span onClick={()=>setMp(p=>({...p,_meta:{data_source:'jdzx_sale'}}))} className="clickable" style={{padding:'4px 10px',fontSize:12,borderRadius:99,border:'1px solid',cursor:'pointer',display:'inline-flex',alignItems:'center',gap:3,background:mp?._meta?.data_source==='jdzx_sale'?'var(--primary)':'var(--card)',color:mp?._meta?.data_source==='jdzx_sale'?'#fff':'var(--muted)',borderColor:mp?._meta?.data_source==='jdzx_sale'?'var(--primary)':'var(--border)'}}><IconTrendUp size={12} /> 商智日销</span>
        <span onClick={()=>setMp(p=>({...p,_meta:{data_source:'jd_po'}}))} className="clickable" style={{padding:'4px 10px',fontSize:12,borderRadius:99,border:'1px solid',cursor:'pointer',display:'inline-flex',alignItems:'center',gap:3,background:mp?._meta?.data_source==='jd_po'?'var(--primary)':'var(--card)',color:mp?._meta?.data_source==='jd_po'?'#fff':'var(--muted)',borderColor:mp?._meta?.data_source==='jd_po'?'var(--primary)':'var(--border)'}}><IconPackage size={12} /> 京东采购单</span>
      </span>}</div>
      <div style={{display:'flex',gap:8,marginBottom:12,alignItems:'center',flexWrap:'wrap'}}>
        <select id="tmplSelect" style={{flex:1,fontSize:16,padding:'8px 12px',border:'1px solid var(--border)',borderRadius:32,minWidth:140}}>
          <option value="">加载映射模板...</option>
          {Array.isArray(templates) && templates.filter(t => t.doc_type === tt).map(t => <option key={t.id} value={t.mapping}>{t.name}</option>)}
          {Array.isArray(templates) && templates.filter(t => t.doc_type !== tt).length > 0 && <option disabled style={{color:'var(--muted2)',fontSize:11}}>── {tt==='order'?'库存':'订单'}模板（{templates.filter(t=>t.doc_type!==tt).length}个） ──</option>}
        </select>
        <button onClick={()=>{const s=document.getElementById('tmplSelect');if(s.value)try{const m=typeof s.value==='string'&&s.value.startsWith('{')?JSON.parse(s.value):s.value;setMp(m&&typeof m==='object'?m:{})}catch(e){console.error(e)}}} className="clickable" style={{padding:'7px 16px',fontSize:13,border:'1px solid var(--border)',borderRadius:32,background:'var(--card)',cursor:'pointer',minHeight:36}}>应用</button>
        <input id="tmplName" placeholder="新模板名称" style={{width:130,fontSize:16,padding:'7px 10px',border:'1px solid var(--border)',borderRadius:32,outline:'none'}}/>
        <button onClick={async()=>{
          const n=document.getElementById('tmplName').value;if(!n)return toast.error('请输入模板名称');
          try {
            const r=await api.post('/api/cleansing/templates',{name:n,doc_type:tt,mapping:mp});
            const msg=r?.data?.message||'模板已保存';
            document.getElementById('tmplName').value='';loadTemplates();toast.success(msg);
          } catch(e){toast.error('模板保存失败: '+(e.response?.data?.detail||e.message));}
        }} className="clickable" style={{padding:'7px 16px',fontSize:13,background:'var(--primary)',color:'var(--card)',border:'none',borderRadius:32,cursor:'pointer',minHeight:36}}>保存</button>
      </div>
      {Array.isArray(cf) && <div style={{marginBottom:12,border:'1px solid var(--border)',borderRadius:32,padding:14,background:'var(--bg)'}}>
        <div style={{fontSize:13,fontWeight:600,marginBottom:10}}>自定义字段</div>
        {cf.map((f,i) => <div key={i} style={{display:'flex',alignItems:'center',gap:8,marginBottom:8}}>
          <input value={f.l} onChange={e=>{const v=e.target.value;setCf(p=>p.map((x,k)=>k===i?{...x,l:v}:x))}} placeholder="字段名" style={{flex:1,fontSize:16,padding:'7px 10px',border:'1px solid var(--border)',borderRadius:32,outline:'none'}}/>
          <select value={f.tp} onChange={e=>{const v=e.target.value;setCf(p=>p.map((x,k)=>k===i?{...x,tp:v}:x))}} style={{fontSize:14,padding:'6px 10px',border:'1px solid var(--border)',borderRadius:32}}>
            <option value="string">文本</option><option value="number">数字</option><option value="date">日期</option>
          </select>
          <button onClick={()=>delField(i)} className="clickable" style={{background:'rgba(225,29,72,0.12)',border:'none',borderRadius:32,cursor:'pointer',padding:'6px 12px',fontSize:13,color:'var(--danger)',minHeight:36}}>删除</button>
        </div>)}
        <button onClick={addField} className="clickable" style={{padding:'7px 16px',fontSize:13,border:'1px dashed #94a3b8',borderRadius:32,background:'var(--card)',cursor:'pointer',color:'var(--muted)',width:'100%',minHeight:36}}>+ 添加自定义字段</button>
      </div>}
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
        return (<div key={c.name} style={{display:'flex',alignItems:'center',gap:10,padding:'8px 12px',border:'1px solid var(--border)',borderRadius:32,marginBottom:6}}>
        <div style={{flex:1,fontSize:14,fontWeight:500,minWidth:0}}>
          {c.name}
          {matched && sf && <span className="small muted" style={{display:'block',fontSize:11,marginTop:1}}>→ {sf.l} ({sf.t})</span>}
        </div>
        <div style={{fontSize:12,color:'var(--muted2)',flexShrink:0}}>→</div>
        <select value={mp[c.name]?.target || ''} onChange={e=>{const v=e.target.value;setMp(p=>({...p,[c.name]:{target:v,type:'string'}}))}}
          style={{fontSize:14,padding:'7px 10px',border:'1px solid var(--border)',borderRadius:32,flex:1,minWidth:130,background:'var(--card)',minHeight:36}}>
          <option value="">-- 不映射 --</option>
          {(tt==='inventory'?INV_FIELDS:tt==='product'?PROD_FIELDS:SYS_FIELDS).map(f => <option key={f.t} value={f.t}>{f.l}</option>)}
          {cf.filter(f => f.t && f.l).map(f => <option key={f.t} value={f.t}>{f.l}</option>)}
        </select>
        <div style={{fontSize:11,width:50,textAlign:'right',flexShrink:0}}>
          {isShared ? <span className="pill warning" style={{fontSize:9,padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>×{targetCounts[currentTarget]}</span> : null}
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
          if (!pv.preview?.length) return null
          // 按来源列显示：只显示有映射的列（target 非空），unmap 的列直接不出现
          const mappedSources = Object.entries(mp).filter(([, v]) => v && v.target)
          const cols = mappedSources.map(([src, cfg]) => {
            const sf = SYS_FIELDS.find(x => x.t === cfg.target) || cf.find(x => x.t === cfg.target)
            return {src, target: cfg.target, label: sf ? sf.l : cfg.target}
          })
          if (cols.length === 0) return <div className="small muted" style={{padding:20,textAlign:'center'}}>没有已映射的字段，请返回并设置字段映射</div>
          return <div style={{marginBottom:12}}>
            <div style={{fontSize:11,color:'var(--muted2)',marginBottom:4}}>← 左右滑动查看 · 仅显示已映射的 {cols.length} 列 →</div>
            <div style={{overflow:"auto",maxHeight:"calc(100vh - 180px)"}}>
            <table><thead><tr style={{position:"sticky",top:0,background:"var(--card)",zIndex:1}}>{cols.map(col => (
              <th key={col.src} style={{minWidth:80,whiteSpace:'nowrap',verticalAlign:'top'}}>
                {col.label}
                <div className="small muted text-9 font-400">← {col.src}</div>
              </th>
            ))}</tr></thead>
            <tbody>{pv.preview.map((r,i) => (
              <tr key={i}>{cols.map(col => (
                <td key={col.src} style={{minWidth:80,whiteSpace:'nowrap',maxWidth:200,overflow:'hidden',textOverflow:'ellipsis'}}>{String(r[col.target]||'')}</td>
              ))}</tr>
            ))}</tbody></table>
            </div>
          </div>
        })()}
        <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
          {btn('← 返回', ()=>{setS(1);setPv(null)}, 'ghost')}
          {btn('确认写入 ('+pv.total+' 条)', doExecute, 'success')}
        </div>
      </div>}
    {s === 1 && <div style={{marginTop:16,display:'flex',gap:10}}>
      <button onClick={()=>setS(0)} className="clickable" style={{flex:1,padding:'10px',fontSize:14,border:'1px solid var(--border)',borderRadius:99,background:'var(--card)',cursor:'pointer',fontWeight:600,minHeight:40}}>← 返回</button>
      <button onClick={preview} className="clickable" style={{flex:1,padding:'10px',fontSize:14,border:'none',borderRadius:99,background:'var(--primary)',color:'#fff',cursor:'pointer',fontWeight:600,minHeight:40}}>下一步 预览 →</button>
      <button onClick={quickExecute} className="clickable" style={{flex:1,padding:'10px',fontSize:14,border:'none',borderRadius:99,background:'var(--success)',color:'#fff',cursor:'pointer',fontWeight:600,minHeight:40,display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center'}}><IconLightning size={14} /> 一键执行</button>
    </div>}

    {s === 3 && res && <div style={{textAlign:'center',padding:40}}>
      <div style={{fontSize:32,marginBottom:4}}>{res.success > 0 ? <IconCheck size={32} style={{color:'var(--success)'}} /> : <IconAlert size={32} style={{color:'var(--warning)'}} />}</div>
      {f?.name ? <div className="small muted" style={{fontSize:12,marginBottom:8}}>{f.name}</div> : ''}
      <div style={{fontWeight:700,fontSize:18,marginBottom:4,color:res.error ? "var(--danger)" : ""}}>
        {res.error ? '清洗失败' : (res.success > 0 ? '清洗完成' : '清洗完成（无新增）')}
      </div>
      <div className="small muted" style={{marginBottom:16}}>{res.error || res.message || ''}{res.error ? <span style={{marginLeft:6,fontSize:12,color:'var(--warning)'}}>（侧边栏 <IconAlert size={12} style={{display:'inline',verticalAlign:'middle'}} /> 查看详情）</span> : ''}</div>
      <div style={{display:'flex',justifyContent:'center',gap:24,marginBottom:16}}>
        <div><div style={{fontSize:24,fontWeight:700,color:'var(--success)'}}>{res.success}</div><div className="small muted">成功</div></div>
        <div><div style={{fontSize:24,fontWeight:700,color:res.failed > 0 ? 'var(--danger)' : 'var(--muted2)'}}>{res.failed}</div><div className="small muted">跳过</div></div>
      </div>
      <div style={{display:'flex',gap:8,justifyContent:'center'}}>
        <button onClick={()=>{setS(0);setF(null);setCols([]);setTr(0);setMp({});setPv(null);setRes(null)}}
          className="btn btn-ghost">重新开始</button>
        <label className="btn btn-success" style={{display:'inline-flex',alignItems:'center',gap:4}}>
          <IconFolder size={14} /> 导入相同格式
          <input type="file" accept=".csv,.xlsx" style={{display:'none'}} onChange={e=>{
            const fi=e.target.files[0]
            if(fi){setF(fi);setBs('识别中');setS(1);detect(fi)}
          }}/>
        </label>
      </div>
    </div>}

  </div>
};
