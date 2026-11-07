import { createPortal } from 'react-dom'
import { fmtInt, fmtWh } from '../format'
import type { InventoryRow } from '../../../api/types'
import { t } from '../../../locale'
import { usePaged, atBottom } from './usePaged'

interface Props {
  open: boolean
  onClose: () => void
  /** 卡面已有的预览行(首帧先渲染它, 分页结果回来再替换 —— 弹窗不为"等数据"而空白) */
  fullOut: InventoryRow[] | null
  oosSrc: InventoryRow[] | null
  healthTab: string
  channel?: string
}

export default function OosSheet(props: Props) {
  const { open, onClose, fullOut, oosSrc: _oosSrc, healthTab, channel } = props
  // 维度必须与健康卡当前视图一致(own/平台/B/BC 合计) —— 曾硬编码 platform, 弹窗与卡面数字对不上
  const wh =
    healthTab === 'own'
      ? 'own'
      : healthTab === 'bc'
        ? 'bc'
        : healthTab === 'platform'
          ? 'platform'
          : 'platform_b'
  /**
   * 2026-11-07 P2 真分页: 原来点击时拉 `limit=5000` 一次性渲染 5 千行 DOM。
   * 现在 500/页, 滚到底续拉; 首帧用卡面已有的 oosSrc 预览行, 不等网络。
   */
  const pg = usePaged<InventoryRow>(
    open,
    function (off, lim) {
      return (
        '/api/inventory/out-of-stock?channel=' +
        encodeURIComponent(channel || 'jd') +
        '&wh=' +
        wh +
        '&paged=1&limit=' +
        lim +
        '&offset=' +
        off
      )
    },
    500,
  )
  const fallback = fullOut && fullOut.length ? fullOut : _oosSrc || []
  const list = pg.rows.length ? pg.rows : fallback
  const total = pg.total >= 0 ? pg.total : list.length
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
            {t('os.title_n', { n: fmtInt(total) })}
          </div>
          {list.map(function (x, i) {
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
          {pg.busy ? (
            <div
              style={{
                textAlign: 'center',
                padding: 8,
                color: 'var(--muted)',
                fontSize: 'var(--font-12)',
              }}
            >
              {t('common.loading')}
            </div>
          ) : null}
          {pg.end && list.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: 8,
                color: 'var(--muted)',
                fontSize: 'var(--font-12)',
              }}
            >
              {t('common.empty')}
            </div>
          ) : null}
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
