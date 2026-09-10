import React, { useState } from "react"
import { useAppStore } from "../../store/useAppStore"
import { IconClipboard } from "../Icons"
import { t } from "../../locale"
export default function HammerDashboard({ channel }) {
  const { hammerDashPeriod, setHammerDashPeriod, setCustomDate, customDateStart, customDateEnd, dashboard } = useAppStore()
  const periodLabel = { today: t('period.today'), week: t('period.week'), month: t('period.month'), custom: t('period.custom') }
  const periodMeta = dashboard?.periods?.[hammerDashPeriod] || {}
  const [showCustom, setShowCustom] = useState(false)
  const [startVal, setStartVal] = useState(customDateStart || '')
  const [endVal, setEndVal] = useState(customDateEnd || '')

  if (!startVal) { const d = new Date(); d.setDate(d.getDate() - 29); setStartVal(d.toISOString().slice(0,10)) }
  if (!endVal) { setEndVal(new Date().toISOString().slice(0,10)) }

  return (
    <div>
      <div className="hammer-header">{channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.dash')}</div>
      <div className="flex items-center gap-6 muted2 text-10 mb-4 flex-wrap">
        {t('dash.period_label')}
        {hammerDashPeriod === 'custom' && customDateStart && customDateEnd &&
          <span className="font-600 text-11" style={{marginLeft:'auto',color:'var(--primary)'}}>{customDateStart.slice(5)}/{customDateEnd.slice(5)}</span>}
        {periodMeta.date && <span style={{marginLeft:'auto'}}>{periodMeta.date}</span>}
      </div>
      <div className="hammer-segmented">
        {['today','week','month','custom'].map(k => (
          <span key={k} onClick={() => {
            if (k === 'custom') setShowCustom(!showCustom)
            else { setShowCustom(false); setHammerDashPeriod(k) }
          }}
            className={'hammer-segment' + ((k === 'custom' ? hammerDashPeriod === 'custom' : hammerDashPeriod === k) ? ' active' : '')}>
            {periodLabel[k]}
          </span>
        ))}
      </div>
      {showCustom && <div className="hammer-panel overflow-hidden">
        <div className="mb-8">
          <div className="muted2 text-10 mb-3" style={{padding:'0 2px'}}>{t('common.start_date')}</div>
          <input type="date" value={startVal} onChange={e=>setStartVal(e.target.value)} className="hammer-input text-14" style={{padding:'6px 10px'}} />
        </div>
        <div className="mb-8">
          <div className="muted2 text-10 mb-3" style={{padding:'0 2px'}}>{t('common.end_date')}</div>
          <input type="date" value={endVal} onChange={e=>setEndVal(e.target.value)} className="hammer-input text-14" style={{padding:'6px 10px'}} />
        </div>
        <button onClick={() => {
          if (startVal && endVal && startVal <= endVal) { setCustomDate(startVal, endVal); setShowCustom(false) }
        }} className="btn btn-primary w-full text-12" style={{minHeight:32,padding:'9px 12px',minHeight:32}}>{t('common.confirm')}</button>
      </div>}
      <div className="hammer-btn-row" style={{marginTop:12}}>
        <button onClick={() => { try { window.__setPage('tasks') } catch {} }}
          className="hammer-btn btn-primary"><IconClipboard size={14} /> 任务管理</button>
      </div>
    </div>
  )
}