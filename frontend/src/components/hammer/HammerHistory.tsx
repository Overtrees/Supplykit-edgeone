import React, { useState, useEffect } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { t } from '../../locale'

/**
 * 历史归档页锤子菜单(2026-11-02 S2-9)
 *
 * 原历史页把「时间区间 / 粒度 / 自定义日期」三组控件全铺在页面卡片里, 占用首屏
 * 高度且与看板页锤子菜单的交互心智不一致(看板的时间也是在锤子里选的)。本组件把
 * 这些控件收敛到锤子菜单, 布局照抄 HammerDashboard:
 *   hammer-header(标题) → 当前区间摘要 → hammer-segmented(区间) → hammer-panel(自定义)
 *   → hammer-segmented(粒度) → hammer-btn-row(跳转归档页)
 *
 * 状态**不放组件本地**, 全部落 store(histPreset/histStart/histEnd/histBy):
 * 锤子面板与页面读写同一个 store, 关掉锤子后页面立刻按新条件重查 —— 这是"交互联动"
 * 的关键。若放组件本地, 锤子面板卸载即丢, 页面拿不到值。
 *
 * 唯一的例外是**自定义日期草稿**(startDraft/endDraft): 它是"尚未确认"的输入,
 * 不能直接写 store —— 否则用户每敲一个字(选一天)页面就重查一次, 且取消/关面板后
 * 页面已经跑到了没确认的区间。点「确认」才 commit。
 */
export default function HammerHistory({ channel }: { channel: string }) {
  const histPreset = useAppStore(s => s.histPreset)
  const histStart = useAppStore(s => s.histStart)
  const histEnd = useAppStore(s => s.histEnd)
  const histBy = useAppStore(s => s.histBy)
  const setHistPreset = useAppStore(s => s.setHistPreset)
  const setHistRange = useAppStore(s => s.setHistRange)
  const setHistBy = useAppStore(s => s.setHistBy)

  const [showCustom, setShowCustom] = useState(histPreset === 'custom')
  const [startDraft, setStartDraft] = useState(histStart)
  const [endDraft, setEndDraft] = useState(histEnd)

  // 从页面「调整」进来时, 草稿要反映当前生效区间(否则看到的是上次残留的草稿)
  useEffect(() => {
    setStartDraft(histStart)
    setEndDraft(histEnd)
  }, [histStart, histEnd])

  const valid = !!startDraft && !!endDraft && startDraft <= endDraft

  return (
    <div>
      <div className="hammer-header">
        {channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.history')}
      </div>

      <div className="flex items-center gap-6 muted2 text-10 mb-4 flex-wrap">
        {t('hist.range_label')}
        <span className="font-600 text-11" style={{ marginLeft: 'auto', color: 'var(--primary)' }}>
          {histStart.slice(5)}/{histEnd.slice(5)}
        </span>
      </div>

      {/* 区间预设: 与看板同款 hammer-segmented, 选中态全靠 .active */}
      <div className="hammer-segmented">
        {[
          { id: '7d', label: t('hist.preset.7d') },
          { id: '30d', label: t('hist.preset.30d') },
          { id: '90d', label: t('hist.preset.90d') },
          { id: 'custom', label: t('hist.preset.custom') },
        ].map(p => (
          <span
            key={p.id}
            onClick={() => {
              if (p.id === 'custom') {
                setShowCustom(v => !v)
                if (!showCustom) {
                  setStartDraft(histStart)
                  setEndDraft(histEnd)
                }
              } else {
                setShowCustom(false)
                setHistPreset(p.id) // store 内部会把 start/end 一并算好(PRESET_DAYS)
              }
            }}
            className={'hammer-segment' + (histPreset === p.id ? ' active' : '')}
          >
            {p.label}
          </span>
        ))}
      </div>

      {/* 自定义区间: 与看板同构(panel 内两个 date + 确认); 输入只改草稿, 确认才提交 */}
      {showCustom && (
        <div className="hammer-panel overflow-hidden">
          <div className="mb-8">
            <div className="muted2 text-10 mb-3" style={{ padding: '0 2px' }}>
              {t('common.start_date')}
            </div>
            <input
              type="date"
              value={startDraft}
              onChange={e => setStartDraft(e.target.value)}
              className="hammer-input text-14"
              style={{ padding: '6px 10px' }}
            />
          </div>
          <div className="mb-8">
            <div className="muted2 text-10 mb-3" style={{ padding: '0 2px' }}>
              {t('common.end_date')}
            </div>
            <input
              type="date"
              value={endDraft}
              onChange={e => setEndDraft(e.target.value)}
              className="hammer-input text-14"
              style={{ padding: '6px 10px' }}
            />
          </div>
          <button
            onClick={() => {
              // 非法区间(起 > 止)不提交: 后端会 clamp, 但用户应当立刻看到没生效
              if (!valid) return
              setHistRange(startDraft, endDraft, true)
              setShowCustom(false)
            }}
            disabled={!valid}
            className="btn btn-primary w-full text-12"
            style={{ minHeight: 32, padding: '9px 12px', opacity: valid ? 1 : 0.5 }}
          >
            {t('common.confirm')}
          </button>
          {!valid && (
            <div className="muted2 text-10" style={{ marginTop: 6, textAlign: 'center' }}>
              起始日期不能晚于结束日期
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-6 muted2 text-10 mb-3" style={{ marginTop: 12 }}>
        {t('hist.by_label')}
      </div>
      <div className="hammer-segmented">
        {(['day', 'month'] as const).map(k => (
          <span
            key={k}
            onClick={() => setHistBy(k)}
            className={'hammer-segment' + (histBy === k ? ' active' : '')}
          >
            {k === 'day' ? t('hist.by.day') : t('hist.by.month')}
          </span>
        ))}
      </div>

      {/* 操作行: 照抄看板 hammer-btn-row(等宽 flex) */}
      <div className="hammer-btn-row" style={{ marginTop: 12 }}>
        <button
          onClick={() => {
            try {
              window.__setPage && window.__setPage('history')
            } catch {}
          }}
          className="hammer-btn btn-primary"
        >
          {t('hist.open_page')}
        </button>
      </div>
    </div>
  )
}
