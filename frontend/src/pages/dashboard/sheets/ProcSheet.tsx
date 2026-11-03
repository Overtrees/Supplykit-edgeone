import { createPortal } from 'react-dom'
import { fmtInt } from '../format'

interface Props {
  open: boolean
  onClose: () => void
  procTotal: number
  procList: any[]
  onGoInsights?: (tab: string, sku?: string) => void
}

export default function ProcSheet(props: Props) {
  const { open, onClose, procTotal, procList, onGoInsights } = props
  if (!open) return null
  return createPortal(
    <>
      <div
        onClick={function () {
          onClose()
        }}
        style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }}
      />
      <div
        style={{
          position: 'fixed',
          left: 0,
          right: 0,
          bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
          zIndex: 9999,
          display: 'flex',
          justifyContent: 'center',
          padding: '0 14px',
          pointerEvents: 'none',
        }}
      >
        <div
          onClick={function (e) {
            e.stopPropagation()
          }}
          className="material-regular"
          style={{
            width: '100%',
            maxWidth: 600,
            borderRadius: 'var(--radius-lg)',
            padding: '18px 14px calc(14px + env(safe-area-inset-bottom))',
            boxShadow: 'var(--shadow-sheet)',
            pointerEvents: 'auto',
            maxHeight: '70vh',
            overflowY: 'auto',
          }}
        >
          <div
            style={{
              fontSize: 'var(--font-18)',
              fontWeight: 700,
              marginBottom: 12,
              textAlign: 'center',
              color: 'var(--text)',
            }}
          >
            采购&补货告警 · 共 {fmtInt(procTotal)} 条
          </div>
          {(procList || []).map(function (x, i) {
            return (
              <div
                key={i}
                onClick={function () {
                  onGoInsights && onGoInsights(x.tag === '采购' ? 'purchase' : 'replen', x.sku)
                }}
                className="clickable"
                style={{
                  padding: '8px 12px',
                  background: 'var(--card)',
                  borderRadius: 'var(--radius-md)',
                  marginBottom: 6,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 8,
                    alignItems: 'flex-start',
                    marginBottom: 2,
                  }}
                >
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      fontWeight: 600,
                      fontSize: 'var(--font-sm)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    <span
                      className={'pill ' + (x.tag === '采购' ? 'warning' : 'danger')}
                      style={{
                        flexShrink: 0,
                        fontSize: 'var(--font-9)',
                        padding: '1px 6px',
                        minHeight: 'auto',
                        lineHeight: '16px',
                      }}
                    >
                      {x.tag}
                    </span>
                    {x.product_name || x.sku}
                  </span>
                  {x.qty > 0 && (
                    <span
                      style={{
                        flexShrink: 0,
                        fontWeight: 700,
                        color: x.tag === '采购' ? 'var(--warning)' : 'var(--danger)',
                      }}
                    >
                      +{x.qty}
                    </span>
                  )}
                </div>
                <div className="small muted" style={{ fontSize: 'var(--font-10)' }}>
                  {x.sku}
                  {x.wh ? ' · ' + x.wh : ''} ·{' '}
                  {x.note
                    ? String(x.note).slice(0, 50)
                    : x.days_to_empty > 999
                      ? '库存充足'
                      : '可撑' + x.days_to_empty + '天'}
                </div>
              </div>
            )
          })}
          <div
            onClick={function () {
              onClose()
            }}
            className="clickable sheet-close"
            style={{ marginTop: 8 }}
          >
            <span style={{ fontSize: 'var(--font-15)', fontWeight: 600, color: '#fff' }}>关闭</span>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
