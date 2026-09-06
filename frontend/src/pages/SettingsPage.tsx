import React, { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { clearCache, clearInflight } from '../api/client'
import { useToast } from '../components/Toast'
import { t } from "../locale"
import ConfirmDialog from '../components/ConfirmDialog'

const VERSION = '1.0.0'
const BUILD = new Date().toISOString().slice(0,10)
const API = import.meta.env.VITE_API_BASE_URL || ''

const Group = ({ title, children }) => (
  <div style={{marginBottom:20}}>
    {title && <div style={{fontSize:13,fontWeight:400,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:0.3,padding:'0 16px 6px 16px'}}>{title}</div>}
    <div style={{background:'var(--card)',borderRadius:32,overflow:'hidden'}}>
      {children}
    </div>
  </div>
)

const Row = ({ label, value, sub, onClick, danger, loading }) => (
  <div onClick={loading ? undefined : onClick} className={onClick && !loading ? 'clickable' : ''} style={{padding:'0 16px',cursor:onClick && !loading ? 'pointer' : 'default',background:'var(--card)',opacity:loading?0.5:1}}>
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'14px 0',minHeight:48,borderBottom:'1px solid var(--border)'}}>
      <div style={{flex:1,minWidth:0}}>
        <div style={{fontSize:16,color:danger?'#ef4444':'var(--text)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:6}}>
          {loading && <span style={{display:'inline-block',width:14,height:14,border:'2px solid var(--primary)',borderTopColor:'transparent',borderRadius:'50%',animation:'spin 0.6s linear infinite'}} />}
          {label}
        </div>
        {sub && <div style={{fontSize:12,color:'var(--muted2)',marginTop:2}}>{sub}</div>}
      </div>
      <div style={{display:'flex',alignItems:'center',gap:6,flexShrink:0,marginLeft:8}}>
        {value && <span style={{fontSize:15,color:'var(--muted2)',maxWidth:160,textAlign:'right',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{value}</span>}
        {onClick && !loading && <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{flexShrink:0,opacity:0.3}}><path d="M4.5 2.5L8 6l-3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
      </div>
    </div>
  </div>
)

const LastRow = ({ label, value, sub, onClick, danger, loading }) => (
  <div onClick={loading ? undefined : onClick} className={onClick && !loading ? 'clickable' : ''} style={{padding:'0 16px',cursor:onClick && !loading ? 'pointer' : 'default',background:'var(--card)',opacity:loading?0.5:1}}>
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'14px 0',minHeight:48}}>
      <div style={{flex:1,minWidth:0}}>
        <div style={{fontSize:16,color:danger?'#ef4444':'var(--text)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:6}}>
          {loading && <span style={{display:'inline-block',width:14,height:14,border:'2px solid var(--danger)',borderTopColor:'transparent',borderRadius:'50%',animation:'spin 0.6s linear infinite'}} />}
          {label}
        </div>
        {sub && <div style={{fontSize:12,color:'var(--muted2)',marginTop:2}}>{sub}</div>}
      </div>
      <div style={{display:'flex',alignItems:'center',gap:6,flexShrink:0,marginLeft:8}}>
        {value && <span style={{fontSize:15,color:'var(--muted2)',maxWidth:160,textAlign:'right',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{value}</span>}
        {onClick && !loading && <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{flexShrink:0,opacity:0.3}}><path d="M4.5 2.5L8 6l-3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
      </div>
    </div>
  </div>
)

function RecycleBin({ onClose, toast }) {
  var [rules, setRules] = useState([])
  useEffect(function() {
    var header = document.querySelector('header')
    if (header) header.style.display = 'none'
    return function() { if (header) header.style.display = '' }
  }, [])
  var [orders, setOrders] = useState([])
  var [loading, setLoading] = useState(true)
  // 批量操作：selected = {rules: Set<id>, orders: Set<id>}
  var [selected, setSelected] = useState({ rules: new Set(), orders: new Set() })
  var [batchBusy, setBatchBusy] = useState(false)

  // 数据加载函数（提取自 useEffect，供初始化与批量操作后刷新复用）
  var loadData = function() {
    setLoading(true)
    var _auth = {'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}
    Promise.all([
      fetch(API + '/api/rules?channel=all&include_deleted=1', {headers:_auth}).then(function(r) { return r.json() }),
      fetch(API + '/api/orders?page=1&page_size=200', {headers:_auth}).then(function(r) { return r.json() }),
    ]).then(function([rData, oData]) {
      var items = rData.data || rData || []
      setRules(items.filter(function(x) { return x.deleted_at }))
      var o = oData.data || oData || []
      setOrders(Array.isArray(o) ? o.filter(function(x) { return x.deleted_at }) : [])
      setLoading(false)
    }).catch(function() { setLoading(false) })
  }

  useEffect(function() { loadData() }, [])

  var toggleSel = function(type, id) {
    setSelected(function(prev) {
      var next = new Set(prev[type])
      if (next.has(id)) next.delete(id); else next.add(id)
      return { ...prev, [type]: next }
    })
  }
  var toggleAll = function(type, items) {
    setSelected(function(prev) {
      var all = items.length > 0 && items.every(function(x) { return prev[type].has(x.id) })
      var next = new Set()
      if (!all) items.forEach(function(x) { next.add(x.id) })
      return { ...prev, [type]: next }
    })
  }
  var batchAction = async function(type, action, label) {
    var ids = Array.from(selected[type])
    if (ids.length === 0) { toast.error('请先勾选要' + label + '的项'); return }
    setBatchBusy(true)
    try {
      var _auth = {'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})(), 'Content-Type':'application/json'}
      if (type === 'rules' && action === 'permanent-delete') {
        await fetch(API + '/api/rules/batch', { method:'POST', headers:_auth, body: JSON.stringify({action:'purge', ids: ids}) })
      } else {
        await Promise.all(ids.map(function(id) {
          return fetch(API + '/api/' + type + '/' + id + '/' + action, { method:'POST', headers:_auth })
        }))
      }
      // toast 可能因 Context 问题不可用，try/catch 降级
      try { toast.success(label + '完成: ' + ids.length + ' 项') } catch(e) { window.alert(label + '完成: ' + ids.length + ' 项') }
      loadData()
      setSelected(function(prev) {
        var next = new Set(prev[type]); ids.forEach(function(id) { next.delete(id) })
        return { ...prev, [type]: next }
      })
    } catch(e) {
      try { toast.error(label + '失败: ' + e.message) } catch(e2) { window.alert(label + '失败: ' + e.message) }
    }
    setBatchBusy(false)
  }
  var confirmPurge = function(type) {
    var ids = Array.from(selected[type])
    if (ids.length === 0) { toast.error('请先勾选要永久删除的项'); return }
    if (window.confirm('永久删除 ' + ids.length + ' 项？此操作不可撤销')) batchAction(type, 'permanent-delete', '永久删除')
  }

  var renderList = function(type, items) {
    if (items.length === 0) return <div className="small muted" style={{padding:'20px',textAlign:'center',fontSize:13}}>{type==='rules'?t("recycle.empty_rules"):t("recycle.empty_orders")}</div>
    var allSelected = items.length > 0 && items.every(function(x) { return selected[type].has(x.id) })
    return <>
      <div style={{display:'flex',gap:6,padding:'8px 4px',flexWrap:'wrap'}}>
        <span onClick={function(){toggleAll(type, items)}} className="clickable" style={{fontSize:12,padding:'5px 12px',borderRadius:99,border:'1px solid var(--border)',background:'var(--card)',color:'var(--primary)',cursor:'pointer'}}>{allSelected ? '取消全选' : '全选'}</span>
        <span onClick={function(){batchAction(type, 'restore', '恢复')}} className="clickable" style={{fontSize:12,padding:'5px 12px',borderRadius:99,border:'1px solid var(--border)',background:'var(--card)',color:'var(--success)',cursor:'pointer'}}>批量恢复 ({selected[type].size})</span>
        <span onClick={function(){confirmPurge(type)}} className="clickable" style={{fontSize:12,padding:'5px 12px',borderRadius:99,border:'1px solid var(--border)',background:'var(--card)',color:'var(--danger)',cursor:'pointer'}}>永久删除 ({selected[type].size})</span>
      </div>
      <div style={{background:'var(--card)',borderRadius:32,overflow:'hidden'}}>
        {items.map(function(x) {
          var isSel = selected[type].has(x.id)
          return <div key={x.id} onClick={function(){toggleSel(type, x.id)}} className="clickable" style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'12px 16px',borderBottom:'1px solid var(--border)',background:isSel?'rgba(29,78,216,0.08)':'transparent'}}>
            <span style={{display:'flex',alignItems:'center',gap:10,flex:1,minWidth:0}}>
              <span style={{width:18,height:18,borderRadius:6,border:'1.5px solid',borderColor:isSel?'var(--primary)':'var(--border)',background:isSel?'var(--primary)':'transparent',display:'inline-flex',alignItems:'center',justifyContent:'center',color:'#fff',fontSize:11,flexShrink:0}}>{isSel?'✓':''}</span>
              <span style={{fontSize:14,color:'var(--text)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{type==='rules'?x.name:(x.order_no + ' - ' + (x.product_name||''))}</span>
            </span>
            {!isSel && <span style={{fontSize:11,color:'var(--muted2)',flexShrink:0,marginLeft:8}}>{x.deleted_at ? String(x.deleted_at).slice(0,10) : ''}</span>}
          </div>
        })}
      </div>
    </>
  }

  return <div style={{display:'flex',flexDirection:'column',minHeight:'100%',background:'var(--bg)',padding:'0 0 calc(0px + env(safe-area-inset-bottom, 20px))',boxSizing:'border-box'}}>
    <div style={{position:'fixed',left:0,right:0,top:0,zIndex:5001,display:'flex',justifyContent:'space-between',alignItems:'center',padding:'calc(env(safe-area-inset-top, 0px) + 12px) 16px 12px 16px',background:'transparent'}}>
      <div style={{fontSize:16,fontWeight:600,color:'var(--text)',padding:'0 14px',borderRadius:99,minHeight:48,display:'flex',alignItems:'center',marginLeft:16,background:'var(--bg-thin)',backdropFilter:'var(--blur-thin)',WebkitBackdropFilter:'var(--blur-thin)',border:'0.5px solid var(--border-light)'}}>{t("settings.recycle_bin")}</div>
      <div onClick={onClose} className="clickable" style={{width:48,height:48,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',cursor:'pointer',flexShrink:0,marginRight:16,background:'var(--bg-thin)',backdropFilter:'var(--blur-thin)',WebkitBackdropFilter:'var(--blur-thin)',border:'0.5px solid var(--border-light)'}}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2.5" strokeLinecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
      </div>
    </div>
    <div style={{padding:'calc(env(safe-area-inset-top, 0px) + 44px) 16px 16px',maxWidth:500,margin:'0 auto',width:'100%'}}>
    {loading ? <div style={{padding:'0 4px'}}>{[1,2,3].map(function(i) {
      return <div key={i} style={{background:'var(--card)',borderRadius:32,padding:16,marginBottom:8}}>
        <div className="skeleton" style={{width:'40%',height:14,marginBottom:8}} />
        <div className="skeleton" style={{width:'70%',height:14}} />
      </div>
    })}</div> : <>
      <div style={{fontSize:13,fontWeight:600,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:0.3,padding:'0 4px 6px 4px',marginBottom:0}}>{t("recycle.deleted_rules")}</div>
      {renderList('rules', rules)}
      <div style={{fontSize:13,fontWeight:600,color:'var(--muted2)',textTransform:'uppercase',letterSpacing:0.3,padding:'0 4px 6px 4px',marginTop:16,marginBottom:0}}>{t("recycle.deleted_orders")}</div>
      {renderList('orders', orders)}
      {batchBusy && <div style={{textAlign:'center',padding:16,fontSize:12,color:'var(--muted2)'}}>处理中...</div>}
    </>}
    </div>
  </div>
}

export default function SettingsPage() {
  const toast = useToast()
  const { channel, wsStatus } = useAppStore()
  const [status, setStatus] = useState('检查中...')
  const [ping, setPing] = useState(0)
  const [lastCheck, setLastCheck] = useState('')
  const [dbSize, setDbSize] = useState('')
  const [cacheSize, setCacheSize] = useState(0)
  const [confirm, setConfirm] = useState(null) // {type:'fill'|'reset'}
  const [refreshing, setRefreshing] = useState(false)


  const checkConnection = async () => {
    setRefreshing(true)
    const start = performance.now()
    try {
      const r = await fetch(API + '/api/insights/ping')
      const ms = Math.round(performance.now() - start)
      const d = await r.json()
      setStatus(d.ok ? '正常' : '异常')
      setPing(ms)
      setLastCheck(new Date().toLocaleTimeString())
      if (d.ok) toast.success('连接正常 · ' + ms + 'ms')
    } catch {
      setStatus('无法连接')
      setPing(0)
      setLastCheck(new Date().toLocaleTimeString())
      toast.error('连接失败')
    }
    setRefreshing(false)
  }

  useEffect(() => {
    checkConnection()
    const timer = setInterval(checkConnection, 30000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    try {
      const s = localStorage.length
      let total = 0
      for (let i = 0; i < s; i++) {
        const k = localStorage.key(i)
        if (k) total += (localStorage.getItem(k) || '').length
      }
      setCacheSize(Math.round(total / 1024))
    } catch {}
  }, [])

  // 种子填充任务轮询：完成后恢复按钮状态
  useEffect(() => {
    const seedTask = (() => { try { return localStorage.getItem('c_seed_task') } catch { return null } })()
    if (!seedTask || !seeding) return
    const poll = setInterval(async () => {
      try {
        const r = await fetch(API + '/api/seed/fill/status?task_id=' + seedTask, {headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
        const d = await r.json()
        if (d.data?.status === 'done' || d.data?.status === 'error') {
          clearInterval(poll); setSeeding(false)
          if (d.data?.status === 'done') { try { localStorage.removeItem('c_seed_task') } catch {}; window.location.reload() }
        }
      } catch { clearInterval(poll); setSeeding(false) }
    }, 3000)
    return () => clearInterval(poll)
  }, [])

  const clearLocalCache = () => {
    const keys = []
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i)
        if (k && (k.startsWith('c_cols_') || k.startsWith('c_ordered') || k.startsWith('c_replen_') || k.startsWith('c_page'))) {
          keys.push(k)
        }
      }
      keys.forEach(k => { try { localStorage.removeItem(k) } catch {} })
      setCacheSize(0)
      toast.success('缓存已清除')
    } catch { toast.error('无法访问本地存储') }
  }

  const [seeding, setSeeding] = useState(() => { try { return !!localStorage.getItem('c_seed_task') } catch { return false } })
  const [resetting, setResetting] = useState(false)
  // 告警推送 webhook 配置（全局，存 replenishment_config.webhook_url）
  const [webhookUrl, setWebhookUrl] = useState('')
  const [webhookSaving, setWebhookSaving] = useState(false)
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(API + '/api/replenishment-config?channel=jd', { headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })() } })
        const d = await r.json()
        if (d?.data?.webhook_url) setWebhookUrl(d.data.webhook_url)
      } catch {}
    })()
  }, [])
  const saveWebhook = async () => {
    setWebhookSaving(true)
    try {
      const r = await fetch(API + '/api/replenishment-config?channel=jd', { method: 'PUT', headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })(), 'Content-Type': 'application/json' }, body: JSON.stringify({ webhook_url: webhookUrl.trim() }) })
      const d = await r.json()
      if (d.ok) toast.success(webhookUrl.trim() ? '已保存，新告警将推送到该地址' : '已保存，告警推送已关闭')
      else toast.error('保存失败: ' + (d.error || ''))
    } catch(e) { toast.error('保存失败: ' + e.message) }
    setWebhookSaving(false)
  }

  const doSeed = async () => {
    setConfirm(null); setSeeding(true)
    try {
      const r = await fetch(API + '/api/seed/fill', {method:'POST', headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok) {
        if (d.data?.requires_reset) { toast.error('已有数据，请先重置'); setSeeding(false); setConfirm('reset'); return }
        const taskId = d.data?.task_id
        if (taskId) { try { localStorage.setItem('c_seed_task', taskId) } catch {} }
        toast.add({type:'success', title:'填充任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
      } else { toast.error('填充失败: ' + (d.error || '')); setSeeding(false) }
    } catch { toast.error('填充失败'); setSeeding(false) }
  }

  const doReset = async () => {
    setConfirm(null)
    setResetting(true)
    try {
      const r = await fetch(API + '/api/seed/reset', {method:'POST', headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok && d.data?.task_id) {
        try { localStorage.setItem('c_reset_task', d.data.task_id) } catch {}
        toast.success('重置任务已提交，后台清理中...')
        // 轮询等待重置完成
        const poll = setInterval(async () => {
          try {
            const sr = await fetch(API + '/api/seed/fill/status?task_id=' + d.data.task_id, {headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
            const sd = await sr.json()
            if (sd.data?.status === 'done' || sd.data?.status === 'error') {
              clearInterval(poll)
              try { localStorage.removeItem('c_reset_task') } catch {}
              clearCache(); clearInflight()
              useAppStore.setState({ dashboard: null, alerts: [], stockRisk: [] })
              toast.success('数据已重置，即将刷新')
              setTimeout(() => window.location.reload(), 1500)
            }
          } catch { clearInterval(poll); setResetting(false) }
        }, 2000)
      } else {
        toast.error('重置失败: ' + (d.error || ''))
        setResetting(false)
      }
    } catch { toast.error('重置失败'); setResetting(false) }
  }

  return <>
    {confirm === 'recycle' ? <RecycleBin onClose={() => setConfirm(null)} toast={toast} /> : <div style={{padding:'16px 0',maxWidth:500,margin:'0 auto'}}>
      <Group title="连接状态">
        <Row label="后端服务" value={status} sub={`${ping}ms · ${lastCheck}`} />
        <Row label="实时连接" value={wsStatus === 'connected' ? '已连接' : wsStatus === 'polling' ? '轮询中' : '已断开'} />
        <LastRow label="当前渠道" value={channel === 'jd' ? '京东' : '其他渠道'} />
      </Group>

      <Group title="操作">
        <Row label="刷新连接" onClick={checkConnection} loading={refreshing} />
        <Row label="清除本地缓存" sub={cacheSize > 0 ? `${cacheSize}KB` : '无缓存'} onClick={() => { if (cacheSize > 0) setConfirm('cache'); else toast.success('暂无缓存需要清除') }} />
        <LastRow label="回收站" sub="查看已删除的规则和订单，可恢复或永久删除" onClick={() => setConfirm('recycle')} />
      </Group>

      <Group title="系统信息">
        <Row label="版本号" value={`v${VERSION}`} />
        <Row label="构建日期" value={BUILD} />
        <Row label="前端" value="React 18 + TypeScript" />
        <LastRow label="后端" value="FastAPI + SQLite" />
      </Group>

      <Group title="界面">
        <LastRow label="重置欢迎页" sub="重新显示首次使用引导" onClick={() => { try { localStorage.removeItem('c_welcome_seen') } catch {} toast.success('欢迎页已重置') }} />
      </Group>

      <Group title="种子数据">
        <Row label="一键填充" sub="生成 2,000 SKU × 60 天 × 10 万条模拟数据" onClick={() => setConfirm('fill')} loading={seeding} />
        <LastRow label="一键重置" sub="清空所有数据恢复初始状态" onClick={() => setConfirm('reset')} danger loading={resetting} />
      </Group>

      <Group title="告警推送">
        <div style={{padding:14}}>
          <div style={{fontSize:13,fontWeight:600,marginBottom:4}}>Webhook 地址</div>
          <div className="small muted" style={{fontSize:11,marginBottom:8}}>钉钉/企业微信机器人地址，新告警每 30 分钟推送到该地址（留空不推送）</div>
          <div style={{display:'flex',gap:8}}>
            <input value={webhookUrl} onChange={e=>setWebhookUrl(e.target.value)} placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." style={{flex:1,fontSize:14,padding:'10px 12px',borderRadius:99,border:'1px solid var(--border)',background:'var(--card)',outline:'none',minWidth:0}} />
            <button onClick={saveWebhook} disabled={webhookSaving} className="btn btn-primary" style={{flexShrink:0,minHeight:40,padding:'0 18px',fontSize:14}}>{webhookSaving?'保存中...':'保存'}</button>
          </div>
        </div>
      </Group>

      <div style={{textAlign:'center',marginTop:24,fontSize:12,color:'var(--muted2)'}}>
        SupplyKit · 供应链数据工作台
      </div>

      {/* 确认弹窗 */}
      {confirm === 'fill' && (
        <ConfirmDialog
          open
          title="生成种子数据？"
          desc="将生成 160 个商品、60 天订单、9 个仓库库存等模拟数据，覆盖现有数据。"
          confirmLabel="生成"
          onConfirm={doSeed}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'reset' && (
        <ConfirmDialog
          open
          title="重置所有数据？"
          desc="此操作不可恢复。将清空订单、库存、商品、规则等全部数据。"
          confirmLabel="重置"
          onConfirm={doReset}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'cache' && (
        <ConfirmDialog
          open
          title="清除本地缓存？"
          desc="将清除列配置、搜索记录等本地缓存数据，不影响服务器数据。"
          confirmLabel="清除"
          onConfirm={() => { clearLocalCache(); setConfirm(null) }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>}
</>
}