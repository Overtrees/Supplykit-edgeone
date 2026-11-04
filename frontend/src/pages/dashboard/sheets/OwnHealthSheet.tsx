import { createPortal } from 'react-dom'
import { fmtInt } from '../format'
import type { OwnHealthData, OwnHealthItem } from '../../../components/dash/OwnHealthCard'
import { t } from '../../../locale'

interface Props {
  open: boolean
  onClose: () => void
  ownHealth?: any
  onAlert?: (sku?: any, whType?: any, wh?: any) => void
}

export default function OwnHealthSheet(props: Props) {
  const { open, onClose, ownHealth, onAlert } = props
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
              // 说明行已删除, 由标题自身 marginBottom 提供与首行的间距(原 4 + 说明行 12)
              marginBottom: 12,
              textAlign: 'center',
              color: 'var(--text)',
            }}
          >
            {t('oh.title_n', { n: fmtInt((ownHealth as OwnHealthData | null)?.total || 0) })}
          </div>
          {/* 2026-11-01 去掉「供给侧口径: 调拨缺口 / 积压 / 效期（自有仓不产生销售…）」说明行
                (用户明确要求去除): 口径解释属实现注释, 不该占弹窗首屏; 行首 pill 已标类型。 */}{' '}
          {/* 2026-11-01 改为**单一全量列表 + 行首标签区分类型**(用户定, 参考「采购&补货」卡):
                原按三类分组各带小标题, 与采购补货卡的行标签范式不一致, 且分组标题
                占用垂直空间。现在统一按 缺口→效期→积压 排序铺平, 每行行首 pill 标类型。 */}
          {(() => {
            const _OWN_META: Record<string, { tag: string; cls: string; c: string }> = {
              own_transfer_gap: { tag: 'oh.tag_transfer_gap', cls: 'danger', c: 'var(--danger)' },
              own_expiry_risk: { tag: 'oh.tag_expiry', cls: 'danger', c: 'var(--danger)' },
              own_overstock: { tag: 'oh.tag_overstock', cls: 'warning', c: 'var(--warning)' },
            }
            const _all = ((ownHealth as OwnHealthData | null)?.items || []) as OwnHealthItem[]
            // 2026-11-01 分类型配额(每类上限 20, 总数上限 60)——
            //   直接整体 sort 后 slice(0,60) 时, 缺口 9 条排在最前, 剩下 51 个名额
            //   全被"效期"吃掉, "积压" 35 条一条都进不来(实测弹窗只剩 积压 5)。
            //   改为三类各自排序后各取前 20 再拼回, 保证三类都可见。
            // 2026-11-01 改为**全量展示**(用户裁决: 改全量)。
            //   原为分类型配额每类 20(共 49), 导致标题写 (90) 而弹窗只有 49 行, 效期 26 条、
            //   积压 15 条在弹窗里看不到 —— 弹窗是唯一能看全的入口, 截断即等于丢数据。
            //   仍按 缺口→效期→积压 排序, 便于顺序阅读; 高度由 maxHeight:70vh + 滚动承接。
            const all = (['own_transfer_gap', 'own_expiry_risk', 'own_overstock'] as string[])
              .map(function (t) {
                return _all.filter(function (x) {
                  return x.alert_type === t
                })
              })
              .reduce(function (a, b) {
                return a.concat(b)
              }, [])
            return all.map(function (x, i) {
              const m = _OWN_META[x.alert_type] || {
                tag: 'oh.tag_warning',
                cls: 'warning',
                c: 'var(--warning)',
              }
              const metric =
                x.alert_type === 'own_transfer_gap'
                  ? t('oh.gap_n', { n: fmtInt(x.gap_qty) })
                  : x.alert_type === 'own_expiry_risk'
                    ? x.days_to_expiry != null && x.days_to_expiry < 0
                      ? t('oh.expired_n', { n: Math.abs(x.days_to_expiry) })
                      : t('oh.left_n', { n: fmtInt(x.days_to_expiry ?? undefined) })
                    : t('oh.cover_n', { n: Math.round(Number(x.cover_days || 0)) })
              return (
                <div
                  key={x.alert_type + x.sku + i}
                  onClick={function () {
                    onAlert && onAlert(x.sku, 'own', x.warehouse || '')
                    onClose()
                  }}
                  className="clickable"
                  style={{ padding: '7px 0', borderBottom: '1px solid var(--border)' }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 8,
                      alignItems: 'flex-start',
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
                        className={'pill ' + m.cls}
                        style={{
                          flexShrink: 0,
                          fontSize: 'var(--font-9)',
                          padding: '1px 6px',
                          minHeight: 'auto',
                          lineHeight: '16px',
                        }}
                      >
                        {t(m.tag)}
                      </span>
                      {x.product_name || x.sku}
                    </span>
                    <span
                      style={{
                        flexShrink: 0,
                        fontWeight: 700,
                        fontSize: 'var(--font-sm)',
                        color: m.c,
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {metric}
                    </span>
                  </div>
                  <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginTop: 2 }}>
                    {x.sku}
                    {x.warehouse ? ' · ' + x.warehouse : ''}
                    {x.description ? ' · ' + x.description : ''}
                  </div>
                </div>
              )
            })
          })()}
          <div
            onClick={function () {
              onClose()
            }}
            className="clickable sheet-close"
            style={{ marginTop: 8 }}
          >
            <span style={{ fontSize: 'var(--font-15)', fontWeight: 600, color: '#fff' }}>{t('common.close')}</span>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
