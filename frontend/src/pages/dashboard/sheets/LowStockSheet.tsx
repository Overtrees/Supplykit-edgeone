import { createPortal } from 'react-dom'
import { fmtInt, fmtWh, alertAge, whTag } from '../format'
import { t } from '../../../locale'
import { usePaged, atBottom } from './usePaged'

/** 告警明细行: 已知字段精确声明, 动态扩展字段 unknown(索引签名兜底) */
interface AlertRow {
  id?: number | string
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
  total: number
  rows: AlertRow[]
  replMode?: string
  onAlert?: (sku?: string, whType?: string, wh?: string) => void
  channel?: string
}

export default function LowStockSheet(props: Props) {
  const { open, onClose, total, rows, replMode, onAlert, channel } = props
  /**
   * 2026-11-07 P2 真分页: 原来父组件点「更多」时一次拉 `limit=3000`(排除自有仓三条后 0.8s,
   * 未排除时 3.9MB/4.7s)。现在 300/页、滚到底续拉, 首帧先用卡面已筛好的 rows 渲染。
   * 注意弹窗**不按仓型二次过滤**: 翻页是 SQL 全序(id DESC)切出来的, 前端再筛会让每页
   * 条数忽多忽少, 甚至出现"标题 2151、列表 300"的对不上。
   */
  const pg = usePaged<AlertRow>(
    open,
    function (off, lim) {
      return (
        '/api/alerts?channel=' +
        encodeURIComponent(channel || 'jd') +
        '&paged=1&types=low_stock&limit=' +
        lim +
        '&offset=' +
        off
      )
    },
    300,
  )
  const list = pg.rows.length ? pg.rows : rows
  const _total = pg.total >= 0 ? pg.total : total
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
            {t('ls.title_n', { n: fmtInt(_total) })}
          </div>
          {/* 2026-11-01 曾把 warehouse_type 过滤整个拿掉 —— 当时的实测结论是
                "库内 low_stock 200 条全是 platform, 没有 bc 类型", 于是 bbcc 下过滤恒空。
                2026-11-02 复查后发现真因不是"没有 bc 行", 而是 **bc 行的 id 比 platform 小 6 万**,
                后端混在一起 ORDER BY id DESC LIMIT 300 时被挤出窗口, 前端全量接口里其实有
                160 条 bc 行(dashboard.py 同日改为 low_stock 单独查)。过滤现在统一在
                lowStockAlerts 里做(带"筛空则回落全量"的防御), 卡面与弹窗共用同一份,
                不会再出现"卡面显示郑州仓、弹窗显示 BC"这种两套口径。 */}
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
                      {x.severity === 'warning' ? t('ls.warning') : t('ls.overstock')}
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
