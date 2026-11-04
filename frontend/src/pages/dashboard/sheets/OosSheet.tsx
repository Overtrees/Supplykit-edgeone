import { createPortal } from 'react-dom'
import { fmtInt, fmtWh } from '../format'
import type { InventoryRow } from '../../../api/types'
import { t } from '../../../locale'

interface Props {
  open: boolean
  onClose: () => void
  fullOut: InventoryRow[] | null
  oosSrc: InventoryRow[] | null
  healthTab: string
}

export default function OosSheet(props: Props) {
  const { open, onClose, fullOut, oosSrc: _oosSrc, healthTab } = props
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
            {t('os.title_n', { n: fmtInt((fullOut && fullOut.length ? fullOut : _oosSrc).length) })}
          </div>
          {(fullOut && fullOut.length ? fullOut : _oosSrc).map(function (x, i) {
            return (
              <div
                key={i}
                className="clickable"
                style={{
                  padding: '8px 12px',
                  background: 'var(--card)',
                  borderRadius: 'var(--radius-md)',
                  marginBottom: 6,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <div
                  style={{
                    fontWeight: 'var(--fw-semibold)',
                    fontSize: 'var(--font-sm)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    minWidth: 0,
                  }}
                >
                  {i + 1}. {x.product_name || x.sku}
                </div>
                <span
                  title={x.warehouse || ''}
                  style={{
                    fontSize: 'var(--font-10)',
                    color: 'var(--muted)',
                    background: 'var(--bg)',
                    padding: '0 6px',
                    borderRadius: 'var(--radius-full)',
                    flexShrink: 0,
                  }}
                >
                  {x.warehouse
                    ? fmtWh(x.warehouse)
                    : healthTab === 'bc' || x.warehouse_type === 'bc'
                      ? 'BC'
                      : healthTab === 'own'
                        ? t('os.own')
                        : t('os.platform')}
                </span>
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
              {t('common.close')}
            </span>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
