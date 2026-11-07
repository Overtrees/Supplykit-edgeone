import { createPortal } from 'react-dom'
import { fmtInt, fmtWh, alertAge, whTag } from '../format'
import { t } from '../../../locale'
import { usePaged, atBottom } from './usePaged'

/** 告警明细行: 已知字段精确声明, 动态扩展字段 unknown(索引签名兜底) */
interface AlertRow {
  id?: number | string
  alert_type?: string
  title?: string
  description?: string
  severity?: string
  created_at?: string
  related_sku?: string
  warehouse?: string
  warehouse_type?: string
  [k: string]: unknown
}

interface Props {
  open: boolean
  onClose: () => void
  otherTotal: number
  fullAlerts: AlertRow[]
  replMode?: string
  onAlert?: (sku?: string, whType?: string, wh?: string) => void
  channel?: string
}

export default function OtherAlertsSheet(props: Props) {
  const { open, onClose, otherTotal, fullAlerts, replMode, onAlert, channel } = props
  /**
   * 2026-11-07 P2 真分页: 300/页、滚到底续拉。排除项与卡面口径一致 ——
   *   低库存/采购/补货由各自的卡承载; 自有仓三条(7215 行, 占全量 2/3)由「自有仓健康」承载。
   * 接口不做分组配额(分页必须是全序切页), 所以这里的 total 就是"其他告警"的真实条数。
   */
  const pg = usePaged<AlertRow>(
    open,
    function (off, lim) {
      return (
        '/api/alerts?channel=' +
        encodeURIComponent(channel || 'jd') +
        '&paged=1&limit=' +
        lim +
        '&offset=' +
        off +
        '&exclude=low_stock,replenish,purchase_need,own_overstock,own_transfer_gap,own_expiry_risk'
      )
    },
    300,
  )
  const list = pg.rows.length
    ? pg.rows
    : (fullAlerts || []).filter(
        x => !['low_stock', 'replenish', 'purchase_need'].includes(x.alert_type),
      )
  const _total = pg.total >= 0 ? pg.total : otherTotal
  // 原组件内的 _whTag 依赖闭包 _replMode; 这里显式绑定, JSX 体一行不用改
  const _whTag = (wt?: string | null) => whTag(wt, props.replMode)
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
          onScroll={function (e) {
            if (atBottom(e)) pg.more()
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
            {t('oa.title_n', { n: fmtInt(_total) })}
          </div>
          {list.map(function (x) {
            return (
              <div
                key={x.id}
                onClick={function () {
                  onAlert && onAlert(x.related_sku, x.warehouse_type, x.warehouse)
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
                      fontWeight: 'var(--fw-semibold)',
                      fontSize: 'var(--font-sm)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {x.title}
                  </span>
                  <span
                    style={{
                      display: 'inline-flex',
                      gap: 4,
                      alignItems: 'center',
                      flexShrink: 0,
                    }}
                  >
                    {(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type)) ? (
                      <span
                        title={x.warehouse || ''}
                        style={{
                          fontSize: 'var(--font-9)',
                          padding: '1px 6px',
                          borderRadius: 'var(--radius-full)',
                          background: 'var(--bg)',
                          color: 'var(--muted)',
                        }}
                      >
                        {x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type)}
                      </span>
                    ) : null}
                    <span
                      className={'pill ' + (x.severity === 'error' ? 'danger' : 'warning')}
                      style={{ fontSize: 'var(--font-10)' }}
                    >
                      {x.severity === 'error' ? t('ls.urgent') : t('ls.warning')}
                    </span>
                  </span>
                </div>
                <div className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
                  {x.description}
                  <span style={{ color: 'var(--muted2)', fontSize: 'var(--font-10)' }}>
                    {alertAge(x.created_at)}
                  </span>
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
              {t('common.close')}
            </span>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
