import React, { useState, useEffect, useRef } from 'react'
import { api, clearCache, clearInflight } from '../api/client'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import { IconPackage, IconTag, IconFactory, IconClipboard, IconScale, IconSave, IconLoading, IconAlert } from '../components/Icons'
import { t } from "../locale"

const API = import.meta.env.VITE_API_BASE_URL || ''
const EVENTS = [
  {value:'inventory.changed',label:'库存变动'},
  {value:'order.created',label:'订单创建'},
  {value:'scheduled.daily',label:'每日定时'},
]

const VARS = {product_name:'商品名',sku:'SKU',avail:'可用量',safety:'安全线',days:'天数',stock:'库存量',order_qty:'订单数',store:'店铺',warehouse:'仓库'}
const renderTmpl = (text) => {
  if (!text) return null
  const parts = text.split(/(\{(\w+)\})/g)
  return parts.map((p,i) => {
    if (i%3===1) return <span key={i} style={{display:'inline-block',background:'rgba(29,78,216,0.1)',color:'var(--primary)',padding:'0 4px',borderRadius:4,fontWeight:600,fontSize:10}}>{VARS[parts[i+1]]||parts[i+1]}</span>
    if (i%3===2) return null
    return <span key={i}>{p}</span>
  })
}
const IS = {width:'100%',padding:'8px 12px',fontSize:16,border:'1px solid var(--border)',borderRadius:32,marginTop:4,outline:'none',background:'var(--card)',boxSizing:'border-box'}

const LF = [
  {l:'可用库存',v:'inv.available_qty'},{l:'安全库存',v:'inv.safety_qty'},{l:'在途库存',v:'inv.in_transit_qty'},
  {l:'锁定库存',v:'inv.locked_qty'},
  {l:'可用+在途',v:'inv.available_qty + inv.in_transit_qty'},
  {l:'可用+在途+锁定',v:'inv.available_qty + inv.in_transit_qty + inv.locked_qty'},
  {l:'安全线-可用(缺口)',v:'inv.safety_qty - inv.available_qty'},
  {l:'可用/安全线(比例)',v:'inv.available_qty / inv.safety_qty'},
  {l:'日销(定时任务提供)',v:'daily_sales'},
  {l:'可撑天数(可用/日销)',v:'inv.available_qty / daily_sales'},
  {l:'距上次销售(天)',v:'inv.days_since_last'},{l:'库存量',v:'inv.stock'},{l:'仓库类型',v:'inv.warehouse_type'},
  {l:'订单数量',v:'order.quantity'},{l:'订单金额',v:'order.total_amount'},
  {l:'订单数量×单价',v:'order.quantity * order.unit_price'},{l:'单价',v:'order.unit_price'}]
const OPS = [{l:'小于',v:'<'},{l:'小于等于',v:'<='},{l:'大于',v:'>'},{l:'大于等于',v:'>='},{l:'等于',v:'=='},{l:'不等于',v:'!='}]
const WHS = [{l:'全部',v:''},{l:'B仓',v:'platform_b'},{l:'C仓',v:'platform'},{l:'自有仓',v:'own'}]
const MODES = [{l:'全部',v:''},{l:'BBCC',v:'bbcc'},{l:'传统多仓',v:'traditional'}]
const fieldLbl = v => {const f=LF.find(x=>x.v===v);return f?f.l:v}
const opLbl = v => {const o=OPS.find(x=>x.v===v);return o?o.l:v}
const sevCls = s => s==='error'?'danger':s==='info'?'info':'warning'
const sevLbl = s => s==='error'?'严重':s==='info'?t("rules.severity_info"):t("rules.severity_warning")

const pc = j => {
  try {
    const c = JSON.parse(j); let rt = c.rightType||'field'; let r = c.right||'inv.safety_qty'; let pct = 100; let wh = c.warehouse||''
    const m = typeof r==='string'?r.match(/^max\(1,\s*(\w+(?:\.\w+)*)\s*\*\s*([\d.]+)\)$/):null
    if (m) { r=m[1]; rt='pct'; pct=Math.round(parseFloat(m[2])*100) }
    if (!rt||rt==='field') { const f=LF.find(x=>x.v===r); if(!f&&typeof r==='string'&&!r.replace('.','').match(/^\d+$/))rt='text'; else if(!f)rt='number' }
    return {left:c.left||'inv.available_qty', op:c.op||'<', right:r, rightType:rt, pctValue:pct, warehouse:wh}
  } catch { return {left:'inv.available_qty', op:'<', right:'inv.safety_qty', rightType:'field', pctValue:100, warehouse:''} }
}

