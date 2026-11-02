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
 *  2. 覆盖: 归档为空时要直说"为什么空", 而不是空白表格。
 *  3. 同比: 没有去年数据时显示"无可比数据", 绝不用 0 当分母算成 -100%。
 *
 * 样式一律走 styles.css 的 .hist-* 类(2026-11-02 用户要求): 内联 style 会让同一个
 * "卡片内小标题"在四个卡片里各有一套字号/间距, 改一处漏三处。
 */
// 日期区间默认值/预设换算已随控件一起移入 store(useAppStore 的 histStart/histEnd/
// setHistPreset) —— 不再放在页面侧, 否则锤子菜单与页面各算一份, 极易不一致。

// 金额一律两位小数: 原实现 <1万走 toLocaleString 默认 0 位, 会显示 64046640.8 这类
// "只有一位小数"的值, 与"万/亿"分支(两位)不一致, 同一列上下两行精度不同。
const fmtMoney = (n: number): string => {
  const v = Number(n || 0)
  const abs = Math.abs(v)
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
// 轴刻度专用: 刻度的作用是看量级, 不是看精度 —— "300.00万" 会白占宽度,
// 还会被 iOS 的窄图区挤成两行
const fmtAxis = (n: number): string => {
  const v = Number(n || 0)
  const abs = Math.abs(v)
  if (abs >= 1e8) return (v / 1e8).toFixed(1) + '亿'
  if (abs >= 1e4) return Math.round(v / 1e4) + '万'
  return String(Math.round(v))
}
const fmtInt = (n: number): string => {
  const v = Math.round(Number(n || 0))
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN')
}

/**
 * 来源结构兼容层。
 *
 * 出现过 "来源: 订单表 undefined ~ undefined": 前端产物还在用数组下标(`s.orders[0]`),
 * 而接口已改成对象 `{from,to,days}` —— `obj[0]` 恒为 undefined。两类结构都认,
 * 缺字段就整段不显示, 不允许把 undefined 打到界面上。
 */
const readSeg = (v: any): { from: string; to: string; days: number } | null => {
  if (!v) return null
  if (Array.isArray(v)) {
    return v[0] && v[1] ? { from: String(v[0]), to: String(v[1]), days: 0 } : null
  }
  if (!v.from || !v.to) return null
  return { from: String(v.from), to: String(v.to), days: Number(v.days || 0) }
}

export default function HistoryPage() {
  const channel = useAppStore(s => s.channel)
  // 2026-11-02 S2-9: 时间区间/粒度改由锤子菜单(HammerHistory)控制, 页面只读 store。
  //   start/end/by 不再本地 useState —— 锤子面板卸载本地态就没了, 页面无法联动。
  //   preset 只在锤子菜单内有意义(选中态), 页面不订阅。
  const start = useAppStore(s => s.histStart)
  const end = useAppStore(s => s.histEnd)
  const by = useAppStore(s => s.histBy)
  const [topBy, setTopBy] = useState<'sku' | 'store'>('sku')
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
    // 2026-11-02 S2-9: 接入全局同步胶囊 —— 本页请求不在 store.loadAll 里,
    //   原本 header 胶囊对历史页一动不动, 用户点完锤子看不出"在拉数"。
    //   +1/-1 严格配对(含过期响应 early-return 分支), 否则胶囊永久转圈。
    const _st = useAppStore.getState()
    _st.setSyncing(1)
    let _settled = false
    const _settle = () => {
      if (_settled) return
      _settled = true
      useAppStore.getState().setSyncing(-1)
    }
    const ch = encodeURIComponent(channel || 'jd')
    const q = `channel=${ch}&start=${start}&end=${end}&t=${freshStamp()}`
    // 2026-11-02 S2-9(用户定案): coverage 不再由页面请求 —— 归档说明在页面上一处不留,
    //   全部由锤子菜单(HammerHistory)自己拉自己渲染。页面少一次往返。
    Promise.allSettled([
      api.get(`/api/history/summary?${q}&by=${by}`),
      api.get(`/api/history/top?${q}&by=${topBy}&limit=20`),
      api.get(`/api/history/yoy?channel=${ch}&days=30&t=${freshStamp()}`),
    ])
      .then(([s, t0, y]) => {
        if (my !== seq.current) return
        const val = (r: any) => (r.status === 'fulfilled' ? r.value?.data : null)
        setSum(val(s))
        setTop(val(t0))
        setYoy(val(y))
        if (s.status === 'rejected') setErr(s.reason)
        setLoading(false)
      })
      .finally(_settle)
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, start, end, by, topBy])

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
      yAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: (v: number) => fmtAxis(v) } },
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

  // 来源 = 真的有数据的那段(后端按 src 分组统计), 不是"本来打算查的那段"。
  // 2026-11-02 S2-9: 原来每段都带自己 "a ~ b" 日期, 与 hist-meta 首行的生效区间
  //   逐字重复(用户点名的那条冗余)。这里改为"哪几段来源 + 各多少天": 覆盖到哪段
  //   区间已由首行说明, 来源行只补"分了几段、各段多长"这层信息。
  const srcText = useMemo(() => {
    const s = sum?.sources || {}
    const unit = by === 'month' ? ' 个月' : ' 天'
    const parts: string[] = []
    const a = readSeg(s.archive)
    const o = readSeg(s.orders)
    if (a) parts.push(`归档 ${a.days}${unit}`)
    if (o) parts.push(`订单 ${o.days}${unit}`)
    return parts.join(' · ')
  }, [sum, by])

  return (
    <div className={'hist-page' + (loading ? ' is-loading' : '')}>
      {/* 2026-11-02 S2-9: 头部不再包卡片 —— "标题+覆盖摘要+区间"做成裸排版(与
          section-title 同层级), 而其它内容卡片保留 card。原先是"卡片套标题",
          视觉上像多了一层冗余容器; 且首卡片 4 个 card 里只有它没有可操作内容。 */}
      <div className="hist-headcard">
        <div className="hist-head">
          <span className="hist-title">历史归档</span>
        </div>
        {/* 2026-11-02 S2-9(用户定案): 页面上不留任何归档说明 —— 状态、覆盖区间、
            "订单自 X 起满 N 天后自动归档"全部由锤子菜单底部承接(.hist-arc)。
            页面首屏只回答"现在查的是哪段区间"。 */}

        {/* 2026-11-02 S2-9: 区间/粒度控件收敛到锤子菜单; 页面不再重复回显
            (hist-meta 已有生效区间日期, 锤子按钮 header 常驻随时可调) */}

        <div className="hist-meta">
          {/* 2026-11-02 S2-9: 接口偶发失败时 sum 是错误对象(无 start/end), 直接模板串
              会渲染 "undefined ~ undefined" —— 缺字段整段不显示(本页既定原则)
              区间只在这里写一次: 来源行原本也带 "订单 a ~ b", 与首行逐字重复
              (2026-11-02 用户指出), 现只描述"哪几段来源 + 各多少天" */}
          <span>
            {sum?.start && sum?.end
              ? `${sum.start} ~ ${sum.end}${sum?.clamped ? '（已截断至 400 天）' : ''}`
              : loading
                ? '加载中…'
                : ''}
          </span>
          {srcText ? <span>来源 {srcText}</span> : null}
        </div>
      </div>

      {err && !sum ? (
        <div className="card">
          <ErrorRetry error={err} onRetry={load} />
        </div>
      ) : null}

      <div className="card">
        <div className="section-title">区间汇总</div>
        {/* 2026-11-02 S2-9: 首载(无任何数据)走骨架, 刷新(有旧值)走淡出过渡 ——
            原来一律显示四个 "—", 用户分不清"在加载"和"真的没数" */}
        <div className={'hist-kpis' + (loading && !sum ? ' is-fresh' : '')}>
          {[
            { k: '金额', v: fmtMoney(totals.gmv), s: '元' },
            { k: '单数', v: fmtInt(totals.orders), s: '单' },
            { k: '件数', v: fmtInt(totals.qty), s: '件' },
            { k: '日均', v: fmtMoney(totals.avg_gmv_per_day), s: '元/天' },
          ].map(x => (
            <div key={x.k}>
              <div className="hist-kpi-k">{x.k}</div>
              {/* 单位跟在数值右侧而不是另起一行: 另起一行时 18px 数字的行高会在中间
                  留出一道空, 视觉上单位像独立的第四行 */}
              <div className="hist-kpi-v">
                {loading && !sum ? (
                  <span className="skeleton hist-kpi-skel" />
                ) : !totals.orders ? (
                  '—'
                ) : (
                  x.v
                )}
                <span className="hist-kpi-unit">{x.s}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="hist-note hist-caveat">
          金额按订单原价合计(含未支付/退款)，与看板 GMV 口径不同
        </div>
        {loading && !sum ? (
          <div className="skeleton hist-chart-skel" />
        ) : empty ? (
          // 2026-11-02 S2-9(用户定案): 页面不留任何归档说明 —— 空态只说"这段没数据",
          // "为什么空/哪天起会有"统一由锤子菜单的归档状态承接, 不再做第二份兜底
          <div className="hist-empty">该区间没有数据</div>
        ) : (
          <Chart option={chartOption} height={window.innerWidth <= 600 ? 220 : 260} />
        )}
      </div>

      <div className="card">
        <div className="hist-title-row">
          <span className="section-title hist-title-flat">Top 20</span>
          <span className="hist-seg">
            {(['sku', 'store'] as const).map(k => (
              <span
                key={k}
                onClick={() => setTopBy(k)}
                className={'hist-seg-item' + (topBy === k ? ' on' : '')}
              >
                {k === 'sku' ? 'SKU' : '店铺'}
              </span>
            ))}
          </span>
        </div>
        {(top?.items || []).length === 0 ? (
          <div className="hist-empty">无数据</div>
        ) : (
          (top.items as any[]).map((it, i) => (
            <div key={it.key + i} className="hist-row-item">
              <span className="hist-rank">{i + 1}</span>
              <span className="hist-name" title={it.key}>
                {it.key || '(空)'}
              </span>
              <span className="hist-amt">{fmtMoney(it.gmv)}</span>
              <span className="hist-share">{it.share}%</span>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <div className="section-title">同比 · 近 30 天</div>
        {!yoy ? (
          <div className="hist-empty">加载中…</div>
        ) : !yoy.has_prev ? (
          <div className="hist-note">
            {yoy.prev?.start
              ? `去年同期 ${yoy.prev.start} ~ ${yoy.prev.end} 无数据`
              : '去年同期无数据'}
            ，暂不做同比
          </div>
        ) : (
          <>
            <div className="hist-yoy-meta">
              {yoy.cur.start} ~ {yoy.cur.end} vs {yoy.prev.start} ~ {yoy.prev.end}
            </div>
            {(['gmv', 'orders', 'qty'] as const).map(k => {
              const d = yoy.delta?.[k]
              const cls = d === null || d === undefined ? 'na' : d >= 0 ? 'up' : 'down'
              return (
                <div key={k} className="hist-row-item">
                  <span className="hist-name">
                    {k === 'gmv' ? '金额' : k === 'orders' ? '单数' : '件数'}
                  </span>
                  <span className="hist-amt">
                    {k === 'gmv' ? fmtMoney(yoy.cur[k]) : fmtInt(yoy.cur[k])}
                  </span>
                  <span className="hist-share hist-share-wide">
                    去年 {k === 'gmv' ? fmtMoney(yoy.prev[k]) : fmtInt(yoy.prev[k])}
                  </span>
                  <span className={'hist-delta ' + cls}>
                    {cls === 'na' ? '—' : (d >= 0 ? '+' : '') + d + '%'}
                  </span>
                </div>
              )
            })}
          </>
        )}
      </div>
    </div>
  )
}
