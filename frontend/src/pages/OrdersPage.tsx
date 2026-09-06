import React, { useState, useEffect, useRef } from 'react'
import { useAppStore } from '../store/useAppStore'
import { clearCache } from '../api/client'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { t } from "../locale"

const COLS = [
  {id:'date',label:'下单日期'},{id:'order_no',label:'订单号'},{id:'barcode',label:'69码'},{id:'store',label:'店铺'},{id:'warehouse',label:'仓库'},
  {id:'product',label:'商品'},{id:'quantity',label:'数量'},{id:'unit_price',label:'单价'},{id:'amount',label:'金额'},{id:'status',label:'状态'},
  {id:'paid_at',label:'入库日期'},
]
const COL_KEY = () => 'c_cols_orders_' + (useAppStore.getState().channel || 'jd')
const getVis=()=>{try{return JSON.parse(localStorage.getItem(COL_KEY())||'null')}catch{return null}}

function OrderSkeleton() {
  return <div>
    {[1,2,3,4,5].map(i => <div key={i} style={{display:'flex',gap:8,padding:'8px 0',borderBottom:'1px solid var(--border)'}}>
      <div className="skeleton" style={{width:80,height:14}}/><div className="skeleton" style={{width:60,height:14}}/>
      <div className="skeleton" style={{width:40,height:14}}/><div className="skeleton" style={{flex:1,height:14}}/>
      <div className="skeleton" style={{width:36,height:14}}/><div className="skeleton" style={{width:36,height:14}}/>
      <div className="skeleton" style={{width:50,height:14}}/>
    </div>)}
  </div>
}

