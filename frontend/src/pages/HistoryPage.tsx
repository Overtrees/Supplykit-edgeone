import React, { useEffect, useMemo, useRef, useState } from 'react'
import Chart from '../components/Chart'
import ErrorRetry from '../components/ErrorRetry'
import { api, freshStamp } from '../api/client'
import { useAppStore } from '../store/useAppStore'

/**
 * 历史归档(2026-11-02 S2-4 方案 A)
 *
 * 这是 daily_stats 的第一个读取方。此前 /cron/archive 每天把 365 天前的订单搬进
 * daily_stats 并删掉源行, 但没有任何页面能读 —— "归档后仍可查"是句空话。
 *
 * 后端把两张表拼成一个查询面(orders ≤365 天 / daily_stats >365 天), 本页只管展示,
 * 但有三件事必须由页面负责说清楚, 否则数字会被误读:
 *  1. 口径: 金额 = 订单金额合计(全部状态), **不等于**看板的 GMV(已支付口径)。
 *     两个页面数字对不上时, 用户第一反应是"有 bug"。
 *  2. 覆盖: 归档为空时要直说"为什么空"(订单最早到哪天 / 保留期多久), 而不是空白表格。
 *  3. 同比: 没有去年数据时显示"无可比数据", 绝不用 0 当分母算成 -100%。
 */
const dstr = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - n)
  return dstr(d)
}
const today = () => dstr(new Date())

const isNarrow = () => typeof window !== 'undefined' && window.innerWidth <= 932
const fmtMoney = (n: number): string => {
  const v = Number(n || 0)
  const abs = Math.abs(v)
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (abs >= (isNarrow() ? 1e4 : 1e5)) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN')
}
const fmtInt = (n: number): string => {
  const v = Math.round(Number(n || 0))
  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + '万'
  return String(v)
}

const PRESETS = [
  { id: '7d', label: '近 7 天', days: 7 },
  { id: '30d', label: '近 30 天', days: 30 },
  { id: '90d', label: '近 90 天', days: 90 },
  { id: 'custom', label: '自定义', days: 0 },
]