export default function RulesPage() {
  const toast = useToast()
  const [saveLoading, setSaveLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [seasonsSaving, setSeasonsSaving] = useState(false)
  // 规则测试（可视化调试）：testRule 当前测试的规则 / testInv 模拟库存 / testResult 测试结果
  const [testRule, setTestRule] = useState(null)
  const [testInv, setTestInv] = useState({ available_qty: 0, safety_qty: 0, in_transit_qty: 0, warehouse_type: '', days_since_last: 0, order_quantity: 0 })
  const [testResult, setTestResult] = useState(null)
  const [testLoading, setTestLoading] = useState(false)

  const runTest = async () => {
    if (!testRule) return
    setTestLoading(true)
    try {
      const r = await fetch(API + '/api/rules/' + testRule.id + '/test', { method: 'POST', headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })(), 'Content-Type': 'application/json' }, body: JSON.stringify({ inv: { available_qty: Number(testInv.available_qty)||0, safety_qty: Number(testInv.safety_qty)||0, in_transit_qty: Number(testInv.in_transit_qty)||0, warehouse_type: testInv.warehouse_type, days_since_last: Number(testInv.days_since_last)||0 }, order: { quantity: Number(testInv.order_quantity)||0 } }) })
      const d = await r.json()
      if (d.ok) setTestResult(d.data)
      else toast.error('测试失败: ' + (d.error || ''))
    } catch(e) { toast.error('测试失败: ' + e.message) }
    setTestLoading(false)
  }
  const [loading, setLoading] = useState(true)
  const [rules, setRules] = useState([])
  const [rulesErr, setRulesErr] = useState('')
  const [debugLog, setDebugLog] = useState([])
  const addDebug = (msg, data) => { const t = new Date().toLocaleTimeString(); setDebugLog(p => [{t, msg, data}, ...p].slice(0,50)) }
  const [editing, setEditing] = useState(null)
  const [cfg, setCfg] = useState({})
  const [seasons, setSeasons] = useState([])
  // 滞销品类配置（自定义条目，仿活动系数）
  const [slowCats, setSlowCats] = useState([])
  const [transitDays, setTransitDays] = useState('3')
  const [fundThreshold, setFundThreshold] = useState('10000')
  useEffect(() => { if (cfg.transit_days) setTransitDays(cfg.transit_days) }, [cfg.transit_days])
  useEffect(() => { if (cfg.slow_fund_threshold) setFundThreshold(cfg.slow_fund_threshold) }, [cfg.slow_fund_threshold])
  const reqSeq = useRef(0)
  const [selectedSupplier, setSelectedSupplier] = useState('')
  // 保存错误统一提示（403 访客模式显示后端 detail）
  const saveErr = (e) => {
    const detail = e?.response?.data?.detail
    if (detail) toast.error(String(detail))
    else if (e?.response?.status === 403) toast.error('访客模式仅可查看，不可修改数据')
    else toast.error('保存失败: ' + (e.message || ''))
  }
  const [suppliers, setSuppliers] = useState([])

  const defaultF = {name:'', event:'inventory.changed', alert_type:'low_stock', alert_title:'', alert_desc:'', severity:'warning', condition_json:'{}'}
  const [f, setF] = useState(defaultF)
  const [cond, setCond] = useState({left:'inv.available_qty', op:'<', right:'inv.safety_qty', rightType:'field', pctValue:100, warehouse:''})
  const { channel: globalChannel, setChannel: setGlobalChannel, hammerRulesTab: tab, hammerRuleNewVersion, hammerRulesMode, hammerSearch, prodBatch, setProdBatch, prodSelIds, setProdBatchSel, setProdBatchFilterLen, prodBatchVersion, bumpProdBatchVersion, prodBatchAllReq } = useAppStore()
  useEffect(() => {
    api.get('/api/replenishment-config/slow-cats?channel=' + globalChannel).then(r => { if (Array.isArray(r.data)) setSlowCats(r.data) }).catch(() => {})
  }, [globalChannel])
  const selIds = prodSelIds || []
  const setSelIds = setProdBatchSel
  useEffect(()=>{ if(!prodBatch) setProdBatchSel([]) },[prodBatch])
  useEffect(()=>{ if(prodBatchVersion>0) load(globalChannel) },[prodBatchVersion])

  

  const load = async (ch) => { 
    try { 
      const c=ch||globalChannel; 
      addDebug('load 开始', {channel: c, 当前rules数: rules.length})
      const token = (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()
      const r = await fetch(API+'/api/rules?channel='+c, {headers:{'Authorization':'Bearer '+token}})
      const d = await r.json()
      // 防御: 异常响应(401/500/结构异常)可能非数组 → setRules(object) 导致 rules.length
      // undefined → 渲染条件全 false → 空白无提示。数组校验+格式异常提示
      const rawData = d.data !== undefined ? d.data : d
      const newData = Array.isArray(rawData) ? rawData : []
      addDebug('load 返回', {status: r.status, 条数: newData.length, ids: newData.map(x=>x.id).slice(0,10)})
      setRules(newData)
      // 同步批量面板状态判断数据(id → is_active)
      const _m = {}
      ;(newData || []).forEach(r => { if (r && r.id) _m[r.id] = r.is_active ? 1 : 0 })
      useAppStore.setState({ batchStateMap: _m })
      setRulesErr(Array.isArray(rawData) ? '' : (r.status !== 200 ? '加载失败，可能是网络异常或服务暂不可用' : '返回数据格式异常'))
      addDebug('setRules 完成', {条数: newData.length})
    } catch(e) { addDebug('load 异常', {error: e.message}); setRulesErr('加载失败，可能是网络异常或服务暂不可用') } 
  }
  useEffect(() => {
    const h = () => load(globalChannel)
    window.addEventListener('rules-changed', h)
    return () => window.removeEventListener('rules-changed', h)
  }, [globalChannel])
  const loadCfg = async (mode, ch) => { try { const m=mode||cfg.replenishment_mode||'bbcc'; const c=ch||globalChannel; const r=await api.get('/api/replenishment-config?mode='+m+'&channel='+c);if(r.data&&Object.keys(r.data).length>0)setCfg(p=>({...p, ...r.data, replenishment_mode:m}));else if(c!=='jd'){const fallback=await api.get('/api/replenishment-config?mode='+m+'&channel=jd');if(fallback.data)setCfg(p=>({...p,...fallback.data,replenishment_mode:m}))}setCfg(p => ({...p, replenishment_mode: m}));return r.data||{} } catch(e) { return {} } }
  const loadSeasons = async (mode, ch) => { try { const m=mode||cfg.replenishment_mode||'bbcc'; const c=ch||globalChannel; const sk='season_config_'+m; if(cfg[sk]){try{const sd=JSON.parse(cfg[sk]||'[]');setSeasons(Array.isArray(sd)?sd:[]);return}catch{}} const r=await api.get('/api/replenishment-config/seasons?mode='+m+'&channel='+c); setSeasons(r.data||[]) } catch(e) {} }
  const loadAll = async (ch) => { setLoading(true); const c=ch||globalChannel; const savedMode=(()=>{try{return localStorage.getItem('c_replen_mode_'+c)}catch{return null}})(); const m=c!=='jd'?'traditional':(savedMode||'bbcc'); clearCache(); clearInflight(); try{const _s=localStorage.getItem('c_supplier_'+c);if(_s)setSelectedSupplier(_s)}catch{} await Promise.all([ (async()=>{try{await load(c)}catch(e){}})(), (async()=>{try{const flat=await api.get('/api/replenishment-config?channel='+c);if(flat.data){setCfg(p=>{const mP='mode_'+m+'_';const mD={};Object.entries(flat.data).forEach(([k,v])=>{if(k.startsWith(mP))mD[k.slice(mP.length)]=v});return{...p,...flat.data,...mD,replenishment_mode:m}});const sK='season_config_'+m;try{const sd=JSON.parse(flat.data[sK]||'[]');setSeasons(Array.isArray(sd)?sd:[])}catch{}}}catch(e){}})(), (async()=>{try{const sr=await api.get('/api/suppliers?channel='+c);if(sr.data)setSuppliers(sr.data.map(x=>x.supplier_code).filter(Boolean))}catch(e){}})() ]); setLoading(false) }
  useEffect(() => { loadAll() }, [globalChannel])
  // tab/模式切换时加载配置，补货参数页加骨架过渡
  useEffect(() => {
    const seq = ++reqSeq.current
    if (tab === 'params') {
      setLoading(true)
      Promise.all([loadCfg(hammerRulesMode), loadSeasons(hammerRulesMode)])
        .catch(() => {})
        .finally(() => { if (reqSeq.current === seq) setLoading(false) })
    } else if (tab === 'purchase') {
      loadCfg(hammerRulesMode)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, hammerRulesMode])
  // 锤子菜单t("rules.new")触发
  useEffect(() => {
    if (hammerRuleNewVersion > 0) resetForm()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hammerRuleNewVersion])

  const resetForm = () => { setEditing({}); setF(defaultF); setCond({left:'inv.available_qty', op:'<', right:'inv.safety_qty', rightType:'field', pctValue:100, warehouse:''}) }
  const cancelEdit = () => { setEditing(null); setF(defaultF); setCond({left:'inv.available_qty', op:'<', right:'inv.safety_qty', rightType:'field', pctValue:100, warehouse:''}); useAppStore.setState({ hammerRuleNewVersion: 0 }) }

  const save = async () => {
    setSaveLoading(true)
    addDebug('save 开始', {isNew: !editing || !editing.id})
    try {
      let rv = cond.right
      if (cond.rightType === 'number') rv = parseFloat(cond.right) || 0
      else if (cond.rightType === 'field') rv = cond.right
      else if (cond.rightType === 'pct') rv = `max(1,${cond.right}*${(cond.pctValue||100)/100})`
      const cj = JSON.stringify({left:cond.left, op:cond.op, right:rv, rightType:cond.rightType, warehouse:cond.warehouse})
      const isNew = !editing || !editing.id
      const url = isNew ? API+'/api/rules' : API+'/api/rules/'+editing.id
      const r = await fetch(url, {method: isNew?'POST':'PUT', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})(), 'Content-Type':'application/json'}, body:JSON.stringify({...f, mode: f.mode||'', channel:globalChannel, condition_json:cj})})
      if (!r.ok) { const err = await r.json().catch(()=>({})); throw new Error(err.detail || 'HTTP '+r.status) }
      toast.success(isNew ? '规则已创建' : '规则已更新')
      addDebug('save 成功', {isNew, id: editing?.id})
      clearCache(); cancelEdit(); await load(globalChannel); window.dispatchEvent(new Event('rules-changed'))
      // 本地即时更新 mode 显示，不等 API 返回（避免旧 state 渲染导致 mode 显示"全部"）
      if (!isNew) setRules(prev => prev.map(rl => rl.id === editing.id ? {...rl, mode: f.mode||''} : rl))
    } catch(e) { toast.error('保存失败: '+e.message) }
    setSaveLoading(false)
  }
  const del = async (id) => {
    addDebug('del 开始', {id})
    await fetch(API+'/api/rules/'+id, {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()},method:'DELETE'})
    addDebug('del 删除请求完成')
    clearCache()
    await load(globalChannel); window.dispatchEvent(new Event('rules-changed'))
    addDebug('del load 完成')
    var timer = setTimeout(async function() {
      await fetch(API+'/api/rules/'+id+'/permanent-delete', {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()},method:'POST'})
    }, 5000)
    toast.add({type:'success', title:t("common.delete"), duration:5000, action: {label: t("undo.undo"), handler: async function() {
      clearTimeout(timer)
      await fetch(API+'/api/rules/'+id+'/restore', {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()},method:'POST'})
      load(globalChannel)
    }}})
  }

  const isBBCC = (cfg.replenishment_mode||'bbcc')==='bbcc'
  const filteredRules = hammerSearch ? rules.filter(function(r) { return (r.name||'').toLowerCase().includes(hammerSearch.toLowerCase()) }) : rules
  useEffect(()=>{ setProdBatchFilterLen(filteredRules.length) },[filteredRules.length])
  useEffect(()=>{ if(prodBatchAllReq>0){ const all=filteredRules.map(r=>r.id); setProdBatchSel(selIds.length===all.length&&all.length>0?[]:all) } },[prodBatchAllReq])
  const cParams = isBBCC ? [{k:'b_to_c_days',l:'B→C调拨(天)',h:'京东B仓→C仓调拨时效'},{k:'c_safety_days',l:'C仓缓冲(天)',h:'C仓安全储备'}] : []
  const bParams = isBBCC ? [{k:'ship_to_b_days',l:'自有仓→B仓时效(天)'},{k:'safety_multiplier',l:'安全库存天数'},{k:'turnover_warning_15',l:'仓储费阈值(天)'},{k:'turnover_warning_90',l:'周转考核红线(天)'}] : []
  const paramFields = isBBCC ? [] : [{k:'lead_time_days',l:'前置期(天)'},{k:'safety_multiplier',l:'安全库存天数'},{k:'turnover_warning_90',l:'周转考核红线(天)'}]
  const purchaseFields = [{k:'purchase_lead_days',l:'采购前置(天)'},{k:'purchase_safety_days',l:'采购安全库存(天)'},{k:'moq',l:'MOQ最小起订(件)'},{k:'max_turnover_days',l:'目标周转(天)'}]

  if (loading) return <div className='card'><div className='section-title'><div className="skeleton" style={{width:120,height:20}}/></div><div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:12}}>{[1,2,3,4,5,6].map(i=><div key={i}><div className="skeleton" style={{width:64,height:12,marginBottom:6}}/><div className="skeleton" style={{width:'100%',height:36}}/></div>)}</div><div style={{marginTop:16}}><div className="skeleton" style={{width:80,height:36,borderRadius:99}}/></div></div>

  return <>
    {/* ── 调试面板（localStorage 设 c_debug_rules=1 启用） ── */}
    {(() => { try { return localStorage.getItem('c_debug_rules') === '1' } catch { return false } })() && (
      <div style={{marginBottom:12,padding:10,borderRadius:12,border:'1px solid var(--warning)',background:'rgba(245,158,11,0.08)',fontSize:11,fontFamily:'monospace'}}>
        <div style={{fontWeight:700,marginBottom:4}}>🔍 规则页调试追踪（关闭: localStorage 设 c_debug_rules=0）</div>
        <div style={{color:'var(--text-secondary)',marginBottom:4}}>当前 rules state: <b>{rules.length}</b> 条 | filteredRules: <b>{filteredRules.length}</b> 条 | 渠道: <b>{globalChannel}</b> | tab: <b>{tab}</b></div>
        <button onClick={() => setDebugLog([])} style={{marginRight:6,padding:'2px 8px',borderRadius:8,border:'1px solid var(--border)',background:'transparent',fontSize:11}}>清空日志</button>
        {debugLog.length === 0 ? <div style={{color:'var(--muted2)'}}>暂无操作日志</div> : debugLog.map((l, i) => (
          <div key={i} style={{borderTop:'1px dashed var(--border)',padding:'2px 0'}}>
            <span style={{color:'var(--muted2)'}}>[{l.t}]</span> {l.msg}
            {l.data && <span style={{color:'var(--text-secondary)'}}> {JSON.stringify(l.data)}</span>}
          </div>
        ))}
      </div>
    )}

    <div className='card'>
    <div className='section-title' style={{display:'flex',flexWrap:'wrap',gap:6}}>
    </div>

    {/* ── 规则列表 ── */}
    {tab==='rules' && <>
      {editing !== null && <div style={{background:'var(--bg)',border:'1px solid var(--border)',borderRadius:32,padding:16,marginBottom:16}}>
        <div style={{fontWeight:600,marginBottom:12}}>{editing.id?t("rules.edit"):t("rules.new_btn") + '规则'}</div>

        {/* 名称 + {t("rules.severity")} + 补货模式 */}
        <div style={{display:'flex',gap:12,alignItems:'flex-end',marginBottom:14,flexWrap:'wrap'}}>
          <label style={{flex:1,minWidth:140,fontSize:12}}>{t("rules.name")}<input value={f.name} onChange={e=>setF({...f,name:e.target.value})} style={IS} placeholder='例：低库存预警'/></label>
          <label style={{fontSize:12}}>级别
            <div style={{display:'flex',gap:4,marginTop:4}}>
              {[{v:'warning',t:'警告',c:'var(--warning)'},{v:'error',t:t("rules.severity_error"),c:'var(--danger)'},{v:'info',t:'提示',c:'var(--primary)'}].map(({v,t,c}) =>
                <span key={v} onClick={()=>setF({...f,severity:v})} className="clickable" style={{padding:'5px 12px',borderRadius:32,fontSize:13,fontWeight:600,cursor:'pointer',background:f.severity===v?c:'transparent',color:f.severity===v?'#fff':'var(--muted)',border:'1px solid',borderColor:f.severity===v?c:'var(--border)',display:'flex',alignItems:'center',gap:3}}>{t}</span>
              )}
            </div>
          </label>
          <label style={{fontSize:12}}>补货模式
            <select value={f.mode||''} onChange={e=>setF({...f,mode:e.target.value})} style={{...IS,fontSize:13,marginTop:4,width:'100%',minWidth:80}}>{MODES.filter(m => m.v !== 'bbcc' || globalChannel === 'jd').map(m=><option key={m.v} value={m.v}>{m.l}</option>)}</select>
          </label>
        </div>

        {/* 触发条件 — 一句话 */}
        <div style={{background:'var(--card)',border:'1px solid var(--border)',borderRadius:32,padding:14,marginBottom:14}}>
          <div style={{fontWeight:600,fontSize:13,marginBottom:10,display:'flex',alignItems:'center',gap:4}}><IconScale size={14} /> 触发条件</div>
          <div style={{display:'flex',gap:6,alignItems:'center',flexWrap:'wrap'}}>
            <span className="text-14 font-500">当</span>
            <select value={cond.warehouse} onChange={e=>setCond(p=>({...p,warehouse:e.target.value}))} style={{...IS,flex:1,minWidth:60,fontSize:13}}>{WHS.filter(w => w.v !== 'platform_b' || globalChannel === 'jd').map(w=><option key={w.v} value={w.v}>{w.l}</option>)}</select>
            <select value={cond.left} onChange={e=>setCond(p=>({...p,left:e.target.value}))} style={{...IS,flex:2,minWidth:120,fontSize:14}}>{LF.map(f=><option key={f.v} value={f.v}>{f.l}</option>)}</select>
            <select value={cond.op} onChange={e=>setCond(p=>({...p,op:e.target.value}))} style={{...IS,width:70,fontSize:14,textAlign:'center'}}>{OPS.map(o=><option key={o.v} value={o.v}>{o.l}</option>)}</select>
            <span style={{display:'flex',alignItems:'center',gap:4,flex:2,minWidth:140}}>
              <input type='number' value={cond.pctValue||0} onChange={e=>setCond(p=>({...p,pctValue:parseInt(e.target.value)||0,rightType:'pct',right:'inv.safety_qty'}))} min={1} max={200} style={{...IS,width:'auto',flex:1,fontSize:14,textAlign:'center'}}/>
              <span style={{fontSize:13,color:'var(--muted2)',fontWeight:500,whiteSpace:'nowrap'}}>
                {cond.left==='inv.days_since_last' ? '天' : cond.left==='inv.available_qty' ? '%（安全库存百分比）' : cond.left==='order.quantity' ? '件' : cond.left==='order.total_amount' ? '元' : '%'}
              </span>
            </span>
          </div>
          <div className='small' style={{marginTop:8,padding:'6px 10px',background:'var(--bg)',borderRadius:32,fontSize:13,color:'var(--primary)'}}>
            <IconClipboard size={12} style={{display:'inline',verticalAlign:'middle',marginRight:4}} />
            当 <b>{WHS.find(w=>w.v===cond.warehouse)?.l||'全部'}</b> <b>{fieldLbl(cond.left)}</b> {opLbl(cond.op)} <b>{cond.pctValue||0}{cond.left==='inv.days_since_last'?'天':cond.left==='inv.available_qty'?'%':'件'}</b>
            {cond.left==='inv.available_qty' ? <span style={{color:'var(--muted2)',fontSize:11}}>（安全库存的 {cond.pctValue||0}%）</span> : ''}
            时
          </div>
        </div>

        {/* 告警内容 */}
        <div style={{background:'var(--card)',border:'1px solid var(--border)',borderRadius:32,padding:14}}>
          <div style={{fontWeight:600,fontSize:13,marginBottom:10,display:'flex',alignItems:'center',gap:4}}><IconAlert size={14} /> 告警内容</div>
          <div style={{display:'flex',gap:12,flexWrap:'wrap',marginBottom:8}}>
            <label style={{flex:1,minWidth:180,fontSize:12}}>
              告警标题
              <div style={{marginTop:4,padding:'8px 12px',background:'var(--bg)',borderRadius:32,fontSize:14,minHeight:36,border:'1px solid var(--border)',display:'flex',alignItems:'center',flexWrap:'wrap',gap:3}}>
                {renderTmpl(f.alert_title) || <span className="muted" style={{fontSize:12}}>输入文字或点击下方按钮插入变量</span>}
              </div>
              <input value={f.alert_title} onChange={e=>setF({...f,alert_title:e.target.value})} style={{...IS,fontSize:13,marginTop:4}} placeholder='输入文字，点击下方按钮插入变量'/>
              <div style={{display:'flex',gap:4,marginTop:4,flexWrap:'wrap'}}>
                {[{v:'{product_name}',l:'商品名'},{v:'{sku}',l:'SKU'}].map(t=>
                  <span key={t.v} onClick={()=>setF({...f,alert_title:f.alert_title+t.v})} className="clickable" style={{padding:'4px 12px',borderRadius:99,fontSize:12,background:'rgba(29,78,216,0.1)',color:'var(--primary)',cursor:'pointer',border:'1px solid rgba(29,78,216,0.2)',display:'inline-flex',alignItems:'center',gap:3}}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>{t.l}</span>
                )}
              </div>
            </label>
            <label style={{flex:1,minWidth:180,fontSize:12}}>
              告警描述
              <div style={{marginTop:4,padding:'8px 12px',background:'var(--bg)',borderRadius:32,fontSize:14,minHeight:36,border:'1px solid var(--border)',display:'flex',alignItems:'center',flexWrap:'wrap',gap:3}}>
                {renderTmpl(f.alert_desc) || <span className="muted" style={{fontSize:12}}>输入文字或点击下方按钮插入变量</span>}
              </div>
              <input value={f.alert_desc} onChange={e=>setF({...f,alert_desc:e.target.value})} style={{...IS,fontSize:13,marginTop:4}} placeholder='输入文字，点击下方按钮插入变量'/>
              <div style={{display:'flex',gap:4,marginTop:4,flexWrap:'wrap'}}>
                {[{v:'{avail}',l:'可用量'},{v:'{safety}',l:'安全线'},{v:'{sku}',l:'SKU'}].map(t=>
                  <span key={t.v} onClick={()=>setF({...f,alert_desc:f.alert_desc+t.v})} className="clickable" style={{padding:'4px 12px',borderRadius:99,fontSize:12,background:'rgba(29,78,216,0.1)',color:'var(--primary)',cursor:'pointer',border:'1px solid rgba(29,78,216,0.2)',display:'inline-flex',alignItems:'center',gap:3}}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>{t.l}</span>
                )}
              </div>
            </label>
          </div>
        </div>

        <div style={{marginTop:16,display:'flex',gap:10}}>
          <button onClick={save} disabled={saveLoading} className="btn btn-primary" style={{flex:1,display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center',minHeight:40}}>{saveLoading ? <><IconLoading size={14} /> 保存中...</> : <><IconSave size={14} /> {t("common.save")}</>}</button>
          <button onClick={cancelEdit} className="btn btn-ghost" style={{flex:1,background:'var(--warning)',color:'#fff',minHeight:40}}>{t("common.cancel")}</button>
        </div>
      </div>}

      

      {filteredRules.map(rule => {
        const condInfo = pc(rule.condition_json||'{}')
        const whLbl = WHS.find(w=>w.v===condInfo.warehouse)?.l||'全部'
        const modeLbl = MODES.find(m=>m.v===(rule.mode||''))?.l||'全部'
        const condText = `当 ${whLbl} ${fieldLbl(condInfo.left)} ${opLbl(condInfo.op)} ${condInfo.rightType==='pct'?fieldLbl(condInfo.right)+'的'+condInfo.pctValue+'%':(condInfo.rightType==='field'?fieldLbl(condInfo.right):condInfo.right)}`
        return <div key={rule.id} onClick={()=>{if(!prodBatch){const c=pc(rule.condition_json||'{}');setEditing(rule);setF({name:rule.name,event:rule.event,alert_type:rule.alert_type||'low_stock',alert_title:rule.alert_title||'',alert_desc:rule.alert_desc||'',severity:rule.severity||'warning',mode:rule.mode||'',condition_json:rule.condition_json||'{}'});setCond(c)}}} style={{cursor:prodBatch?'default':'pointer',padding:'14px 16px',border:'1px solid var(--border)',borderRadius:32,marginBottom:8,background:prodBatch&&selIds.includes(rule.id)?'rgba(29,78,216,0.08)':'transparent'}}>
        {prodBatch && <span onClick={(e)=>{e.stopPropagation();const ids=selIds;setSelIds(ids.includes(rule.id)?ids.filter(i=>i!==rule.id):[...ids,rule.id])}} className="clickable" style={{display:'inline-flex',alignItems:'center',gap:8,marginBottom:8}}><span style={{width:18,height:18,borderRadius:6,border:'1.5px solid',borderColor:selIds.includes(rule.id)?'var(--primary)':'var(--border)',background:selIds.includes(rule.id)?'var(--primary)':'transparent',display:'inline-flex',alignItems:'center',justifyContent:'center',color:'#fff',fontSize:11}}>{selIds.includes(rule.id)?'✓':''}</span><span style={{fontSize:12,color:'var(--muted2)'}}>选择</span></span>}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:10}}>
        <div style={{flex:1,minWidth:0}}>
          <div style={{fontWeight:600,fontSize:15,display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
            {rule.name}
            <span className={'pill '+(rule.is_active?'success':'warning')} style={{fontSize:10,padding:'2px 8px',minHeight:'auto',lineHeight:'18px'}}>{rule.is_active?'启用':'停用'}</span>
            <span className={'pill '+sevCls(rule.severity)} style={{fontSize:10,padding:'2px 8px',minHeight:'auto',lineHeight:'18px'}}>{sevLbl(rule.severity)}</span>
            {rule.mode && <span style={{fontSize:10,color:'var(--muted2)',background:'var(--bg)',padding:'2px 8px',borderRadius:99}}>{modeLbl}</span>}
          </div>
          <div style={{marginTop:6,padding:'8px 12px',background:'var(--bg)',borderRadius:32,fontSize:13,color:'var(--primary)',display:'block'}}>
            <IconScale size={12} style={{display:'inline',verticalAlign:'middle',marginRight:4}} /> {condText}
          </div>
          <div style={{fontSize:12,color:'var(--muted)',marginTop:4,display:'flex',flexWrap:'wrap',gap:3,alignItems:'center'}}>
            {renderTmpl(rule.alert_title) || <span className="small muted">无标题</span>}
            {rule.alert_desc ? <><span style={{color:'var(--muted2)',margin:'0 3px'}}>·</span>{renderTmpl(rule.alert_desc)}</> : ''}
          </div>
        </div>
        <div style={{display:'flex',gap:8,flexShrink:0,alignItems:'flex-start'}}>
          <button onClick={()=>{setTestRule(rule);setTestInv({available_qty:0,safety_qty:0,in_transit_qty:0,warehouse_type:condInfo.warehouse||'',days_since_last:0,order_quantity:0});setTestResult(null)}} className="clickable" style={{fontSize:13,padding:'6px 14px',minHeight:36,borderRadius:99,border:'1px solid var(--border)',background:'var(--card)',color:'var(--primary)',cursor:'pointer',fontWeight:600}}>测试</button>

        </div>
        </div>
      </div>})}
      {filteredRules.length===0 && (rulesErr ? <ErrorRetry error={rulesErr} onRetry={()=>load(globalChannel)} /> : <div className='small muted' style={{textAlign:'center',padding:40}}>{rules.length===0 ? t("rules.empty") : (hammerSearch ? '没有匹配"'+hammerSearch+'"的规则' : t("rules.empty"))}</div>)}
    </>}

    {/* ── 补货参数 ── */}
    {tab==='params' && <div>
      {isBBCC ? <>
        <div className='section-title' style={{fontSize:14,marginBottom:10,display:'flex',alignItems:'center',gap:4}}><IconPackage size={14} /> C 仓</div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginBottom:20}}>{cParams.map(({k,l,h})=><label key={k} style={{fontSize:13}}>{l}<input value={cfg[k]||''} onChange={e=>setCfg(p=>({...p,[k]:e.target.value}))} style={IS}/>{h && <div className='small muted' style={{fontSize:11,marginTop:2}}>{h}</div>}</label>)}</div>
        <div className='section-title' style={{fontSize:14,marginBottom:10,display:'flex',alignItems:'center',gap:4}}><IconFactory size={14} /> B 仓</div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:14,marginBottom:20}}>{bParams.map(({k,l})=><label key={k} style={{fontSize:13}}>{l}<input value={cfg[k]||''} onChange={e=>setCfg(p=>({...p,[k]:e.target.value}))} style={IS}/></label>)}</div>
      </> : <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:14,marginBottom:20}}>{paramFields.map(({k,l})=><label key={k} style={{fontSize:13}}>{l}<input value={cfg[k]||''} onChange={e=>setCfg(p=>({...p,[k]:e.target.value}))} style={IS}/></label>)}</div>}
      <button disabled={saving} onClick={async()=>{setSaving(true);const m=cfg.replenishment_mode||'bbcc';const ch=globalChannel;try{const toSave={};[...cParams,...bParams,...paramFields].forEach(f=>{if(cfg[f.k]!==undefined)toSave[f.k]=cfg[f.k]});await api.put('/api/replenishment-config?mode='+m+'&channel='+ch,toSave);setCfg(p=>({...p,...toSave}));toast.success('已保存')}catch(e){saveErr(e)}setSaving(false)}} className="btn btn-primary" style={{width:'100%',display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center',minHeight:42}}>{saving?<><IconLoading size={14} /> 保存中...</>:<><IconSave size={14} /> 保存</>}</button>
    </div>}

    {/* ── 采购参数 ── */}
    {tab === 'purchase' && <div>
      <div style={{fontSize:12,color:'var(--muted2)',marginBottom:8}}>供应商起订（可选）</div>
      <div className="hammer-btn-row" style={{marginBottom:12}}>
        <select value={selectedSupplier} onChange={e=>{setSelectedSupplier(e.target.value);try{localStorage.setItem('c_supplier_'+globalChannel,e.target.value)}catch{}}}
          className="hammer-select">
          <option value="">通用（所有供应商）</option>
          {suppliers.map(s=><option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <div className="hammer-params-grid">
        {purchaseFields.map(({k,l})=>{
          const isSupKey = k === 'moq' || k === 'purchase_lead_days' || k === 'purchase_safety_days'
          const actualKey = selectedSupplier && isSupKey ? `${k}_${selectedSupplier}` : k
          return <label key={actualKey} style={{fontSize:13}}>
            {l}{selectedSupplier && isSupKey && <span className='small muted'>（{selectedSupplier}）</span>}
            <input value={cfg[actualKey]||''} onChange={e=>setCfg(p=>({...p,[actualKey]:e.target.value}))} className="hammer-input"/>
          </label>
        })}
      </div>
      <button disabled={saving} onClick={async()=>{setSaving(true);const ch=globalChannel;try{const toSave={};purchaseFields.forEach(f=>{const isSupKey = f.k === 'moq' || f.k === 'purchase_lead_days' || f.k === 'purchase_safety_days'; const k=selectedSupplier&&isSupKey?`${f.k}_${selectedSupplier}`:f.k;if(cfg[k]!==undefined)toSave[k]=cfg[k]});await api.put('/api/replenishment-config?channel='+ch,toSave);setCfg(p=>({...p,...toSave}));toast.success('已保存')}catch(e){saveErr(e)}setSaving(false)}} className="btn btn-primary" style={{width:'100%',display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center',minHeight:42}}>{saving?<><IconLoading size={14} /> 保存中...</>:<><IconSave size={14} /> 保存</>}</button>
    </div>}

    {/* ── 滞销参数（自定义品类条目，仿活动系数） ── */}
    {tab === 'slow' && <div>
      <div className="small muted" style={{marginBottom:10,fontSize:12}}>按品类分组自定义滞销判定：每个品类设「滞销线(天)」和「临期线(月)」，品类名单匹配商品的分类字段</div>
      {slowCats.map((s,i)=><div key={s.key||i} style={{padding:'10px 14px',border:'1px solid var(--border)',borderRadius:32,marginBottom:8}}>
        <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:8}}>
          <input value={s.name} onChange={e=>setSlowCats(p=>p.map((x,j)=>j===i?{...x,name:e.target.value}:x))} placeholder='品类名(如 食品)' style={{flex:1,minWidth:80,fontSize:16,padding:'6px 10px',border:'1px solid var(--border)',borderRadius:32,outline:'none'}}/>
          <label style={{fontSize:12,display:'flex',alignItems:'center',gap:4,cursor:'pointer',flexShrink:0}} onClick={()=>setSlowCats(p=>p.map((x,j)=>j===i?{...x,enabled:!(x.enabled!==false)}:x))}>
            <svg width="20" height="20" viewBox="0 0 18 18" style={{flexShrink:0}}>
              {s.enabled!==false ? (
                <><circle cx="9" cy="9" r="8" fill="var(--primary)" /><path d="M5.5 9.5l2 2 3.5-3.5" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></>
              ) : (
                <circle cx="9" cy="9" r="8" fill="none" stroke="var(--border)" strokeWidth="1.5" />
              )}
            </svg>
          </label>
          <span onClick={()=>setSlowCats(p=>p.filter((_,j)=>j!==i))} className="clickable" style={{color:'var(--danger)',fontSize:16,cursor:'pointer',flexShrink:0}}>×</span>
        </div>
        <div style={{display:'flex',gap:8,marginBottom:8,flexWrap:'wrap'}}>
          <label style={{fontSize:12,flex:1,minWidth:80}}>滞销线(天)
            <input type='number' value={s.slow_days} onChange={e=>setSlowCats(p=>p.map((x,j)=>j===i?{...x,slow_days:parseInt(e.target.value)||30}:x))} style={{...IS,marginTop:2}}/>
          </label>
          <label style={{fontSize:12,flex:1,minWidth:80}}>观察线(天) <span style={{color:'var(--muted2)'}}>留空自动</span>
            <input type='number' value={s.observe_days ?? ''} placeholder={String(Math.max(Math.floor(parseInt(s.slow_days||30) / 2), 15))} onChange={e=>setSlowCats(p=>p.map((x,j)=>j===i?{...x,observe_days: e.target.value===''?'':parseInt(e.target.value)||''}:x))} style={{...IS,marginTop:2}}/>
          </label>
          <label style={{fontSize:12,flex:1,minWidth:80}}>临期线(月)
            <input type='number' value={s.shelf_months} onChange={e=>setSlowCats(p=>p.map((x,j)=>j===i?{...x,shelf_months:parseInt(e.target.value)||3}:x))} style={{...IS,marginTop:2}}/>
          </label>
        </div>
        <label style={{fontSize:12,display:'block'}}>品类名单（逗号分隔，匹配商品"分类"字段）
          <input value={s.cats||''} onChange={e=>setSlowCats(p=>p.map((x,j)=>j===i?{...x,cats:e.target.value}:x))} placeholder='酱油,薯片,糖果...' style={{...IS,marginTop:2}}/>
        </label>
      </div>)}
      <div style={{marginTop:16}}>
        <label style={{fontSize:13,display:'flex',alignItems:'center',gap:10}}>物流在途(天)
          <input value={transitDays} onChange={e=>setTransitDays(e.target.value)} style={{...IS,width:80,fontSize:14,textAlign:'center'}}/>
          <span className="small muted" style={{fontSize:11}}>库存出库到客户/入仓的运输天数，默认 3（用于效期预警：已消耗 + 在途 &gt; 1/3 标临近）</span>
        </label>
        <label style={{fontSize:13,display:'flex',alignItems:'center',gap:10,marginTop:8}}>资金占用线(¥)
          <input value={fundThreshold} onChange={e=>setFundThreshold(e.target.value)} style={{...IS,width:80,fontSize:14,textAlign:'center'}}/>
          <span className="small muted" style={{fontSize:11}}>滞销品资金占用(库存×单价)超此线 → 升级「处置」等级(默认 10000)</span>
        </label>
      </div>
      <div style={{marginTop:12}}>
        <button onClick={()=>setSlowCats(p=>[...p,{key:'new'+Date.now(),name:'新品类',slow_days:30,observe_days:'',shelf_months:3,cats:'',enabled:true}])} className="btn btn-ghost clickable" style={{fontSize:13,padding:'8px 16px',width:'100%',minHeight:40}}>+ 添加品类</button>
      </div>
      <div style={{marginTop:12}}>
        <button disabled={saving} onClick={async()=>{setSaving(true);const ch=globalChannel;try{const r=await api.put('/api/replenishment-config/slow-cats?channel='+ch,{items:slowCats});await api.put('/api/replenishment-config?channel='+ch,{transit_days:transitDays||'',slow_fund_threshold:fundThreshold||''});toast.success('已保存')}catch(e){saveErr(e)}setSaving(false)}} className="btn btn-primary" style={{width:'100%',display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center',minHeight:42}}>{saving?<><IconLoading size={14} /> 保存中...</>:<><IconSave size={14} /> 保存</>}</button>
        </div>
      </div>}
    {tab === 'params' && <><div className='section-title' style={{marginTop:16,marginBottom:8,display:'flex',alignItems:'center',gap:4}}><IconTag size={14} /> 活动系数</div>
      {seasons.map((s,i)=><div key={s.key||i} style={{display:'flex',alignItems:'center',gap:10,padding:'10px 14px',border:'1px solid var(--border)',borderRadius:32,marginBottom:8}}>
        <input value={s.name} onChange={e=>setSeasons(p=>p.map((x,j)=>j===i?{...x,name:e.target.value}:x))} placeholder='618大促' style={{flex:1,minWidth:80,fontSize:16,padding:'6px 10px',border:'1px solid var(--border)',borderRadius:32,outline:'none'}}/>
        <span className='small muted'>×</span>
        <input type='number' value={s.factor} onChange={e=>setSeasons(p=>p.map((x,j)=>j===i?{...x,factor:parseFloat(e.target.value)||1}:x))} step='0.1' min='1' max='3' style={{width:70,fontSize:16,padding:'6px 10px',border:'1px solid var(--border)',borderRadius:32,outline:'none'}}/>
        <span className='small muted'>倍</span>
        <label style={{fontSize:12,display:'flex',alignItems:'center',gap:4,cursor:'pointer',flexShrink:0}} onClick={()=>setSeasons(p=>p.map((x,j)=>j===i?{...x,enabled:!(x.enabled!==false)}:x))}>
          <svg width="20" height="20" viewBox="0 0 18 18" style={{flexShrink:0}}>
            {s.enabled!==false ? (
              <>
                <circle cx="9" cy="9" r="8" fill="var(--primary)" />
                <path d="M5.5 9.5l2 2 3.5-3.5" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </>
            ) : (
              <circle cx="9" cy="9" r="8" fill="none" stroke="var(--border)" strokeWidth="1.5" />
            )}
          </svg>
          启用
        </label>
        <button onClick={()=>setSeasons(p=>p.filter((_,j)=>j!==i))} className="clickable" style={{fontSize:13,padding:'6px 14px',minHeight:36,borderRadius:99,border:'none',background:'var(--danger)',color:'#fff',cursor:'pointer',fontWeight:600,flexShrink:0}}>删除</button>
      </div>)}
      <button onClick={()=>setSeasons(p=>[...p,{key:'new',name:'新活动',factor:1.2,enabled:true}])} className="btn btn-ghost clickable" style={{fontSize:13,padding:'8px 16px',width:'100%',minHeight:40}}>+ 添加活动</button>
      <div style={{marginTop:12}}>
        <button disabled={seasonsSaving} onClick={async()=>{setSeasonsSaving(true);const m=cfg.replenishment_mode||'bbcc';const ch=globalChannel;try{await api.put('/api/replenishment-config/seasons?mode='+m+'&channel='+ch,{items:seasons});await loadCfg(m,ch);toast.success('已保存')}catch(e){saveErr(e)}setSeasonsSaving(false)}} className="btn btn-primary" style={{width:'100%',display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center',minHeight:42,opacity:seasonsSaving?0.6:1}}>{seasonsSaving?<><IconLoading size={14} /> 保存中...</>:<><IconSave size={14} /> 保存</>}</button>
      </div>
    </>}

    {/* ── 规则测试弹窗（可视化调试：输入模拟数据判断是否触发） ── */}
    {testRule && <div style={{position:'fixed',inset:0,zIndex:4000}}>
      <div onClick={()=>setTestRule(null)} style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.3)'}} />
      <div className="material-regular" style={{position:'fixed',left:14,right:14,bottom:'calc(env(safe-area-inset-bottom) + 14px)',maxWidth:560,margin:'0 auto',borderRadius:32,padding:'18px 16px calc(16px + env(safe-area-inset-bottom))',boxShadow:'var(--shadow-sheet)',maxHeight:'75vh',overflowY:'auto'}}>
        <div style={{fontWeight:700,fontSize:16,marginBottom:4,textAlign:'center'}}>规则测试</div>
        <div style={{textAlign:'center',fontSize:12,color:'var(--muted2)',marginBottom:14}}>{testRule.name}</div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
          <label style={{fontSize:12}}>可用量<input type="number" value={testInv.available_qty} onChange={e=>setTestInv(p=>({...p,available_qty:e.target.value}))} style={IS}/></label>
          <label style={{fontSize:12}}>安全线<input type="number" value={testInv.safety_qty} onChange={e=>setTestInv(p=>({...p,safety_qty:e.target.value}))} style={IS}/></label>
          <label style={{fontSize:12}}>在途<input type="number" value={testInv.in_transit_qty} onChange={e=>setTestInv(p=>({...p,in_transit_qty:e.target.value}))} style={IS}/></label>
          <label style={{fontSize:12}}>滞销天数<input type="number" value={testInv.days_since_last} onChange={e=>setTestInv(p=>({...p,days_since_last:e.target.value}))} style={IS}/></label>
          <label style={{fontSize:12}}>订单数量<input type="number" value={testInv.order_quantity} onChange={e=>setTestInv(p=>({...p,order_quantity:e.target.value}))} style={IS}/></label>
          <label style={{fontSize:12}}>仓库主体
            <select value={testInv.warehouse_type} onChange={e=>setTestInv(p=>({...p,warehouse_type:e.target.value}))} style={IS}>
              <option value="">全部</option>
              <option value="platform">C仓</option>
              {globalChannel==='jd' && <option value="platform_b">B仓</option>}
              <option value="own">自有仓</option>
            </select>
          </label>
        </div>
        <div style={{display:'flex',gap:10,marginTop:14}}>
          <button onClick={runTest} disabled={testLoading} className="btn btn-primary" style={{flex:1,display:'inline-flex',alignItems:'center',gap:4,justifyContent:'center',minHeight:42}}>{testLoading ? <><IconLoading size={14}/> 测试中...</> : '运行测试'}</button>
          <button onClick={()=>setTestRule(null)} className="btn btn-ghost" style={{flex:1,minHeight:42}}>关闭</button>
        </div>
        {testResult && (
          <div style={{marginTop:14,padding:'12px 14px',borderRadius:24,background: testResult.triggered ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.08)',border:'1px solid',borderColor: testResult.triggered ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.3)'}}>
            <div style={{fontWeight:700,fontSize:15,color: testResult.triggered ? 'var(--success)' : 'var(--danger)',marginBottom:6}}>
              {testResult.triggered ? '✓ 触发告警' : '✗ 未触发'}
            </div>
            {testResult.triggered && <div style={{fontSize:12,color:'var(--text)'}}>
              <div><b>{testResult.alert_title}</b></div>
              <div className="small muted">{testResult.alert_desc}</div>
            </div>}
            {testResult.detail && <div style={{fontSize:11,color:'var(--muted2)',marginTop:8,borderTop:'1px dashed var(--border)',paddingTop:8}}>
              条件: 当 <b>{testResult.detail.warehouse ? (testResult.detail.warehouse==='platform_b'?'B仓':testResult.detail.warehouse==='platform'?'C仓':'自有仓') : '全部'}</b> {testResult.detail.left} {testResult.detail.op} {testResult.detail.right}
              <br/>计算: 左侧值 = {String(testResult.detail.left_value)}
              {String(testResult.detail.right_value).startsWith('max(') ? <>，右侧 = {testResult.detail.right_value}</> : <>，右侧值 = {String(testResult.detail.right_value)}</>}
            </div>}
          </div>
        )}
      </div>
    </div>}
  </div>
  </>
}