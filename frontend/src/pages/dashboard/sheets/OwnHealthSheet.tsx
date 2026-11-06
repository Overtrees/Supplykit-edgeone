import { createPortal } from 'react-dom'
import { useCallback, useEffect, useRef, useState } from 'react'
import { fmtInt } from '../format'
import type { OwnHealthData, OwnHealthItem } from '../../../components/dash/OwnHealthCard'
import { apiJson } from '../../../api/client'
import { t } from '../../../locale'

interface Props {
  open: boolean
  onClose: () => void
  /** 供给侧健康数据(结构由 OwnHealthData 描述, 边界处断言收敛) */
  ownHealth?: unknown
  onAlert?: (sku?: string, whType?: string, wh?: string) => void
  /** 分页拉明细用(2026-11-06): 弹窗自己按页取, 不再让 /dashboard/aux 背 1.3 万条 */
  channel?: string
}

/** 每页条数 —— 与后端 /dashboard/own-health 的 limit 上限(500)留出余量 */
const PAGE = 200

const TYPE_ORDER = ['own_transfer_gap', 'own_expiry_risk', 'own_overstock']

/** 按「缺口 → 效期 → 积压」排序, 组内保持后端给的紧急度顺序 */
function orderRows(rows: OwnHealthItem[]): OwnHealthItem[] {
  return TYPE_ORDER.map(function (k) {
    return rows.filter(function (x) {
      return x.alert_type === k
    })
  }).reduce(function (a, b) {
    return a.concat(b)
  }, [] as OwnHealthItem[])
}

export default function OwnHealthSheet(props: Props) {
  const { open, onClose, ownHealth, onAlert, channel } = props
  const [rows, setRows] = useState<OwnHealthItem[]>([])
  const [busy, setBusy] = useState(false)
  const [end, setEnd] = useState(false)
  const offRef = useRef(0)
  const busyRef = useRef(false)
  const endRef = useRef(false)

  /**
   * 2026-11-06 分页拉取(结构改造, 不是"藏起来")
   *
   * 原来弹窗的数据来自 /dashboard/aux 的 ownHealth.items —— **全量**。当条目从 90 涨到
   * 13,615 条时: aux 响应 5.6MB、共享表缓存单条超 TiDB 6MB 上限永远写不进去、每个请求
   * 重算两遍(线上单请求 9.5s)、同实例其它接口全部排队 → "切页面加载不出"。
   * 现在弹窗打开才按页拉(200/页, 滚到底自动续), 翻到底仍是全量, 一条不少。
   */
  const load = useCallback(
    async function (reset: boolean) {
      if (!open || busyRef.current) return
      if (!reset && endRef.current) return
      busyRef.current = true
      setBusy(true)
      const off = reset ? 0 : offRef.current
      try {
        const r =
          (await apiJson<{ data?: { items?: OwnHealthItem[] } }>(
            'GET',
            '/api/dashboard/own-health?channel=' +
              encodeURIComponent(channel || 'jd') +
              '&offset=' +
              off +
              '&limit=' +
              PAGE,
            { timeout: 45000 },
          )) || {}
        const add = (r.data && r.data.items ? r.data.items : []) as OwnHealthItem[]
        offRef.current = off + add.length
        setRows(function (prev) {
          return orderRows(off === 0 ? add : prev.concat(add))
        })
        if (add.length < PAGE) {
          endRef.current = true
          setEnd(true)
        }
      } catch {
        // 失败不阻塞弹窗: 已加载的行照常展示, 用户可点「加载更多」重试
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [open, channel],
  )

  useEffect(
    function () {
      if (!open) return
      offRef.current = 0
      endRef.current = false
      setRows([])
      setEnd(false)
      load(true)
    },
    [open, channel, load],
  )

  if (!open) return null
  const _src = rows.length
    ? rows
    : (((ownHealth as OwnHealthData | null)?.items || []) as OwnHealthItem[])

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
            const el = e.currentTarget
            if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) load(false)
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
                占用垂直空间。现在统一按 缺口→效期→积压 排序铺平, 每行行首 pill 标类型。
              2026-11-06: 数据来源改为分页接口(见 load()), 排序仍按 缺口→效期→积压。 */}
          {(() => {
            const _OWN_META: Record<string, { tag: string; cls: string; c: string }> = {
              own_transfer_gap: { tag: 'oh.tag_transfer_gap', cls: 'danger', c: 'var(--danger)' },
              own_expiry_risk: { tag: 'oh.tag_expiry', cls: 'danger', c: 'var(--danger)' },
              own_overstock: { tag: 'oh.tag_overstock', cls: 'warning', c: 'var(--warning)' },
            }
            return _src.map(function (x, i) {
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
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {x.product_name || x.sku}
                      </span>
                    </span>
                    <span
                      style={{
                        flexShrink: 0,
                        fontWeight: 'var(--fw-bold)',
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
          {busy ? (
            <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
              {t('common.loading')}
            </div>
          ) : null}
          {!busy && !end ? (
            <div
              onClick={function () {
                load(false)
              }}
              className="clickable"
              style={{ padding: 12, textAlign: 'center', fontSize: 'var(--font-sm)' }}
            >
              {t('ins.load_more')}
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
