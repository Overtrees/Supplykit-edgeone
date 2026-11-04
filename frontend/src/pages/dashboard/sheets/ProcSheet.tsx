import { createPortal } from 'react-dom'
import { fmtInt } from '../format'
import { t } from '../../../locale'

/** 采购/补货明细行: 已知字段精确声明, 动态扩展字段 unknown(索引签名兜底) */
interface ProcRow {
  sku?: string
  product_name?: string
  kind?: string
  qty?: number
  note?: string
  wh?: string
  days_to_empty?: number
  [k: string]: unknown
}

interface Props {
  open: boolean
  onClose: () => void
  procTotal: number
  procList: ProcRow[]
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
              fontWeight: 'var(--fw-bold)',
              marginBottom: 12,
              textAlign: 'center',
              color: 'var(--text)',
            }}
          >
            {t('dash.proc_alert_count', { n: fmtInt(procTotal) })}
          </div>
          {(procList || []).map(function (x, i) {
            return (
              <div
                key={i}
                onClick={function () {
                  onGoInsights && onGoInsights(x.kind === 'purchase' ? 'purchase' : 'replen', x.sku)
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
                      fontWeight: 'var(--fw-semibold)',
                      fontSize: 'var(--font-sm)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    <span
                      className={'pill ' + (x.kind === 'purchase' ? 'warning' : 'danger')}
                      style={{
                        flexShrink: 0,
                        fontSize: 'var(--font-9)',
                        padding: '1px 6px',
                        minHeight: 'auto',
                        lineHeight: '16px',
                      }}
                    >
                      {x.kind === 'purchase' ? t('dash.tag_purchase') : t('dash.tag_replenish')}
                    </span>
                    {x.product_name || x.sku}
                  </span>
                  {x.qty > 0 && (
                    <span
                      style={{
                        flexShrink: 0,
                        fontWeight: 'var(--fw-bold)',
                        color: x.kind === 'purchase' ? 'var(--warning)' : 'var(--danger)',
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
                      ? t('dash.stock_sufficient')
                      : t('dash.cover_for_days', { n: x.days_to_empty })}
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
            <span
              style={{
                fontSize: 'var(--font-15)',
                fontWeight: 'var(--fw-semibold)',
                color: '#fff',
              }}
            >
              {t('dash.close')}
            </span>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