export default function OrdersPage() {
  const toast = useToast()
  const { orders, orderPage, orderLoading, setOrderPage, orderStatus, dataLoaded, channel, hammerCols, hammerSearch, orderTotal, setHammerSearch, orderLoadErr } = useAppStore()
  useEffect(() => { clearCache('orders'); useAppStore.getState().loadAll(1); setHammerSearch('') }, [channel])
  useEffect(() => { useAppStore.getState().loadAll() }, [hammerSearch, orderStatus])
  const [confirmDel, setConfirmDel] = useState(null)
  // 删除撤销定时器集合：卸载时统一清理，防止软删订单残留
  const timersRef = useRef([])
  useEffect(() => () => { timersRef.current.forEach(clearTimeout); timersRef.current = [] }, [])
  const [visCols, setVisCols] = useState(() => getVis(COL_KEY()) || COLS.map(c => c.id))
  useEffect(() => { if (hammerCols?.orders) setVisCols(hammerCols.orders) }, [hammerCols])
  const totalPages = Math.max(1, Math.ceil((orderTotal || 0) / 30))
  const s = hammerSearch || ''
  const st = orderStatus || ''

  // 加载平台仓库存（按 SKU+仓库 维度）
  const delOrder = async () => {
    if (!confirmDel) return
    var id = confirmDel
    setConfirmDel(null)
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      const r = await fetch(`${API}/api/orders/${id}`, {method:'DELETE', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      if (r.ok) {
        useAppStore.getState().loadAll()
        var timer = setTimeout(async function() {
          await fetch(`${API}/api/orders/${id}/permanent-delete`, {method:'POST', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
        }, 5000)
        // 组件卸载/页面切换时清理定时器，防止软删订单残留（服务端 30 天回收站兜底）
        timersRef.current.push(timer)
        toast.add({type:'success', title:t("undo.deleted"), duration:5000, action: {label: t("undo.undo"), handler: async function() {
          clearTimeout(timer)
          timersRef.current = timersRef.current.filter(x => x !== timer)
          await fetch(`${API}/api/orders/${id}/restore`, {method:'POST', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
          useAppStore.getState().loadAll()
        }}})
      } else toast.error('删除失败')
    } catch(e) { toast.error('删除失败: '+e.message) }
  }

  return <div>
    {s !== '' && <div style={{display:'flex',gap:6,alignItems:'center',marginBottom:8,flexWrap:'wrap'}}>
      <span className="small muted">搜索 "{s}"</span>
      {st && <span className="pill info">{st}</span>}
    </div>}

    <div className="card">
    <div style={{fontSize:18,fontWeight:700,marginBottom:8}}>订单 <span className="small muted" style={{fontWeight:400}}>{t("common.total")} {orderTotal || 0} {t("common.items")}</span></div>
    {orderLoading || !dataLoaded ? <OrderSkeleton />
    : orders.length === 0
      ? (orderLoadErr ? <ErrorRetry error={orderLoadErr} onRetry={() => useAppStore.getState().loadAll()} /> : <EmptyState icon='clipboard' title={s?t("order.empty_matched"):t("order.empty")} desc={s?'换个关键词试试':'通过清洗导入订单数据'} action={!s&&<button className="btn btn-primary" onClick={()=>window.__setPage&&window.__setPage('cleansing')}>去导入数据 →</button>} />)
      : <div style={{overflow:'auto',maxHeight:"calc(100vh - 180px)"}}>
        <div style={{fontSize:11,color:'var(--muted2)',marginBottom:4}}>{t("common.showing")} {visCols.length}/{COLS.length} {t("common.columns")}</div>
      <table><colgroup>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);return col?<col key={col.id} />:null})}</colgroup>
      <thead style={{position:"sticky",top:0,background:"var(--card)",zIndex:1}}><tr>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);return col?<th key={col.id}>{col.label}</th>:null})}</tr></thead>
      <tbody>
        {orders.map(x => {
          return <tr key={x.id}>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);if(!col)return null;
            if(col.id==='order_no')return <td key={col.id} className="mono col-sku">{x.order_no}</td>
            if(col.id==='barcode')return <td key={col.id} className="mono" style={{fontSize:11}}>{x.barcode||'-'}</td>
            if(col.id==='store')return <td key={col.id} className="col-store">{x.store||'-'}</td>
            if(col.id==='warehouse')return <td key={col.id} className="col-store">{x.warehouse||'-'}</td>
            if(col.id==='product')return <td key={col.id} className="col-name">{x.product_name}</td>
            if(col.id==='quantity')return <td key={col.id} className="col-qty">{x.quantity}</td>
            if(col.id==='unit_price')return <td key={col.id} className="col-price">¥{Number(x.unit_price||x.total_amount/(x.quantity||1)).toLocaleString()}</td>
            if(col.id==='amount')return <td key={col.id} className="col-price">¥{Number(x.total_amount).toLocaleString()}</td>
            if(col.id==='status')return <td key={col.id}><span className={'pill ' + (function(){var s=x.order_status||'';if(s.includes('完成')||s.includes('签收')||s.includes('收货'))return 'success';if(s.includes('退款')||s.includes('取消')||s.includes('退货')||s.includes('售后'))return 'danger';if(s.includes('发货')||s.includes('出库'))return 'info';return 'warning'})()}>{x.order_status}</span></td>
            if(col.id==='date')return <td key={col.id} className="col-date">{x.ordered_at}</td>
            if(col.id==='paid_at')return <td key={col.id} className="col-date">{x.paid_at||'-'}</td>

            return <td key={col.id} className="small muted" style={{fontSize:11}}>-</td>
          })}</tr>
        })}
      </tbody></table>
    </div>}
    </div>  {/* end card */}
    <ConfirmDialog open={!!confirmDel} title='删除订单' desc='删除后不可恢复' confirmLabel='删除' onConfirm={delOrder} onCancel={()=>setConfirmDel(null)} />

    {orderTotal > 30 && <div style={{display:'flex',justifyContent:'center',alignItems:'center',gap:8,marginTop:12,flexWrap:'wrap'}}>
      <button onClick={()=>setOrderPage(1)} disabled={orderPage<=1} className="page-btn" style={{fontSize:12}}>‹‹</button>
      <button onClick={()=>setOrderPage(orderPage-1)} disabled={orderPage<=1} className="page-btn" style={{fontSize:14}}>‹</button>
      <span className="small muted" style={{fontSize:12}}>第 {orderPage}/{totalPages} 页</span>
      <button onClick={()=>setOrderPage(orderPage+1)} disabled={orderPage>=totalPages} className="page-btn" style={{fontSize:14}}>›</button>
      <button onClick={()=>setOrderPage(totalPages)} disabled={orderPage>=totalPages} className="page-btn" style={{fontSize:12}}>››</button>
      <span style={{display:'flex',alignItems:'center',gap:4}}>
        <span className="small muted">跳至</span>
        <input type="number" min={1} max={totalPages} defaultValue={orderPage}
          onKeyDown={e=>{if(e.key==='Enter'){const v=parseInt(e.target.value);if(v>=1&&v<=totalPages)setOrderPage(v)}}}
          style={{width:50,fontSize:12,padding:'4px 6px',border:'none',borderRadius:32,textAlign:'center',background:'var(--card)',color:'var(--text)',boxSizing:'border-box',outline:'none'}} />
        <span className="small muted">页</span>
      </span>
    </div>}
  </div>
}
