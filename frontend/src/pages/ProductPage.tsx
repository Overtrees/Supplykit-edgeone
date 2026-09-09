import React,{useEffect,useState, useRef} from 'react'
import {api} from '../api/client'
import { useToast } from '../components/Toast'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'

import { useAppStore } from '../store/useAppStore'
import { clearCache } from '../api/client'
import { t } from "../locale"
import { PRODUCT_COLS as COLS } from '../components/hammer/configs'
const COL_KEY = () => 'c_cols_products_' + (useAppStore.getState().channel || 'jd')
const getVis = () => { try { return JSON.parse(localStorage.getItem(COL_KEY())||'null') } catch{return null} }

function Skeleton(){return <div>{[1,2,3,4].map(i=><div key={i} style={{display:'flex',gap:8,padding:'8px 0',borderBottom:'1px solid var(--border)'}}>
  <div className="skeleton" style={{width:70,height:14}}/><div className="skeleton" style={{flex:1,height:14}}/>
  <div className="skeleton" style={{width:50,height:14}}/><div className="skeleton" style={{width:40,height:14}}/>
  <div className="skeleton" style={{width:50,height:14}}/><div className="skeleton" style={{width:40,height:14}}/>
</div>)}</div>}

export default function ProductPage(){const[list,setList]=useState([]);const[ld,setLd]=useState(true);const[pg,setPg]=useState(1);const pgRef=useRef(1);const[pgTotal,setPgTotal]=useState(0);const[loadingMore,setLoadingMore]=useState(false);const reqSeq=useRef(0);const[loadErr,setLoadErr]=useState('')
const[visCols,setVisCols]=useState(()=>getVis(COL_KEY())||COLS.map(c=>c.id))
const toast = useToast()
const {channel: globalChannel, hammerSearch, hammerCols, prodBatch, prodSelIds, setProdBatchSel, setProdBatchFilterLen, prodBatchVersion, prodBatchAllReq} = useAppStore()
const selIds = prodSelIds || []
const setSelIds = setProdBatchSel
const [[, setBatchBusy]] = useState(false)
const s = hammerSearch || ''
const fl = ld ? [] : list
const loadProd=(p)=>{const seq=++reqSeq.current;if(p===1)setLd(true);else setLoadingMore(true);api.get('/api/products?page='+p+'&page_size=100&channel='+globalChannel+'&search='+encodeURIComponent(s),{timeout:90000}).then(r=>{if(seq!==reqSeq.current)return;const d=r.data||{};const items=d.items||d||[];setPgTotal(d.total||items.length||0);setPg(p); pgRef.current = p;setList(prev=>p===1?items:[...prev,...items]);setLoadErr('');setLd(false);setLoadingMore(false);const _m={...useAppStore.getState().batchStateMap};(items||[]).forEach(it=>{if(it&&it.id)_m[it.id]=it.is_active?1:0});useAppStore.setState({batchStateMap:_m})}).catch(()=>{if(seq===reqSeq.current){setLd(false);setLoadingMore(false);setList([]);setLoadErr('加载失败，可能是网络异常或服务暂不可用')}})}
useEffect(()=>{clearCache('products');setPg(1);loadProd(1)}, [globalChannel, s])
useEffect(() => {
  if (hammerCols?.products) setVisCols(hammerCols.products)
}, [hammerCols])
// 退出批量模式时清空选择
useEffect(()=>{ if(!prodBatch) setSelIds([]) },[prodBatch])
useEffect(()=>{ if(prodBatchVersion>0){ clearCache('products'); reload() } },[prodBatchVersion])
  useEffect(()=>{ if(prodBatchAllReq>0){ const all=fl.map(x=>x.id); setSelIds(selIds.length===all.length&&all.length>0?[]:all) } },[prodBatchAllReq])
useEffect(()=>{ setProdBatchFilterLen(fl.length) },[fl.length])
const reload = () => { setPgTotal(0); loadProd(1) }

if(ld)return<div className='card'><div className='section-title'><span>{t("nav.products")}</span></div><Skeleton/></div>

return<div className='card' style={{containerType:'inline-size'}}>
<div className='section-title' style={{display:'flex',flexWrap:'wrap',gap:6,alignItems:'center'}}>
  <span>商品管理 <span className='small muted' style={{fontSize:11,fontWeight:400}}>已加载 {Math.min(list.length, pgTotal||list.length)}/{pgTotal||list.length} 条 · 显示 {visCols.length}/{COLS.length} 列{s ? ` · "${s}"` : ''}</span></span>
</div>
{fl.length===0?(loadErr?<ErrorRetry error={loadErr} onRetry={()=>loadProd(1)}/>:<EmptyState icon='tag' title={s?t("product.empty_matched"):t("product.empty")} desc={s?'换个关键词试试':'通过清洗页导入商品数据'} action={!s&&<button className="btn btn-primary" onClick={()=>window.__setPage&&window.__setPage('cleansing')}>去导入数据 →</button>}/>):<div style={{overflow:'auto',maxHeight:"calc(100vh - 180px)"}}>

<div style={{fontSize:11,color:'var(--muted2)',marginBottom:4}}>{t("common.showing")} {visCols.length}/{COLS.length} {t("common.columns")}</div>
<table><colgroup>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);return col?<col key={col.id} />:null})}</colgroup>
<thead style={{position:"sticky",top:0,background:"var(--card)",zIndex:1}}><tr>{prodBatch&&<th style={{width:30}}></th>}{visCols.map(id=>{const col=COLS.find(c=>c.id===id);return col?<th key={col.id}>{col.label}</th>:null})}</tr></thead>
<tbody>{fl.map(x=><tr key={x.id} style={{...(prodBatch&&selIds.includes(x.id)?{background:'rgba(29,78,216,0.08)'}:undefined),...(x.status!=='active'?{opacity:0.55}:undefined)}}>{prodBatch&&<td className="clickable" onClick={(e)=>{const ids=selIds;setSelIds(ids.includes(x.id)?ids.filter(i=>i!==x.id):[...ids,x.id])}}><span style={{width:18,height:18,borderRadius:6,border:'1.5px solid',borderColor:selIds.includes(x.id)?'var(--primary)':'var(--border)',background:selIds.includes(x.id)?'var(--primary)':'transparent',display:'inline-flex',alignItems:'center',justifyContent:'center',color:'#fff',fontSize:11}}>{selIds.includes(x.id)?'✓':''}</span></td>}{visCols.map(id=>{const col=COLS.find(c=>c.id===id);if(!col)return <td key={col.id} className="small muted" style={{fontSize:11}}>-</td>;
  if(col.id==='barcode')return <td key={col.id} className='mono' style={{fontSize:11}}>{x.barcode||'-'}</td>
  if(col.id==='channel')return <td key={col.id} style={{fontSize:11}}>{(useAppStore.getState().channel)==='other'?'其他':'京东'}</td>
  if(col.id==='brand')return <td key={col.id} style={{fontSize:12}}>{x.brand||'-'}</td>
  if(col.id==='sku')return <td key={col.id} className='mono col-sku'>{x.sku}</td>
  if(col.id==='name')return <td key={col.id} className='col-name'>{x.product_name}</td>
  if(col.id==='store')return <td key={col.id} className='col-store'>{x.store}</td>
  if(col.id==='cat')return <td key={col.id} className='col-store'>{x.category}</td>
  if(col.id==='price')return <td key={col.id} className='col-price'>¥{x.price}</td>
  if(col.id==='box')return <td key={col.id} className='col-qty' style={{fontSize:12}}>{x.box_qty||1}</td>
  if(col.id==='unit')return <td key={col.id} className='col-qty' style={{fontSize:12}}>{x.unit||'瓶'}</td>
  if(col.id==='weight')return <td key={col.id} className='col-qty' style={{fontSize:12}}>{x.weight||'-'}</td>
  if(col.id==='volume')return <td key={col.id} className='col-qty' style={{fontSize:12}}>{x.volume||'-'}</td>
  if(col.id==='batch_days')return <td key={col.id} className='col-qty' style={{fontSize:12}}>{x.batch_days||'-'}</td>
  if(col.id==='status')return <td key={col.id}><span className={'pill '+(x.status==='active'?'success':'warning')}>{x.status==='active'?'在售':'停用'}</span></td>
  return <td key={col.id} className="small muted" style={{fontSize:11}}>-</td>
})}
</tr>)}
</tbody></table>
        {pgTotal > 0 && list.length < pgTotal && (
          <div style={{textAlign:'center',padding:'10px 0'}} ref={function(el){
            if (el && !el._obs) {
              el._obs = new IntersectionObserver(function(entries){
                if (entries[0].isIntersecting && !loadingMore) { const np=pgRef.current+1; pgRef.current=np; loadProd(np) }
              }, {rootMargin: '200px'})
              el._obs.observe(el)
            }
          }}><span className="btn btn-ghost" style={{fontSize:12,padding:'6px 16px',cursor:'pointer'}}>{loadingMore ? '加载中... ' : ''}({Math.min(list.length, pgTotal)}/{pgTotal})</span></div>
        )}
        {pgTotal > 0 && list.length >= pgTotal && <div style={{textAlign:'center',padding:'10px 0',fontSize:11,color:'var(--muted2)'}}>已加载全部 {pgTotal} 条</div>}
</div>}
</div>}