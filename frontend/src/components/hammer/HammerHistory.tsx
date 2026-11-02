import React, { useState, useEffect, useMemo } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { api } from '../../api/client'
import { t } from '../../locale'

/**
 * 历史归档页锤子菜单(2026-11-02 S2-9; 同日 UI 收敛: 去按钮/短文案/间距对齐看板)
 *
 * 历史页的时间区间/粒度控件收敛于此, 布局对齐 HammerDashboard:
 *   hammer-header → 区间摘要(右对齐日期) → segmented(区间) → panel(自定义)
 *   → segmented(粒度) → footer(归档状态, 2026-11-02 从页面卡片移入)
 *
 * 间距规范(与看板逐项对齐):
 *   两行摘要标签统一样式(muted2 text-10 mb-4), segmented 与下一组标签间 marginTop 12,
 *   自定义面板沿用 hammer-panel 既有节奏 —— 不再各写一套 margin。
 *
 * 状态落 store(histPreset/histStart/histEnd/histBy), 页面与锤子同源读写;
 * 自定义日期是组件本地草稿, 点「确认」才 commit(避免逐字段触发重查/取消污染区间)。
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
  // 归档状态: 页面不再展示, 移到这里(菜单底部)。面板是"用时才挂载"的, 所以每次打开
  // 拉一次 —— 与页面同接口、同 300s 缓存键, 不会多打一次库。
  const [cov, setCov] = useState<any>(null)

  // 从外部进来时草稿要反映当前生效区间(否则看到上次残留草稿)
  useEffect(() => {
    setStartDraft(histStart)
    setEndDraft(histEnd)
  }, [histStart, histEnd])

  useEffect(() => {
    let alive = true
    api
      .get(`/api/history/coverage?channel=${encodeURIComponent(channel || 'jd')}`)
      .then(r => {
        if (alive) setCov(r?.data || null)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [channel])

  const valid = !!startDraft && !!endDraft && startDraft <= endDraft
  const hasArchive = Number(cov?.archive?.rows || 0) > 0
  // 与后端 _MAX_SPAN 对齐: 超过 400 天会被截断到最近 400 天(前端只做提示, 不裁剪)
  const spanDays = useMemo(() => {
    if (!histStart || !histEnd) return 0
    const a = Date.parse(histStart + 'T00:00:00Z')
    const b = Date.parse(histEnd + 'T00:00:00Z')
    if (isNaN(a) || isNaN(b) || b < a) return 0
    return Math.round((b - a) / 86400000) + 1
  }, [histStart, histEnd])

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
      {/* 2026-11-02 S2-9: 400 天截断提示从页面移到这里 —— 页面头部已无区间文案,
          这条"你选的范围被截短了"必须有个地方说, 否则用户会以为数据缺了。
          只在实际跨度 >400 天时出现, 放摘要下方一行 10px 灰字, 不占分段控件位置。 */}
      {spanDays > 400 ? <div className="hist-clamp-hint">{t('hist.clamped_hint')}</div> : null}

      {/* 短文案(7天/30天/90天/自定义): 4 段在 390 视口内一行放下, 不再截断换行 */}
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
                setHistPreset(p.id) // store 内部同步回写 start/end
              }
            }}
            className={'hammer-segment' + (histPreset === p.id ? ' active' : '')}
          >
            {p.label}
          </span>
        ))}
      </div>

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
              {t('hist.range_invalid')}
            </div>
          )}
        </div>
      )}

      {/* 粒度标签与区间摘要同构(muted2 text-10 mb-4 + marginTop 12 分组间距) */}
      <div className="flex items-center gap-6 muted2 text-10 mb-4" style={{ marginTop: 12 }}>
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

      {/* 归档状态(2026-11-02 从页面卡片移入菜单底部)
          间距: 上 12(与前一组 segmented 的分组间距一致) / 下 4(面板内容贴底留一口气) /
          左右 2(与自定义面板里 "起始日期" 标签的左右缩进对齐, 不做成满宽顶格) */}
      <div className="hist-arc">
        <div className="hist-arc-row">
          <span className="hist-arc-k">{t('hist.archive_state')}</span>
          {!cov ? (
            <span className="hist-arc-v hist-arc-muted">…</span>
          ) : hasArchive ? (
            <span className="hist-arc-v hist-arc-on">{t('hist.archive_on')}</span>
          ) : (
            <span className="hist-arc-v hist-arc-off">{t('hist.archive_none')}</span>
          )}
        </div>
        {cov ? (
          <div className="hist-arc-sub">
            {hasArchive
              ? t('hist.archive_cover')
                  .replace('{a}', cov.archive?.min || '-')
                  .replace('{b}', cov.archive?.max || '-')
              : t('hist.archive_day')
                  .replace('{d}', cov.archive_day || '-')
                  .replace('{n}', String(cov.cut_days || 365))}
          </div>
        ) : null}
        {cov?.stale_orders ? (
          <div className="hist-arc-sub">
            {t('hist.archive_stale').replace('{n}', String(cov.stale_orders))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
