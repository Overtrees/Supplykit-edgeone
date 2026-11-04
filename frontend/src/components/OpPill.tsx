import React, { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useAppStore } from '../store/useAppStore'
import { t } from '../locale'

/**
 * 操作态胶囊(2026-11-03)
 *
 * 与「同步中/加载中」同一个位置, 但表达的是另一件事:
 *   同步中 = 前端在拉数据(syncing)
 *   规则重算中 / N 项处理中 = 后端在算/在导(ops)
 *
 * 为什么不能只靠看板那行"规则重算中"文案: 那段文案只有看板挂载时才存在。
 * 用户在规则页点保存、然后切到库存页 —— 过程提示就彻底消失了。
 */

const fmt = (ms: number): string => {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return s + 's'
  const m = Math.floor(s / 60)
  return m + 'm' + String(s % 60).padStart(2, '0') + 's'
}

const Spinner = ({ size = 12 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={3}
    strokeLinecap="round"
    style={{ animation: 'spin 0.9s linear infinite', flexShrink: 0 }}
  >
    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
  </svg>
)

export default function OpPill() {
  const ops = useAppStore(s => s.ops)
  const opDone = useAppStore(s => s.opDone)
  const [open, setOpen] = useState(false)
  // 秒级重绘(只用于"已用时间"): 没有运行中项时不起定时器
  const [, force] = useState(0)
  useEffect(() => {
    if (!ops.length) return
    const _tid = setInterval(() => force(v => v + 1), 1000)
    return () => clearInterval(_tid)
  }, [ops.length])
  // 任务清空时收起面板(渲染期不 setState)
  useEffect(() => {
    if (!ops.length && open) setOpen(false)
  }, [ops.length, open])

  if (!ops.length) return null

  // 文案: 规则重算优先(最常见的等待场景), 其次"几项处理中"
  const rules = ops.filter(o => o.kind === 'rules')
  const label = rules.length
    ? t('op.rules_running')
    : ops.length > 1
    ? t('op.n_processing', { n: ops.length })
      : ops[0].label

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        title={t('op.title')}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          fontSize: 'inherit',
          fontWeight: 'inherit',
          color: 'var(--muted)',
          whiteSpace: 'nowrap',
          background: 'none',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        <Spinner />
        {label}
      </button>
      {open &&
        createPortal(
          <>
            <div
              onClick={() => setOpen(false)}
              style={{ position: 'fixed', inset: 0, zIndex: 4000, background: 'transparent' }}
            />
            <div
              style={{
                position: 'fixed',
                left: 0,
                right: 0,
                bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
                zIndex: 4001,
                display: 'flex',
                justifyContent: 'center',
                padding: '0 14px',
                pointerEvents: 'none',
              }}
            >
              <div
                onClick={e => e.stopPropagation()}
                className="material-regular"
                style={{
                  width: '100%',
                  maxWidth: 600,
                  borderRadius: 'var(--radius-lg)',
                  padding: '16px 14px calc(12px + env(safe-area-inset-bottom))',
                  boxShadow: 'var(--shadow-sheet)',
                  pointerEvents: 'auto',
                  maxHeight: '60vh',
                  overflowY: 'auto',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginBottom: 10,
                  }}
                >
                  <span style={{ fontWeight: "var(--fw-bold)" }}>{t('op.panel_title')}</span>
                  <span className="small muted" style={{ cursor: 'pointer' }}>
                    {t('common.close')}
                  </span>
                </div>
                <div className="small muted" style={{ marginBottom: 10, lineHeight: 1.5 }}>
                  {t('op.panel_hint')}
                </div>
                {ops.map(o => (
                  <div
                    key={o.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '8px 0',
                      borderTop: '1px solid var(--line, rgba(0,0,0,0.06))',
                    }}
                  >
                    <Spinner size={11} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span className="ellipsis" style={{ display: 'block' }}>
                        {t(o.label)}
                        {o.scope ? <span className="small muted"> · {o.scope}</span> : null}
                      </span>
                    </span>
                    <span className="small muted" style={{ flexShrink: 0 }}>
                      {fmt(Date.now() - o.startedAt)}
                    </span>
                    <span
                      className="small muted clickable"
                      style={{ flexShrink: 0, color: 'var(--primary)' }}
                      onClick={() => opDone(o.id)}
                      title={t('op.hide_tip')}
                    >
                      {t('op.hide')}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </>,
          document.body,
        )}
    </>
  )
}
