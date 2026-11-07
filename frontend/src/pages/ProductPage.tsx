import { IconCheck } from '../components/Icons'
import React, { useEffect, useState, useRef } from 'react'
import { api, abortScope, isCanceled } from '../api/client'
import { useToast } from '../components/Toast'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'

import { useAppStore } from '../store/useAppStore'
import { clearCache } from '../api/client'
import { t } from '../locale'
import { PRODUCT_COLS as COLS } from '../components/hammer/configs'
import { colLabel } from '../components/colLabels'
/** 商品列表响应 / 行(动态字段走 unknown 索引签名, 边界处收敛) */
interface ProductRow {
  id?: string | number
  is_active?: number | boolean
  [k: string]: unknown
}
interface ProductResp {
  items?: ProductRow[]
  total?: number
  [k: string]: unknown
}

const COL_KEY = () => 'c_cols_products_' + (useAppStore.getState().channel || 'jd')
const getVis = () => {
  try {
    return JSON.parse(localStorage.getItem(COL_KEY()) || 'null')
  } catch {
    return null
  }
}

function Skeleton() {
  return (
    <div>
      {[1, 2, 3, 4].map(i => (
        <div
          key={i}
          style={{
            display: 'flex',
            gap: 8,
            padding: '8px 0',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div className="skeleton" style={{ width: 70, height: 14 }} />
          <div className="skeleton" style={{ flex: 1, height: 14 }} />
          <div className="skeleton" style={{ width: 50, height: 14 }} />
          <div className="skeleton" style={{ width: 40, height: 14 }} />
          <div className="skeleton" style={{ width: 50, height: 14 }} />
          <div className="skeleton" style={{ width: 40, height: 14 }} />
        </div>
      ))}
    </div>
  )
}

export default function ProductPage() {
  const [list, setList] = useState([])
  const [ld, setLd] = useState(true)
  const [pg, setPg] = useState(1)
  const pgRef = useRef(1)
  const [pgTotal, setPgTotal] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const reqSeq = useRef(0)
  const [loadErr, setLoadErr] = useState('')
  const [visCols, setVisCols] = useState(() => getVis() || COLS.map(c => c.id))
  const toast = useToast()
  const {
    channel: globalChannel,
    hammerSearch,
    hammerCols,
    prodBatch,
    prodSelIds,
    setProdBatchSel,
    setProdBatchFilterLen,
    prodBatchVersion,
    prodBatchAllReq,
  } = useAppStore()
  const selIds = prodSelIds || []
  const setSelIds = setProdBatchSel
  const [, setBatchBusy] = useState(false)
  const s = hammerSearch || ''
  const fl = ld ? [] : list
  const loadProd = p => {
    const seq = ++reqSeq.current
    if (p === 1) setLd(true)
    else setLoadingMore(true)
    api
      .get<ProductResp>(
        '/api/products?page=' +
          p +
          '&page_size=100&channel=' +
          globalChannel +
          '&search=' +
          encodeURIComponent(s),
        { timeout: 90000, scope: 'products' },
      )
      .then(r => {
        if (seq !== reqSeq.current) return
        const d: ProductResp = r.data || {}
        // 数组兜底(2026-10-10: 偶发响应结构异常曾致 fl.map 非函数——渲染崩溃)
        const items = (Array.isArray(d.items) ? d.items : Array.isArray(d) ? d : []) as ProductRow[]
        setPgTotal(d.total || items.length || 0)
        setPg(p)
        pgRef.current = p
        setList(prev => (p === 1 ? items : [...prev, ...items]))
        setLoadErr('')
        setLd(false)
        setLoadingMore(false)
        const _m = { ...useAppStore.getState().batchStateMap }
        ;(items || []).forEach(it => {
          if (it && it.id) _m[it.id] = it.is_active ? 1 : 0
        })
        useAppStore.setState({ batchStateMap: _m })
      })
      .catch(e => {
        if (isCanceled(e)) return
        if (seq === reqSeq.current) {
          setLd(false)
          setLoadingMore(false)
          setList([])
          setLoadErr(t('common.load_failed'))
        }
      })
  }
  useEffect(() => {
    abortScope('products')
    clearCache('products')
    setPg(1)
    loadProd(1)
  }, [globalChannel, s])
  useEffect(() => {
    if (hammerCols?.products) setVisCols(hammerCols.products)
  }, [hammerCols])
  // 退出批量模式时清空选择
  useEffect(() => {
    if (!prodBatch) setSelIds([])
  }, [prodBatch])
  useEffect(() => {
    if (prodBatchVersion > 0) {
      clearCache('products')
      reload()
    }
  }, [prodBatchVersion])
  useEffect(() => {
    if (prodBatchAllReq > 0) {
      const all = fl.map(x => x.id)
      setSelIds(selIds.length === all.length && all.length > 0 ? [] : all)
    }
  }, [prodBatchAllReq])
  useEffect(() => {
    setProdBatchFilterLen(fl.length)
  }, [fl.length])
  const reload = () => {
    setPgTotal(0)
    loadProd(1)
  }

  if (ld)
    return (
      <div className="card">
        <div className="section-title">
          <span>{t('nav.products')}</span>
        </div>
        <Skeleton />
      </div>
    )

  return (
    <div
      key={ld ? 'skeleton' : 'data'}
      className="card fade-in"
      style={{ containerType: 'inline-size' }}
    >
      <div
        className="section-title"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}
      >
        <span>
          {t('prd.title')}{' '}
          <span
            className="small muted"
            style={{ fontSize: 'var(--font-xs)', fontWeight: 'var(--fw-regular)' }}
          >
            {t('prd.loaded', {
              a: Math.min(list.length, pgTotal || list.length),
              b: pgTotal || list.length,
              c: visCols.length,
              d: COLS.length,
            })}
            {s ? ` · "${s}"` : ''}
          </span>
        </span>
      </div>
      {fl.length === 0 ? (
        loadErr ? (
          <ErrorRetry error={loadErr} onRetry={() => loadProd(1)} />
        ) : (
          <EmptyState
            icon="tag"
            title={s ? t('product.empty_matched') : t('product.empty')}
            desc={s ? t('prd.try_other_kw') : t('prd.import_hint')}
            action={
              !s && (
                <button
                  className="btn btn-primary"
                  onClick={() => window.__setPage && window.__setPage('cleansing')}
                >
                  {t('common.go_import')}
                </button>
              )
            }
          />
        )
      ) : (
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 180px)' }}>
          <table>
            <colgroup>
              {visCols.map(id => {
                const col = COLS.find(c => c.id === id)
                return col ? <col key={col.id} /> : null
              })}
            </colgroup>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
              <tr>
                {prodBatch && <th style={{ width: 30 }}></th>}
                {visCols.map(id => {
                  const col = COLS.find(c => c.id === id)
                  return col ? <th key={col.id}>{colLabel(col.id)}</th> : null
                })}
              </tr>
            </thead>
            <tbody>
              {fl.map(x => (
                <tr
                  key={x.id}
                  style={{
                    ...(prodBatch && selIds.includes(x.id)
                      ? { background: 'rgba(29,78,216,0.08)' }
                      : undefined),
                    ...(x.status !== 'active' ? { opacity: 0.55 } : undefined),
                  }}
                >
                  {prodBatch && (
                    <td
                      className="clickable"
                      onClick={e => {
                        const ids = selIds
                        setSelIds(ids.includes(x.id) ? ids.filter(i => i !== x.id) : [...ids, x.id])
                      }}
                    >
                      <span
                        style={{
                          width: 18,
                          height: 18,
                          borderRadius: 'var(--radius-xs)',
                          border: '1.5px solid',
                          borderColor: selIds.includes(x.id) ? 'var(--primary)' : 'var(--border)',
                          background: selIds.includes(x.id) ? 'var(--primary)' : 'transparent',
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          color: '#fff',
                          fontSize: 'var(--font-xs)',
                        }}
                      >
                        {selIds.includes(x.id) ? <IconCheck size={12} /> : ''}
                      </span>
                    </td>
                  )}
                  {visCols.map(id => {
                    const col = COLS.find(c => c.id === id)
                    if (!col)
                      return (
                        <td
                          key={col.id}
                          className="small muted"
                          style={{ fontSize: 'var(--font-xs)' }}
                        >
                          -
                        </td>
                      )
                    if (col.id === 'barcode')
                      return (
                        <td key={col.id} className="mono" style={{ fontSize: 'var(--font-xs)' }}>
                          {x.barcode || '-'}
                        </td>
                      )
                    if (col.id === 'channel')
                      // 2026-11-06 取**行值**而非全局选中的渠道: 原来整列都渲染成当前渠道,
                      //   切一次渠道全表跟着变, 看起来就是「商品没有按渠道分」。
                      //   channel 是世界的名字(渠道码), 显示走已有双语词条, 不硬编码中文。
                      return (
                        <td key={col.id} style={{ fontSize: 'var(--font-xs)' }}>
                          {String(x.channel || '') === 'other'
                            ? t('inv.channel_other_short')
                            : String(x.channel || '') === 'jd'
                              ? t('inv.channel_jd_short')
                              : String(x.channel || '-')}
                        </td>
                      )
                    if (col.id === 'brand')
                      return (
                        <td key={col.id} style={{ fontSize: 'var(--font-sm)' }}>
                          {x.brand || '-'}
                        </td>
                      )
                    if (col.id === 'sku')
                      return (
                        <td key={col.id} className="mono col-sku">
                          {x.sku}
                        </td>
                      )
                    if (col.id === 'name')
                      return (
                        <td key={col.id} className="col-name">
                          {x.product_name}
                        </td>
                      )
                    if (col.id === 'store')
                      return (
                        <td key={col.id} className="col-store">
                          {x.store}
                        </td>
                      )
                    if (col.id === 'cat')
                      return (
                        <td key={col.id} className="col-store">
                          {x.category}
                        </td>
                      )
                    if (col.id === 'price')
                      return (
                        <td key={col.id} className="col-price">
                          ¥{x.price}
                        </td>
                      )
                    if (col.id === 'box')
                      return (
                        <td key={col.id} className="col-qty" style={{ fontSize: 'var(--font-sm)' }}>
                          {x.box_qty || 1}
                        </td>
                      )
                    if (col.id === 'unit')
                      return (
                        <td key={col.id} className="col-qty" style={{ fontSize: 'var(--font-sm)' }}>
                          {x.unit || t('prd.unit_default')}
                        </td>
                      )
                    if (col.id === 'weight')
                      return (
                        <td key={col.id} className="col-qty" style={{ fontSize: 'var(--font-sm)' }}>
                          {x.weight || '-'}
                        </td>
                      )
                    if (col.id === 'volume')
                      return (
                        <td key={col.id} className="col-qty" style={{ fontSize: 'var(--font-sm)' }}>
                          {x.volume || '-'}
                        </td>
                      )
                    if (col.id === 'batch_days')
                      return (
                        <td key={col.id} className="col-qty" style={{ fontSize: 'var(--font-sm)' }}>
                          {x.batch_days || '-'}
                        </td>
                      )
                    if (col.id === 'status')
                      return (
                        <td key={col.id} style={{ textAlign: 'center' }}>
                          <span
                            className={'pill ' + (x.status === 'active' ? 'success' : 'warning')}
                          >
                            {x.status === 'active'
                              ? t('prd.status_active')
                              : t('prd.status_inactive')}
                          </span>
                        </td>
                      )
                    // 三态只读: 未标注('')单独成态 —— 它不是"否", 出入库会按"做效期"处理并告警,
                    //   必须让运营一眼看出哪些商品还欠一个标注(编辑统一走清洗页导入)
                    if (col.id === 'expiry_managed') {
                      const _em = String(x.expiry_managed ?? '').trim()
                      const _map =
                        _em === 'yes'
                          ? { txt: t('prd.em_yes'), cls: 'success', tip: t('prd.em_yes_tip') }
                          : _em === 'no'
                            ? { txt: t('prd.em_no'), cls: 'neutral', tip: t('prd.em_no_tip') }
                            : { txt: t('prd.em_unset'), cls: 'warning', tip: t('prd.em_unset_tip') }
                      return (
                        <td key={col.id} style={{ textAlign: 'center' }}>
                          <span className={'pill ' + _map.cls} title={_map.tip}>
                            {_map.txt}
                          </span>
                        </td>
                      )
                    }
                    return (
                      <td
                        key={col.id}
                        className="small muted"
                        style={{ fontSize: 'var(--font-xs)' }}
                      >
                        -
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {pgTotal > 0 && list.length < pgTotal && (
            <div
              style={{ textAlign: 'center', padding: '10px 0' }}
              ref={function (el) {
                if (el && !el._obs) {
                  el._obs = new IntersectionObserver(
                    function (entries) {
                      if (entries[0].isIntersecting && !loadingMore) {
                        const np = pgRef.current + 1
                        pgRef.current = np
                        loadProd(np)
                      }
                    },
                    { rootMargin: '200px' },
                  )
                  el._obs.observe(el)
                }
              }}
            >
              <span
                className="btn btn-ghost"
                style={{ fontSize: 'var(--font-sm)', padding: '6px 16px', cursor: 'pointer' }}
              >
                {loadingMore ? t('common.loading') : ''}({Math.min(list.length, pgTotal)}/{pgTotal})
              </span>
            </div>
          )}
          {pgTotal > 0 && list.length >= pgTotal && (
            <div
              style={{
                textAlign: 'center',
                padding: '10px 0',
                fontSize: 'var(--font-xs)',
                color: 'var(--muted2)',
              }}
            >
              {t('common.all_loaded', { n: pgTotal })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
