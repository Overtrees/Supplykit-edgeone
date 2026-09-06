import React, { useState, useMemo, useEffect, useRef } from 'react'
import { api, clearCache } from '../api/client'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'
import { IconLoading } from '../components/Icons'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { useAppStore } from '../store/useAppStore'
import { INV_COLS } from '../components/hammer/configs'
import { t } from "../locale"

const API = import.meta.env.VITE_API_BASE_URL || ''
const COL_KEY='c_cols_inventory'
const getVis=(wt,ch)=>{try{return JSON.parse(localStorage.getItem(COL_KEY+'_'+ch+'_'+wt)||'null')}catch{return null}}

interface InventoryPageProps { highlightSku?: string }

export default function InventoryPage({ highlightSku }: InventoryPageProps) {
  const [batchOpen, setBatchOpen] = useState([])
  const [batchData, setBatchData] = useState({})
  const [batchLoading, setBatchLoading] = useState({})
  const toggleBatch = (x) => {
    const key = x.sku + '|' + x.warehouse
    setBatchOpen(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
    if (!batchData[key] && !batchLoading[key]) {
      setBatchLoading(prev => ({...prev, [key]: true}))
      api.get('/api/batches?channel=' + (x.channel || 'jd') + '&sku=' + encodeURIComponent(x.sku) + '&warehouse=' + encodeURIComponent(x.warehouse) + '&warehouse_type=' + encodeURIComponent(whType)).then(r => {
        setBatchData(prev => ({...prev, [key]: r.data || []}))
        setBatchLoading(prev => ({...prev, [key]: false}))
      }).catch(() => setBatchLoading(prev => ({...prev, [key]: false})))
    }
  }
  const toast = useToast()
  const [inventory, setInventory] = useState([])
  const [loading, setLoading] = useState(true)
  const [invPage, setInvPage] = useState(1)
  const invPageRef = useRef(1)
  const [invTotal, setInvTotal] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadErr, setLoadErr] = useState('')
  const [visCols, setVisCols] = useState([])
  const [confirmDel, setConfirmDel] = useState(null)
  const [monthRange, setMonthRange] = useState('')
  const reqSeq = useRef(0)
  const { channel: globalChannel, hammerWhType, hammerCols, hammerSearch, setHammerSearch } = useAppStore()
  const whType = hammerWhType
  useEffect(() => { if (visCols.length === 0) setVisCols(getVis('own', globalChannel) || INV_COLS['own'].map(c=>c.id)) }, [globalChannel])

  useEffect(() => {
    const saved = hammerCols?.['inventory_'+whType]
    if (saved) setVisCols(saved)
    else {
      const ls = getVis(whType, globalChannel)
      if (ls) setVisCols(ls)
      else setVisCols(INV_COLS[whType].map(c => c.id))
    }
  }, [hammerCols, whType])

  const s = hammerSearch || ''
  const loadInv = async (p) => {
    const seq = ++reqSeq.current
    if (p === 1) setLoading(true)
    else setLoadingMore(true)
    try {
      const r = await api.get('/api/insights/with-sales?wh_type=' + whType + '&channel=' + globalChannel + '&page=' + p + '&page_size=' + 100 + '&search=' + encodeURIComponent(s), { timeout: 90000 })
      if (seq !== reqSeq.current) { setLoading(false); setLoadingMore(false); return }  // 竞态丢弃
      setLoadErr('')
      const d = r.data || {}
      const items = (d.items || d || [])
      setInvTotal(d.total || items.length || 0)
      setInvPage(p); invPageRef.current = p
      setInventory(prev => p === 1 ? items : [...prev, ...items])
      if (p === 1 && items.length > 0) {
        const s = items[0].month_start?.slice(5) || ''
        const e = items[0].month_end?.slice(5) || ''
        setMonthRange(`${s}至${e}`)
      }
    } catch(e) { if (seq === reqSeq.current) { setInventory([]); setLoadErr('加载失败，可能是网络异常或服务暂不可用') } }
    if (seq === reqSeq.current) { setLoading(false); setLoadingMore(false) }
  }
  useEffect(() => { clearCache('with-sales'); setInvPage(1); loadInv(1) }, [whType, globalChannel, s])
  // 从告警跳转: 高亮 SKU 滚动到可视区(等数据渲染后, 多页时也定位)
  useEffect(() => {
    if (!highlightSku) return
    const t = setTimeout(() => {
      try {
        const el = document.getElementById('hl-' + highlightSku)
        if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      } catch(e) {}
    }, 400)
    return () => clearTimeout(t)
  }, [highlightSku, inventory, whType])
  const handleScroll = (e) => {
    const el = e.target
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200 && !loadingMore && inventory.length > 0 && (!invTotal || inventory.length < invTotal)) {
      loadInv(invPage + 1)
    }
  }

  const fl = useMemo(() => inventory, [inventory])

  const totalTurnover = useMemo(() => {
    const valid = inventory.filter(x => x.turnover_days != null)
    return valid.length > 0
      ? (valid.reduce((s,x) => s + x.turnover_days, 0) / valid.length).toFixed(1)
      : null
  }, [inventory])

  const delInv = async () => {
    if (!confirmDel) return
    try {
      const r = await fetch(`${API}/api/inventory/${confirmDel}`, {method:'DELETE', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      if (r.ok) { toast.success('已删除'); setConfirmDel(null); loadInv() }
      else toast.error('删除失败')
    } catch(e) { toast.error('删除失败: '+e.message) }
    setConfirmDel(null)
  }

  return <div className='card'>
    <div className='section-title' style={{display:'flex',justifyContent:'space-between',alignItems:'center',flexWrap:'wrap',gap:8}}>
      <span>进销存 <span className='small muted' style={{fontSize:11,fontWeight:400}}>已加载 {Math.min(inventory.length, invTotal||inventory.length)}/{invTotal||inventory.length} 条 · 显示 {visCols.length}/{INV_COLS[whType].length} 列{s ? ` · "${s}"` : ''}</span></span>
    </div>
    {loading ? <div>{[1,2,3,4].map(i=><div key={i} className='skeleton' style={{height:36,marginBottom:4}}/>)}</div>
    : fl.length === 0
      ? (loadErr ? <ErrorRetry error={loadErr} onRetry={() => loadInv(1)} /> : <EmptyState icon='package' title={s?t("inv.empty_matched"):t("common.empty")} desc={s?'换个关键词试试':'通过清洗导入库存数据'} action={!s&&<button className="btn btn-primary" onClick={()=>window.__setPage&&window.__setPage('cleansing')}>去导入数据 →</button>} />)
      : <div style={{overflow:'auto',maxHeight:'calc(100vh - 180px)'}}>
        <div style={{fontSize:11,color:'var(--muted2)',marginBottom:4}}>{t("common.showing")} {visCols.length}/{INV_COLS[whType].length} {t("common.columns")}</div>
      <table><colgroup>{visCols.map(id=>{const col=INV_COLS[whType].find(c=>c.id===id);return col?<col key={col.id} />:null})}</colgroup>
        <thead style={{position:"sticky",top:0,background:"var(--card)",zIndex:1}}><tr>{visCols.map(id=>{const col=INV_COLS[whType].find(c=>c.id===id);if(!col)return null;let el;if(col.id==='month_in')el=<th key={col.id}>{col.label}<br/><span className='small' style={{fontWeight:400}}>{monthRange}</span></th>;else if(col.id==='month_out')el=<th key={col.id}>{col.label}<br/><span className='small' style={{fontWeight:400}}>{monthRange}</span></th>;else el=<th key={col.id}>{col.label}</th>;return el})}</tr></thead>
      <tbody>{fl.map(x => {
        const isHL = highlightSku && x.sku === highlightSku
        const visCells = visCols.map(function(id){const col=INV_COLS[whType].find(function(c){return c.id===id});if(!col)return null;var el;if(col.id==='warehouse'){var isOpen=batchOpen.includes(x.sku+'|'+x.warehouse);var hasBatch=(x.batch_count||0)>1;el=React.createElement('td',{key:col.id,className:'col-store'},x.warehouse||'-',hasBatch?React.createElement('span',{style:{fontSize:10,marginLeft:6,color:'var(--primary)'}},isOpen?'▴':'⤵ 批次'):null);}else if(col.id==='channel')el=React.createElement('td',{key:col.id,style:{fontSize:11}},x.channel==='other'?'其他':'京东');else if(col.id==='brand')el=React.createElement('td',{key:col.id,style:{fontSize:11}},x.brand||'-');else if(col.id==='sku')el=React.createElement('td',{key:col.id,className:'mono col-sku'},x.sku);else if(col.id==='barcode')el=React.createElement('td',{key:col.id,className:'mono',style:{fontSize:11}},x.barcode||'-');else if(col.id==='name')el=React.createElement('td',{key:col.id,className:'col-name'},x.product_name);else if(col.id==='begin'){var _beg=(x.available_qty||0)-(x.month_inbound||0)+(x.month_outbound||0);el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontWeight:600}},_beg)}else if(col.id==='transit')el=React.createElement('td',{key:col.id,className:'col-qty'},x.in_transit_qty);else if(col.id==='c_transit')el=React.createElement('td',{key:col.id,className:'col-qty'},x.c_transit||0);else if(col.id==='month_in')el=React.createElement('td',{key:col.id,className:'col-qty'},x.month_inbound??0);else if(col.id==='month_out')el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontWeight:600}},x.month_outbound??0);else if(col.id==='prod_date')el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontSize:11}},x.batch_prod_date||'-');else if(col.id==='exp_date')el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontSize:11}},x.batch_exp_date||'-');else if(col.id==='batch_days')el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontSize:11}},x.batch_days||'-');else if(col.id==='eff_status'){var es=x.batch_status;var ecolor=es==='ok'?'var(--success)':es==='warn'?'var(--warning)':es==='no'?'var(--danger)':(es==='expired'?'#7c3aed':'var(--muted2)');var elbl=es==='ok'?'✓ 正常':es==='warn'?'⚠️ 临近':es==='no'?'✗ 否':(es==='expired'?'⚫ 过期':'-');el=React.createElement('td',{key:col.id},React.createElement('span',{style:{fontSize:11,fontWeight:600,color:ecolor}},elbl),x.batch_pct?React.createElement('span',{style:{fontSize:10,color:'var(--muted2)',marginLeft:4}},x.batch_pct+'%'):null);}else if(col.id==='over_third'){var _ot_st=x.batch_status;el=React.createElement('td',{key:col.id,style:{fontSize:11}},_ot_st==='no'?React.createElement('span',{style:{color:'var(--danger)',fontWeight:600}},'✗ 已超1/3'):_ot_st==='expired'?React.createElement('span',{style:{color:'#7c3aed'}},'已过期'):_ot_st==='warn'?React.createElement('span',{style:{color:'var(--warning)',fontWeight:600}},'⚠️ 临近'):_ot_st==='ok'?React.createElement('span',{style:{color:'var(--muted2)'}},'否'):'-');}else if(col.id==='note'){var _ns=x.batch_status;var _nb=(x.batch_pct||0);var _nclr=_ns==='ok'?'var(--success)':_ns==='warn'?'var(--warning)':_ns==='no'?'var(--danger)':(_ns==='expired'?'#7c3aed':'var(--muted2)');var _nlbl=_ns==='ok'?'正常':_ns==='warn'?'临近1/3':_ns==='no'?'已超1/3':(_ns==='expired'?'已过期':'');var _nact=_ns==='ok'?(_nb>30?'动销放缓，促动销':'正常销售'):_ns==='warn'?'促销去库/调拨':_ns==='no'?'尽快清仓/退供':(_ns==='expired'?'报废/退供应商':'');el=React.createElement('td',{key:col.id,style:{fontSize:11}},(_nb>0||_ns)?[React.createElement('span',{key:'s',style:{color:_nclr,fontWeight:600}},_nb+'% '+_nlbl),' → ',React.createElement('span',{key:'a',style:{color:'var(--muted2)'}},_nact)]:'-')}else if(col.id==='avail')el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontWeight:600}},x.available_qty);else if(col.id==='turnover'){var tc=x.turnover_days;el=React.createElement('td',{key:col.id,className:'col-qty',style:{fontWeight:600,color:tc!=null&&tc>30?'#ef4444':tc!=null&&tc>15?'var(--warning)':'var(--text)'}},tc!=null?tc+'天':'∞')}else if(col.id==='price')el=React.createElement('td',{key:col.id,className:'col-price',style:{fontSize:12}},x.price?('¥'+Number(x.price).toFixed(1)):'-');else if(col.id==='stock_amount'){var sa=(x.available_qty||0)*(x.price||0);el=React.createElement('td',{key:col.id,className:'col-price',style:{fontWeight:600,fontSize:12}},sa?'¥'+sa.toLocaleString():'-')}else el=React.createElement('td',{key:col.id,className:'small muted',style:{fontSize:11}},'-');return el})
        var bk=x.sku+'|'+x.warehouse
        var isOpen=batchOpen.includes(bk)
        var batchTrs=[]
        var _span = visCols.length
        if(isOpen&&batchLoading[bk]){
          // 展开加载中: 给出视觉反馈(此前无任何提示, 慢网络下像"点了没反应")
          batchTrs.push(React.createElement('tr',{key:x.id+'-bl',className:'tr-batch'},React.createElement('td',{colSpan:_span,className:'td-loading'},React.createElement(IconLoading,{size:12}),'批次加载中…')))
        } else if(isOpen&&!batchData[bk]){
          // 批次拉取失败/异常: 提示重试(收起后再展开会重新拉取)
          batchTrs.push(React.createElement('tr',{key:x.id+'-be',className:'tr-err'},React.createElement('td',{colSpan:_span,className:'td-loading',style:{color:'var(--danger)'}},'批次加载失败，请收起后重新展开')))
        } else if(isOpen&&batchData[bk]){
          batchData[bk].forEach(function(b,bi){
            var pct=0;if(b.exp_date&&b.prod_date){var dp=Math.abs((new Date(b.exp_date)-new Date(b.prod_date))/86400000);pct=dp>0?Math.max(0,Math.min(100,Math.round(((new Date()-new Date(b.prod_date))/86400000)/dp*100))):0}
            var bcolor=pct>=67?'var(--danger)':(pct>=40?'var(--warning)':'var(--success)')
            var note=pct>=67?'⚠️ 已消耗过半':(pct>=40?'🟡 消耗中':(pct>0?'🟢 正常消耗':'⚪ 刚入库'))
            var bcells=visCols.map(function(id){var col=INV_COLS[whType].find(function(c){return c.id===id});if(!col)return null;var el;if(col.id==='prod_date')el=React.createElement('td',{key:col.id,style:{fontSize:11}},b.prod_date||'-');else if(col.id==='exp_date')el=React.createElement('td',{key:col.id,style:{fontSize:11}},b.exp_date||'-');else if(col.id==='batch_days'){var td=0;if(b.exp_date&&b.prod_date)td=Math.round((new Date(b.exp_date)-new Date(b.prod_date))/86400000);el=React.createElement('td',{key:col.id,style:{fontSize:11}},td||'-')}else if(col.id==='eff_status'){var es='';if(b.exp_date&&b.prod_date){var cv=Math.round(((new Date()-new Date(b.prod_date))/86400000)/((new Date(b.exp_date)-new Date(b.prod_date))/86400000)*100);es=cv>=100?'⚫过期':(cv>=67?'✗否':(cv>40?'⚠️临近':'✓正常'))}el=React.createElement('td',{key:col.id,style:{fontSize:11,fontWeight:600,color:es==='✓正常'?'var(--success)':es==='⚠️临近'?'var(--warning)':es==='✗否'?'var(--danger)':es==='⚫过期'?'#7c3aed':'var(--muted2)'}},es)}else if(col.id==='avail')el=React.createElement('td',{key:col.id,style:{fontWeight:600,fontSize:11}},b.qty);else if(col.id==='over_third'){var ot_st='-';if(b.exp_date&&b.prod_date){var td=Math.round((new Date(b.exp_date)-new Date(b.prod_date))/86400000);if(td>0){var cv=Math.round(((new Date()-new Date(b.prod_date))/86400000)/td*100);ot_st=cv>=100?'⚫已过期':(cv>33?'✗ 已超1/3':'正常')}}el=React.createElement('td',{key:col.id,style:{fontSize:11,color:ot_st.includes('已超')?'var(--danger)':ot_st.includes('过期')?'#7c3aed':'var(--muted2)'}},ot_st)}else if(col.id==='note')el=React.createElement('td',{key:col.id,style:{fontSize:11,color:bcolor,fontWeight:600}},pct+'% '+note);else if(col.id==='warehouse'||col.id==='sku'||col.id==='name'||col.id==='barcode'||col.id==='channel'||col.id==='brand'){var v=col.id==='name'?x.product_name:(col.id==='brand'?(x.brand||'-'):(x[col.id]||'-'));el=React.createElement('td',{key:col.id,style:{fontSize:11}},v)}else if(col.id==='price')el=React.createElement('td',{key:col.id,style:{fontSize:11}},x.price?('¥'+Number(x.price).toFixed(1)):'-');else if(col.id==='month_in')el=React.createElement('td',{key:col.id,style:{fontSize:11,color:'var(--muted2)'}},'-')
            else if(col.id==='month_out')el=React.createElement('td',{key:col.id,style:{fontSize:11,color:'var(--muted2)'}},'-')
            else if(col.id==='begin')el=React.createElement('td',{key:col.id,style:{fontSize:11,color:'var(--muted2)'}},'-')
            else if(col.id==='stock_amount'){var _amt=b.qty*(x.price||0);el=React.createElement('td',{key:col.id,style:{fontSize:11,fontWeight:600,color:_amt>0?'var(--text)':'var(--muted2)'}},_amt>0?'¥'+_amt.toLocaleString():'-')}
            else if(col.id==='turnover')el=React.createElement('td',{key:col.id,style:{fontSize:11,color:'var(--muted2)'}},'-')
            else el=React.createElement('td',{key:col.id,style:{fontSize:11}},'-');return el})
            batchTrs.push(React.createElement('tr',{key:x.id+'-b'+bi,className:'tr-batch'},bcells))
          })
        }        var _hb=(x.batch_count||0)>1;return [React.createElement('tr',{key:x.id,id:'hl-'+x.sku,onClick:_hb?function(){toggleBatch(x)}:null,className:_hb?(isOpen?'tr-click tr-open':'tr-click'):'',style:isHL?{background:'rgba(245,158,11,0.15)',outline:'2px solid #f59e0b'}:{}},visCells)].concat(batchTrs)      })}
      </tbody>
      {totalTurnover != null && <tfoot>
        <tr style={{fontWeight:700,borderTop:'2px solid var(--border)'}}>
          {visCols.map(function(id){
            var col=INV_COLS[whType].find(function(c){return c.id===id});
            if(!col)return null;
            if(col.id==='begin')return React.createElement('td',{key:col.id,style:{textAlign:'right',fontSize:12}},inventory.reduce(function(s,x){return s+(x.beginning_stock||0)},0));
            if(col.id==='month_in')return React.createElement('td',{key:col.id,className:'col-qty'},inventory.reduce(function(s,x){return s+(x.month_inbound||0)},0));
            if(col.id==='month_out')return React.createElement('td',{key:col.id,className:'col-qty',style:{fontWeight:600}},inventory.reduce(function(s,x){return s+(x.month_outbound||0)},0));
            if(col.id==='avail')return React.createElement('td',{key:col.id,className:'col-qty',style:{fontWeight:600}},inventory.reduce(function(s,x){return s+(x.available_qty||0)},0));
            if(col.id==='turnover')return React.createElement('td',{key:col.id,style:{fontSize:13,fontWeight:700}},totalTurnover+' 天');
            if(col.id==='price')return React.createElement('td',{key:col.id,className:'col-price',style:{fontSize:12}},'');
            if(col.id==='stock_amount')return React.createElement('td',{key:col.id,className:'col-price',style:{fontWeight:600,fontSize:12}},'¥'+inventory.reduce(function(s,x){return s+((x.available_qty||0)*(x.price||0))},0).toLocaleString());
            if(id===visCols[0])return React.createElement('td',{key:col.id,colSpan:1,style:{textAlign:'right',fontSize:12,color:'var(--text)'}},'合计');
            return React.createElement('td',{key:col.id});
          })}
        </tr>
      </tfoot>}
              </table>
        {invTotal > 0 && inventory.length < invTotal && (
          <div style={{textAlign:'center',padding:'10px 0'}} ref={function(el){
            if (el && !el._obs) {
              el._obs = new IntersectionObserver(function(entries){
                if (entries[0].isIntersecting && !loadingMore) { var np = invPageRef.current + 1; invPageRef.current = np; loadInv(np) }
              }, {rootMargin: '200px'})
              el._obs.observe(el)
            }
          }}><span className="btn btn-ghost" style={{fontSize:12,padding:'6px 16px',cursor:'pointer'}}>{loadingMore ? '加载中... ' : ''}({Math.min(inventory.length, invTotal)}/{invTotal})</span></div>
        )}
        {invTotal > 0 && inventory.length >= invTotal && <div style={{textAlign:'center',padding:'10px 0',fontSize:11,color:'var(--muted2)'}}>已加载全部 {invTotal} 条</div>}
    </div>}
    <ConfirmDialog open={!!confirmDel} title='删除库存记录' desc='删除后不可恢复' confirmLabel='删除' onConfirm={delInv} onCancel={()=>setConfirmDel(null)} />
  </div>
}