export default function HistoryPage() {
  const channel = useAppStore(s => s.channel)
  const [preset, setPreset] = useState('30d')
  const [start, setStart] = useState(() => daysAgo(29))
  const [end, setEnd] = useState(() => today())
  const [by, setBy] = useState<'day' | 'month'>('day')
  const [topBy, setTopBy] = useState<'sku' | 'store'>('sku')
  const [cov, setCov] = useState<any>(null)
  const [sum, setSum] = useState<any>(null)
  const [top, setTop] = useState<any>(null)
  const [yoy, setYoy] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<any>(null)
  const seq = useRef(0)

  const load = () => {
    const my = ++seq.current
    setLoading(true)
    setErr(null)
    const q = `channel=${encodeURIComponent(channel || 'jd')}&start=${start}&end=${end}&t=${freshStamp()}`
    Promise.allSettled([
      api.get(`/api/history/coverage?channel=${encodeURIComponent(channel || 'jd')}`),
      api.get(`/api/history/summary?${q}&by=${by}`),
      api.get(`/api/history/top?${q}&by=${topBy}&limit=20`),
      api.get(
        `/api/history/yoy?channel=${encodeURIComponent(channel || 'jd')}&days=30&t=${freshStamp()}`,
      ),
    ]).then(([c, s, t0, y]) => {
      if (my !== seq.current) return
      const val = (r: any) => (r.status === 'fulfilled' ? r.value?.data : null)
      setCov(val(c))
      setSum(val(s))
      setTop(val(t0))
      setYoy(val(y))
      if (s.status === 'rejected') setErr(s.reason)
      setLoading(false)
    })
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, start, end, by, topBy])

  const pickPreset = (id: string, days: number) => {
    setPreset(id)
    if (days) {
      setStart(daysAgo(days - 1))
      setEnd(today())
    }
  }

  const series = sum?.series || []
  const totals = sum?.totals || {}
  const empty = !series.length || !totals.orders

  const chartOption = useMemo(() => {
    const names = series.map((x: any) => x.date)
    const values = series.map((x: any) => x.gmv)
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (p: any) => {
          const i = p[0]?.dataIndex ?? 0
          const r = series[i] || {}
          return `${r.date}<br/>金额: ${fmtMoney(r.gmv)}<br/>单数: ${fmtInt(
            r.orders,
          )}<br/>件数: ${fmtInt(r.qty)}`
        },
      },
      grid: { containLabel: true, top: 12, bottom: 4, left: 4, right: 8 },
      xAxis: {
        type: 'category',
        data: names,
        axisLabel: { fontSize: 9, hideOverlap: true },
        axisTick: { show: false },
      },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: (v: number) => fmtMoney(v) } },
      series: [
        {
          type: series.length === 1 ? 'bar' : 'line',
          data: values,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 2, color: '#007aff' },
          areaStyle: { opacity: 0.12, color: '#007aff' },
          barWidth: '50%',
          itemStyle: { color: '#007aff', borderRadius: [4, 4, 0, 0] },
        },
      ],
    }
  }, [series])

  const srcText = useMemo(() => {
    const s = sum?.sources || {}
    const parts: string[] = []
    if (s.archive) parts.push(`归档表 ${s.archive[0]} ~ ${s.archive[1]}`)
    if (s.orders) parts.push(`订单表 ${s.orders[0]} ~ ${s.orders[1]}`)
    return parts.join(' · ')
  }, [sum])

  return (
    <div>
      <div className="card">
        <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, marginBottom: 2 }}>
          历史归档
          <span className="small muted" style={{ fontWeight: 400, fontSize: 'var(--font-xs)' }}>
            {' '}
            一年以前的数据从这里查
          </span>
        </div>
        <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
          {cov?.note || '加载中…'}
          {cov?.archive?.rows
            ? ` · ${fmtInt(cov.archive.rows)} 行 / ${fmtInt(cov.archive.skus)} 个 SKU`
            : ''}
        </div>

        {/* 区间与粒度分两行: 手机上 4 个预设 + 2 个粒度挤不进一行, 挤的结果是
            "按日/按月"被顶到第二行左端, 看起来像布局坏了 */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
          {PRESETS.map(p => (
            <span
              key={p.id}
              className={'pill ' + (preset === p.id ? 'info' : 'neutral')}
              onClick={() => pickPreset(p.id, p.days)}
              style={{ cursor: 'pointer', fontSize: 'var(--font-xs)' }}
            >
              {p.label}
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
          <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            粒度
          </span>
          {(['day', 'month'] as const).map(k => (
            <span
              key={k}
              className={'pill ' + (by === k ? 'info' : 'neutral')}
              onClick={() => setBy(k)}
              style={{ cursor: 'pointer', fontSize: 'var(--font-xs)' }}
            >
              {k === 'day' ? '按日' : '按月'}
            </span>
          ))}
        </div>

        {preset === 'custom' && (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
            <input
              type="date"
              className="tpl-field"
              value={start}
              onChange={e => setStart(e.target.value)}
            />
            <span className="muted small">~</span>
            <input
              type="date"
              className="tpl-field"
              value={end}
              onChange={e => setEnd(e.target.value)}
            />
          </div>
        )}

        <div className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
          {sum ? `${sum.start} ~ ${sum.end}` : ''}
          {sum?.clamped ? ' · 已按单次查询 400 天上限截断' : ''}
          {srcText ? ' · 来源: ' + srcText : ''}
        </div>
      </div>

      {err && !sum ? (
        <div className="card">
          <ErrorRetry error={err} onRetry={load} />
        </div>
      ) : null}

      <div className="card">
        <div className="section-title">区间汇总</div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0,1fr))',
            gap: 8,
            margin: '8px 0 4px',
          }}
        >
          {[
            { k: '金额', v: fmtMoney(totals.gmv), s: '元' },
            { k: '单数', v: fmtInt(totals.orders), s: '单' },
            { k: '件数', v: fmtInt(totals.qty), s: '件' },
            { k: '日均', v: fmtMoney(totals.avg_gmv_per_day), s: '元/天' },
          ].map(x => (
            <div key={x.k} style={{ minWidth: 0 }}>
              <div className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
                {x.k}
              </div>
              <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, lineHeight: 1.25 }}>
                {loading && !sum ? '—' : x.v}
              </div>
              <div className="small muted" style={{ fontSize: 'var(--font-9)' }}>
                {x.s}
              </div>
            </div>
          ))}
        </div>
        <div className="small muted" style={{ fontSize: 'var(--font-9)', marginBottom: 6 }}>
          金额 = 订单金额合计(全部状态), 与看板 GMV(已支付口径, 扣优惠/加运费税)不同口径
        </div>
        {empty ? (
          <div
            className="small muted"
            style={{ padding: '22px 0', textAlign: 'center', fontSize: 'var(--font-13)' }}
          >
            该区间没有数据{cov?.note ? ' · ' + cov.note : ''}
          </div>
        ) : (
          <Chart option={chartOption} height={200} />
        )}
      </div>

      <div className="card">
        <div
          className="section-title"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
        >
          <span>Top 20</span>
          <span
            style={{
              display: 'inline-flex',
              gap: 2,
              background: 'var(--bg)',
              borderRadius: 999,
              padding: 2,
            }}
          >
            {(['sku', 'store'] as const).map(k => (
              <span
                key={k}
                onClick={() => setTopBy(k)}
                style={{
                  fontSize: 'var(--font-9)',
                  padding: '2px 8px',
                  borderRadius: 999,
                  cursor: 'pointer',
                  fontWeight: topBy === k ? 600 : 400,
                  background: topBy === k ? 'var(--card)' : 'transparent',
                  color: topBy === k ? 'var(--text)' : 'var(--muted2)',
                }}
              >
                {k === 'sku' ? '按 SKU' : '按店铺'}
              </span>
            ))}
          </span>
        </div>
        {(top?.items || []).length === 0 ? (
          <div className="small muted" style={{ padding: '10px 0' }}>
            无数据
          </div>
        ) : (
          (top.items as any[]).map((it, i) => (
            <div
              key={it.key + i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '7px 0',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <span
                className="small muted"
                style={{ width: 18, fontSize: 'var(--font-xs)', flex: 'none' }}
              >
                {i + 1}
              </span>
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontSize: 'var(--font-13)',
                }}
                title={it.key}
              >
                {it.key || '(空)'}
              </span>
              <span style={{ fontSize: 'var(--font-13)', fontWeight: 600, flex: 'none' }}>
                {fmtMoney(it.gmv)}
              </span>
              <span
                className="small muted"
                style={{ width: 46, textAlign: 'right', fontSize: 'var(--font-xs)', flex: 'none' }}
              >
                {it.share}%
              </span>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <div className="section-title">同比 · 近 30 天 vs 去年同期</div>
        {!yoy ? (
          <div className="small muted" style={{ padding: '10px 0' }}>
            加载中…
          </div>
        ) : !yoy.has_prev ? (
          <div className="small muted" style={{ fontSize: 'var(--font-13)', lineHeight: 1.6 }}>
            {yoy.note}
          </div>
        ) : (
          <div style={{ marginTop: 6 }}>
            <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginBottom: 6 }}>
              {yoy.cur.start} ~ {yoy.cur.end} vs {yoy.prev.start} ~ {yoy.prev.end}
            </div>
            {(['gmv', 'orders', 'qty'] as const).map(k => {
              const cur = yoy.cur[k]
              const prev = yoy.prev[k]
              const d = yoy.delta?.[k]
              const up = (d ?? 0) >= 0
              return (
                <div
                  key={k}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '6px 0',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <span style={{ flex: 1, fontSize: 'var(--font-13)' }}>
                    {k === 'gmv' ? '金额' : k === 'orders' ? '单数' : '件数'}
                  </span>
                  <span style={{ fontSize: 'var(--font-13)' }}>
                    {k === 'gmv' ? fmtMoney(cur) : fmtInt(cur)}
                  </span>
                  <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
                    去年 {k === 'gmv' ? fmtMoney(prev) : fmtInt(prev)}
                  </span>
                  <span
                    style={{
                      width: 62,
                      textAlign: 'right',
                      fontSize: 'var(--font-13)',
                      fontWeight: 600,
                      color: d === null ? 'var(--muted2)' : up ? 'var(--danger)' : 'var(--success)',
                    }}
                  >
                    {d === null ? '—' : (up ? '+' : '') + d + '%'}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
