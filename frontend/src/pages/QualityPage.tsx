import React, { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { t } from "../locale"

const TYPE_LABEL = {
  duplicate_order: '重复订单号',
  duplicate_sku: '重复SKU',
  format_error: '格式错误',
  field_warning: '字段警告',
  field_error: '字段错误',
  mapping_info: '映射信息',
}
const LEVEL_LABEL = { warning: '警告', error: '异常', info: '提示' }
const PAGE_SIZE = 200

export default function QualityPage() {
  const { channelVersion, loading } = useAppStore()
  const [list, setList] = useState([])
  const [total, setTotal] = useState(0)
  const [, setPage] = useState(1)
  const [ld, setLd] = useState(true)
  const [moreLoading, setMoreLoading] = useState(false)
  const pageRef = useRef(1)
  const reqSeq = useRef(0)

  // 分页加载(>200 条不再截断, 底部加载更多)
  const load = (p) => {
    const seq = ++reqSeq.current
    if (p === 1) setLd(true)
    else setMoreLoading(true)
    api.get('/api/quality-logs?page=' + p + '&page_size=' + PAGE_SIZE)
      .then(r => {
        if (seq !== reqSeq.current) { setLd(false); setMoreLoading(false); return }
        const d = r.data || {}
        const items = Array.isArray(d) ? d : (d.items || [])
        const t = Array.isArray(d) ? items.length : (d.total || items.length)
        setList(prev => p === 1 ? items : [...prev, ...items])
        setTotal(t)
        setPage(p); pageRef.current = p
        setLd(false); setMoreLoading(false)
      })
      .catch(() => { if (seq === reqSeq.current) { setLd(false); setMoreLoading(false); setList([]) } })
  }
  useEffect(() => { setList([]); setTotal(0); load(1) }, [channelVersion])

  if (loading && list.length === 0 && ld) return <div className="card"><div className="section-title">{t("nav.quality")}</div><div>{[1,2,3].map(i => <div key={i} className="skeleton" style={{height:36,marginBottom:4}} />)}</div></div>

  const groups = {}
  for (const x of list) {
    const day = (x.created_at || '').slice(0,10) || '未知日期'
    if (!groups[day]) groups[day] = []
    groups[day].push(x)
  }

  const days = Object.entries(groups).reverse()
  const rows = []
  for (const [day, items] of days) {
    rows.push(
      <div key={day} style={{marginBottom:12}}>
        <div style={{fontSize:11,fontWeight:600,color:'var(--muted2)',marginBottom:4}}>{day} · {items.length} 条</div>
        <div style={{overflow:'auto',maxHeight:'calc(100vh - 180px)'}}>
        <table style={{minWidth:400}}><tbody>
          {items.map(x => (
            <tr key={x.id}>
              <td style={{whiteSpace:'nowrap',padding:'5px 6px',width:72,fontSize:12}}>{TYPE_LABEL[x.log_type||x.issue_type] || x.log_type||x.issue_type}</td>
              <td style={{maxWidth:240,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',padding:'5px 6px',fontSize:12}} title={x.message||x.issue_message}>{x.message||x.issue_message}</td>
              <td style={{padding:'5px 6px',width:44}}><span className={'pill '+(x.level==='error'||x.severity==='error'?'danger':x.level==='warning'||x.severity==='warning'?'warning':'info')} style={{fontSize:10}}>{LEVEL_LABEL[x.level||x.severity] || x.level||x.severity}</span></td>
              <td className="mono" style={{fontSize:11,padding:'5px 6px',width:64,color:'var(--muted2)'}}>{(x.created_at||'').slice(11,16) || '-'}</td>
            </tr>
          ))}
        </tbody></table></div>
      </div>
    )
  }

  return <div className="card">
    <div className="section-title">{t("nav.quality")}</div>
    {list.length === 0 ? (
      <div className="small muted" style={{padding:24,textAlign:'center'}}>{t("quality.empty")}</div>
    ) : (
      <>
        {rows}
        {list.length < total && (
          <div className="text-center" style={{padding:'10px 0'}}>
            <button className="btn btn-ghost" style={{fontSize:12,padding:'6px 16px',cursor:'pointer'}}
              onClick={() => load(pageRef.current + 1)} disabled={moreLoading}>
              {moreLoading ? '加载中...' : `加载更多 (${list.length}/${total})`}
            </button>
          </div>
        )}
      </>
    )}
  </div>
}
