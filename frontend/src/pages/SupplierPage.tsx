import React,{useEffect, useState} from 'react'
import {api} from '../api/client'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'

import { SUPPLIER_COLS as COLS } from '../components/hammer/configs'
const COL_KEY = () => 'c_cols_suppliers_' + (useAppStore.getState().channel || 'jd')
const getVis=()=>{try{return JSON.parse(localStorage.getItem(COL_KEY())||'null')}catch{return null}}

function Skeleton(){return <div>{[1,2,3].map(i=><div key={i} style={{display:'flex',gap:8,padding:'8px 0',borderBottom:'1px solid var(--border)'}}>
  <div className="skeleton" style={{width:60,height:14}}/><div className="skeleton" style={{flex:1,height:14}}/>
  <div className="skeleton" style={{width:50,height:14}}/><div className="skeleton" style={{width:40,height:14}}/>
</div>)}</div>}

import { useAppStore } from '../store/useAppStore'
import { t } from "../locale"
export default function SupplierPage(){const[list,setList]=useState([]);const[ld,setLd]=useState(true);const[loadErr,setLoadErr]=useState('')
const[visCols,setVisCols]=useState(()=>getVis(COL_KEY())||COLS.map(c=>c.id))
const { channelVersion, hammerCols, hammerSearch } = useAppStore()
const loadSuppliers = () => {
    setLd(true)
    api.get('/api/suppliers?channel=' + (useAppStore.getState().channel || 'jd')).then(r=>{const d=r.data?.items||r.data||[];setList(d);setLoadErr('');setLd(false)}).catch(()=>{setLd(false);setList([]);setLoadErr('加载失败，可能是网络异常或服务暂不可用')})
  }
  useEffect(()=>{ loadSuppliers() },[channelVersion])
useEffect(() => { if (hammerCols?.suppliers) setVisCols(hammerCols.suppliers) }, [hammerCols])
if(ld)return<div className='card'><div className='section-title'><span>{t("nav.suppliers")}</span></div><Skeleton/></div>
const s = (hammerSearch || '').toLowerCase()
const fl=s?list.filter(x=>(x.supplier_name||x.name||'').toLowerCase().includes(s)||(x.supplier_code||x.code||'').toLowerCase().includes(s)||(x.contact_person||'').toLowerCase().includes(s)||(x.contact_phone||x.phone||'').toLowerCase().includes(s)||(x.mobile||'').toLowerCase().includes(s)):list
return<div className='card'><div className='section-title' style={{display:'flex',flexWrap:'wrap',gap:6,alignItems:'center'}}>
  <span>供应商管理 <span className='small muted'>{t("common.total")} {list.length} {t("common.items")}</span></span>
</div>
{fl.length===0?(loadErr?<ErrorRetry error={loadErr} onRetry={loadSuppliers}/>:<EmptyState icon='factory' title={s?t("supplier.empty_matched"):t("supplier.empty")} desc={s?'换个关键词试试':'通过清洗页导入供应商数据'} action={!s&&<button className="btn btn-primary" onClick={()=>window.__setPage&&window.__setPage('cleansing')}>去导入数据 →</button>}/>):<div style={{overflow:'auto',maxHeight:"calc(100vh - 180px)"}}>
<div style={{fontSize:'var(--font-xs)',color:'var(--muted2)',marginBottom:4}}>{t("common.showing")} {visCols.length}/{COLS.length} {t("common.columns")}</div>
<table><colgroup>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);return col?<col key={col.id} />:null})}</colgroup>
<thead style={{position:"sticky",top:0,background:"var(--card)",zIndex:1}}><tr>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);return col?<th key={col.id}>{col.label}</th>:null})}</tr></thead>
<tbody>{fl.map(x=><tr key={x._key||x.id}>{visCols.map(id=>{const col=COLS.find(c=>c.id===id);if(!col)return <td key={col.id} className="small muted" style={{fontSize:'var(--font-xs)'}}>-</td>;
  if(col.id==='brand')return <td key={col.id} style={{fontSize:'var(--font-sm)'}}>{x.brand||'-'}</td>
  if(col.id==='code')return <td key={col.id} className='mono col-sku'>{x.supplier_code||x.code}</td>
  if(col.id==='name')return <td key={col.id} className='col-name'>{x.supplier_name}</td>
  if(col.id==='contact')return <td key={col.id} className='col-store'>{x.contact_person}</td>
  if(col.id==='phone')return <td key={col.id} className='col-store'>{x.contact_phone||x.phone}</td>
  if(col.id==='score')return <td key={col.id} className='col-price'><span className={'pill '+(x.score>3?'success':'warning')}>{x.score}/5</span></td>
  return <td key={col.id} className="small muted" style={{fontSize:'var(--font-xs)'}}>-</td>
})}</tr>)}</tbody></table></div>}</div>}