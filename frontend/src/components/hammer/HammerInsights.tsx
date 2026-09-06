import React, { useState, useEffect } from "react"
import { t } from "../../locale"
import { useAppStore } from "../../store/useAppStore"
import { useToast } from "../../components/Toast"
import { INS_BBCC_COLS, INS_TRAD_COLS, INS_PURCHASE_COLS, INS_SLOW_COLS, insColKey, getInsVis, insDefVis, insDefVisTrad } from "./configs"
import { IconExport } from "../Icons"
import { api } from '../../api/client' 
interface HammerInsightsProps { channel: string }

export default function HammerInsights({ channel }: HammerInsightsProps) {
  const { hammerPanel, setHammerPanel, setHammerCols, hammerInsightsTab, setHammerInsightsTab, hammerReplenMode, setHammerReplenMode, hammerData, setHammerData, prodBatch, setProdBatch, prodSelIds, setProdBatchSel, requestProdBatchAll } = useAppStore()
  const toast = useToast()
  const [bpOpen, setBpOpen] = useState(false)
  const [bpAction, setBpAction] = useState('mark')
  const [bpNote, setBpNote] = useState('')
  const [bpBusy, setBpBusy] = useState(false)
  const mode = (channel !== 'jd' && hammerReplenMode === 'bbcc') ? 'traditional' : hammerReplenMode
  const isPurchase = hammerInsightsTab === 'purchase'
  const isSlow = hammerInsightsTab === 'slow'
  const cols = isSlow ? INS_SLOW_COLS : (isPurchase ? INS_PURCHASE_COLS : (mode === 'bbcc' ? INS_BBCC_COLS : INS_TRAD_COLS))
  const [visCols, setVisCols] = useState(() => {
    if (isSlow) return INS_SLOW_COLS.map(c => c.id)
    if (isPurchase) return INS_PURCHASE_COLS.map(c => c.id)
    return getInsVis(mode, channel) || (mode==='bbcc'?insDefVis(INS_BBCC_COLS):insDefVisTrad(INS_TRAD_COLS))
  })
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    if (isSlow) {
      const saved = JSON.parse(localStorage.getItem('c_cols_' + channel + '_slow') || 'null')
      const cols = saved || INS_SLOW_COLS.map(c => c.id)
      setVisCols(cols); setHammerCols('insights_' + channel + '_slow', cols)
    } else if (isPurchase) {
      const saved = JSON.parse(localStorage.getItem('c_cols_' + channel + '_purchase') || 'null')
      const cols = saved || INS_PURCHASE_COLS.map(c => c.id)
      setVisCols(cols); setHammerCols('insights_' + channel + '_purchase', cols)
    } else {
      const saved = getInsVis(mode, channel) || (mode==='bbcc'?insDefVis(INS_BBCC_COLS):insDefVisTrad(INS_TRAD_COLS))
      setVisCols(saved); setHammerCols('insights_' + mode, saved)
    }
  }, [mode, hammerInsightsTab, channel])

  const saveCols = (c) => {
    setVisCols(c)
    if (isSlow) {
      localStorage.setItem('c_cols_' + channel + '_slow', JSON.stringify(c))
      setHammerCols('insights_' + channel + '_slow', c)
    } else if (isPurchase) {
      localStorage.setItem('c_cols_' + channel + '_purchase', JSON.stringify(c))
      setHammerCols('insights_' + channel + '_purchase', c)
    } else {
      localStorage.setItem(insColKey(mode, channel), JSON.stringify(c))
      setHammerCols('insights_' + mode, c)
    }
  }

  const doExport = async (type) => {
    setExporting(true)
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      const _mode = window.__hammerReplenMode || 'bbcc'
      const _type = type === 'slow' ? 'slow' : type === 'purchase' ? 'purchase_suggestions' : type === 'replen' ? 'replen' : 'purchase'
      const r = await fetch(API + '/api/exports?type=' + _type + '&mode=' + _mode + '&channel=' + channel, 
        {method:'POST', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok && d.task_id) {
        toast.add({type:'success', title:'导出任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
      } else {
        throw new Error(d.error || '提交失败')
      }
    } catch(e) { toast.error('导出失败: ' + e.message) }
    setExporting(false)
  }

  // 批量处置(规则页同款批量模式, 复用 prodBatch/prodSelIds; 后端 disposal_records 持久)
  const runDispose = async () => {
    const s = useAppStore.getState()
    const ids = s.prodSelIds || []
    if (ids.length === 0) { toast.error('请先勾选要处置的项'); return }
    setBpBusy(true)
    try {
      const items = ids.map(k => { const parts = k.split('|'); return { sku: parts[0], warehouse: parts[1] } })
      await api.post('/api/disposals/batch', { channel, action: bpAction, note: bpNote, items })
      toast.success('已处置 ' + items.length + ' 项')
      s.setProdBatch(false); s.setProdBatchSel([]); setBpOpen(false)
      window.dispatchEvent(new Event('insights-refresh'))
    } catch(e) { toast.error('处置失败: ' + (e.message||'')) }
    setBpBusy(false)
  }
  return (
    <div>
      <div className="hammer-header">{channel === 'jd' ? '京东' : '其他'} · 建议</div>
      {/* tab 入口 */}
      <div className="hammer-segmented" style={{marginBottom:4}}>
        {[['replen','补货建议'],['purchase','采购建议'],['slow','滞销预警']].map(([id,label]) => (
          <span key={id} onClick={() => setHammerInsightsTab(id)}
            className={'hammer-segment' + (hammerInsightsTab === id ? ' active' : '')}>
            {label}
          </span>
        ))}
      </div>
      {/* 补货模式行（单独一行） */}
      {hammerInsightsTab === 'replen' && (
        <div className="hammer-segmented" style={{marginBottom:8}}>
          {channel === 'jd' && (
            <span onClick={() => setHammerReplenMode('bbcc')}
              className={'hammer-segment' + (mode==='bbcc' ? ' active' : '')}>
              BBCC
            </span>
          )}
          <span onClick={() => setHammerReplenMode('traditional')}
            className={'hammer-segment' + (mode==='traditional' ? ' active' : '')}>
            传统多仓
          </span>
        </div>
      )}
      {/* 操作行：搜索+列选择一行，导出单独一行 */}
      <div style={{display:'flex',flexDirection:'column',gap:6}}>
        <div className="hammer-row-2">
          <button onClick={() => setHammerPanel(hammerPanel === 'search' ? null : 'search')}
            className="hammer-btn btn-ghost">搜索</button>
          <button onClick={() => setHammerPanel(hammerPanel === 'columns' ? null : 'columns')}
              className="hammer-btn btn-ghost">
              列选择 ({visCols.length}/{cols.length})
            </button>
        </div>
        <button onClick={() => doExport(
          isSlow ? 'slow' : (isPurchase ? 'purchase' : 'replen')
        )} disabled={exporting} className="clickable hammer-btn btn-ghost" style={{opacity:exporting?0.5:1}}>
          {exporting ? <span className="hammer-spinner" /> : <IconExport size={13} />} {exporting ? '导出中...' : '导出'}
        </button>
        {isSlow && <button onClick={() => setBpOpen(!bpOpen)}
          className="hammer-btn btn-ghost" style={{borderColor: useAppStore.getState().prodBatch ? 'var(--danger)' : undefined, color: useAppStore.getState().prodBatch ? 'var(--danger)' : undefined}}>批量处置</button>}
      </div>
      {/* 批量处置面板(规则页同款批量模式) */}
      {isSlow && bpOpen && (
        <div className="hammer-panel">
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
            <span className="text-12 muted2">已选 <b style={{color:'var(--text)'}}>{useAppStore.getState().prodSelIds?.length || 0}</b> 项</span>
            {useAppStore.getState().prodBatch ? (
              <button className="hammer-clear" onClick={() => { useAppStore.getState().setProdBatch(false); useAppStore.getState().setProdBatchSel([]) }}>退出批量模式</button>
            ) : (
              <button className="hammer-clear" onClick={() => useAppStore.getState().setProdBatch(true)}>进入批量模式</button>
            )}
          </div>
          <div className="hammer-btn-row">
            <button className="hammer-btn btn-ghost" onClick={() => { const s = useAppStore.getState(); if (!s.prodBatch) s.setProdBatch(true); s.requestProdBatchAll() }}>全选/取消</button>
          </div>
          <div className="muted2 text-10" style={{marginTop:6}}>滞销预警按 SKU×仓库 · 处置后该行标记「已处理」</div>
          <div className="hammer-btn-row" style={{marginTop:8}}>
            {[['mark','标记已处理','var(--success)'],['return','退货供应商','#f59e0b'],['clearance','清仓甩卖','#8b5cf6'],['promo','降价促销','#06b6d4']].map(([v,l,c]) => (
              <button key={v} className="hammer-btn btn-ghost" style={{color:c, opacity:(bpBusy||(useAppStore.getState().prodSelIds||[]).length===0)?0.4:1, borderColor: bpAction===v?c:undefined}}
                disabled={bpBusy||(useAppStore.getState().prodSelIds||[]).length===0} onClick={()=>setBpAction(v)}>{l}</button>
            ))}
          </div>
          <div style={{marginTop:8}}>
            <input value={bpNote} onChange={e=>setBpNote(e.target.value)} placeholder="备注(可选)" className="hammer-input" />
          </div>
          <div className="hammer-btn-row" style={{marginTop:8}}>
            <button disabled={bpBusy} className="hammer-btn btn-primary" onClick={runDispose}>{bpBusy ? '处理中...' : '执行处置'}</button>
          </div>
        </div>
      )}
      {/* 搜索面板 */}
      {hammerPanel === 'search' && (
        <div className="hammer-panel">
          <div className="hammer-header">
            {hammerInsightsTab === 'replen' ? '补货建议' : hammerInsightsTab === 'purchase' ? '采购建议' : '滞销预警'}
            {isPurchase ? '' : mode === 'bbcc' ? ' (BBCC)' : ' (传统)'} · 搜索
          </div>
          <input id="hm-search-insights" value={hammerData?.[channel]?.['insights_search_' + (isPurchase ? 'purchase' : isSlow ? 'slow' : mode)] || ''}
            onChange={e => setHammerData('insights_search_' + (isPurchase ? 'purchase' : isSlow ? 'slow' : mode), e.target.value)}
            placeholder="搜索SKU/商品名..." className="hammer-input" />
          {hammerData?.[channel]?.['insights_search_' + (isPurchase ? 'purchase' : isSlow ? 'slow' : mode)] && (
            <div style={{marginTop:4,textAlign:'center'}}>
              <button className="hammer-clear" onClick={() => setHammerData('insights_search_' + (isPurchase ? 'purchase' : isSlow ? 'slow' : mode), '')}>{t("common.clear")}</button>
            </div>
          )}
        </div>
      )}
      {/* 列选择面板 */}
      {hammerPanel === 'columns' && (
        <div className="hammer-panel hammer-panel-scroll">
          <div className="cols-top-bar">
            <button onClick={()=>saveCols(cols.map(c=>c.id))} className="hammer-clear">{t("common.all")}</button>
            <button onClick={()=>saveCols([])} className="hammer-clear">取消全选</button>
            <button onClick={()=>saveCols(isSlow ? INS_SLOW_COLS.map(c=>c.id) : (isPurchase ? INS_PURCHASE_COLS.map(c=>c.id) : (mode==='bbcc'?insDefVis(INS_BBCC_COLS):insDefVisTrad(INS_TRAD_COLS))))} className="hammer-clear">{t("common.default")}</button>
          </div>
          <div className="muted2 text-10" style={{marginBottom:2,padding:'0 4px'}}>{t("common.drag_hint")}</div>
          {visCols.length > 0 && <div className="cols-group-title"><span>已显示</span><span>{visCols.length}</span></div>}
          {visCols.map(id=>{
            const col=cols.find(c=>c.id===id);if(!col)return null
            return <div key={col.id} draggable
              onDragStart={e=>{e.dataTransfer.setData('text/plain',col.id);e.target.style.opacity='0.4';e.currentTarget.parentNode._dragId=col.id}}
              onDragEnd={e=>e.target.style.opacity='1'}
              onDragOver={e=>{e.preventDefault();e.currentTarget.style.borderTop='2px solid var(--primary)';const from=e.currentTarget.parentNode._dragId;if(from&&from!==col.id){const nxt=visCols.filter(c=>c!==from);const toIdx=nxt.indexOf(col.id);nxt.splice(toIdx,0,from);saveCols(nxt)}}}
              onDragLeave={e=>e.currentTarget.style.borderTop='1px solid transparent'}
              onDrop={e=>{e.preventDefault();e.currentTarget.style.borderTop='1px solid transparent';const from=e.dataTransfer.getData('text/plain');if(from===col.id)return;const nxt=visCols.filter(c=>c!==from);const toIdx=nxt.indexOf(col.id);nxt.splice(toIdx,0,from);saveCols(nxt);e.currentTarget.parentNode._dragId=null}}
              className="col-drag visible">
              <span className="muted2 text-12" style={{width:16,flexShrink:0,textAlign:'center',cursor:'grab'}}>⠿</span>
              <input type="checkbox" checked onChange={e=>{saveCols(visCols.filter(c=>c!==col.id))}} className="accent-primary" />
              <span className="flex-1 text-12">{col.label || '(序号)'}</span>
              <span className="muted2 text-9">#{visCols.indexOf(col.id)+1}</span>
            </div>
          })}
          {(()=>{
            const hidden=cols.filter(c=>!visCols.includes(c.id))
            if(hidden.length===0)return null
            return <>
              <div className="cols-group-title"><span>已隐藏</span><span>{hidden.length}</span></div>
              {hidden.map(col=>
                <div key={col.id} className="col-drag hidden">
                  <span className="muted2" style={{width:16,flexShrink:0,textAlign:'center'}}>○</span>
                  <input type="checkbox" onChange={e=>{saveCols([...visCols,col.id])}} className="accent-primary" />
                  <span className="flex-1 text-12">{col.label || '(序号)'}</span>
                </div>
              )}
            </>
          })()}
        </div>
      )}
    </div>
  )
}

/* 清洗页: 锤子菜单渠道标注 */
