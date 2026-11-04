import { useEffect, useRef, useState } from 'react'

interface ChartSub {
  textStyle?: Record<string, unknown>
  [k: string]: unknown
}

interface ChartSeries {
  label?: ChartSub
  [k: string]: unknown
}

interface ChartOption {
  textStyle?: ChartSub
  title?: ChartSub
  legend?: ChartSub
  tooltip?: ChartSub
  series?: ChartSeries | ChartSeries[]
  [k: string]: unknown
}

interface ChartProps {
  option: ChartOption
  height?: number
}
// 按需引入 ECharts 组件，减少打包体积
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'

echarts.use([BarChart, LineChart, CanvasRenderer, GridComponent, TooltipComponent, LegendComponent])

export default function Chart({ option, height = 260 }: ChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const [theme, setTheme] = useState<'light' | 'dark'>('light')

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    setTheme(mq.matches ? 'dark' : 'light')
    const handler = e => setTheme(e.matches ? 'dark' : 'light')
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  useEffect(() => {
    if (!ref.current) return
    // 2026-11-02 S0: setTimeout 内部的 return 会被 **setTimeout 丢弃**(不是 effect cleanup),
    // 于是每次 option/theme 变化都新增一个 resize 监听 + 一个 ResizeObserver, 永不释放。
    // 看板 8 个图表 × 每 30s 刷新一轮 = 每小时上千个僵尸监听器, 内存与 resize 抖动持续累积。
    // 改为把内部清理函数存进 cleanup 变量, 由外层 effect cleanup 统一调用。
    let cleanup: (() => void) | null = null
    const timer = setTimeout(() => {
      if (!ref.current) return
      try {
        if (inst.current) {
          inst.current.dispose()
          inst.current = null
        }
        const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
        const cs = getComputedStyle(document.documentElement)
        const textColor = cs.getPropertyValue('--text').trim() || '#0f172a'
        const mutedColor = cs.getPropertyValue('--muted').trim() || 'var(--muted)'
        const opt = {
          backgroundColor: 'transparent',
          ...option,
          textStyle: { color: textColor, ...option.textStyle },
          title: {
            ...option.title,
            textStyle: { color: textColor, ...(option.title?.textStyle || {}) },
          },
          ...(option.legend
            ? {
                legend: {
                  ...option.legend,
                  textStyle: { color: mutedColor, ...(option.legend.textStyle || {}) },
                },
              }
            : {}),
          ...(option.series
            ? {
                series: (Array.isArray(option.series) ? option.series : [option.series])
                  .filter(Boolean)
                  .map(s => ({
                    ...s,
                    label: s.label
                      ? {
                          ...s.label,
                          color: textColor,
                          textBorderColor: 'transparent',
                          ...(s.label.color ? { color: s.label.color } : {}),
                        }
                      : s.label,
                  })),
              }
            : {}),
          tooltip: {
            ...option.tooltip,
            backgroundColor: theme === 'dark' ? 'rgba(30,41,59,0.95)' : 'rgba(255,255,255,0.95)',
            borderColor: theme === 'dark' ? '#334155' : '#e2e8f0',
            borderRadius: 26,
            textStyle: { color: textColor, ...(option.tooltip?.textStyle || {}) },
          },
        }
        chart.setOption(opt)
        inst.current = chart
        const resize = () => chart.resize()
        window.addEventListener('resize', resize)
        // 容器尺寸自适应(tab 切换/布局变化时宽度变化 → resize, 防柱形溢出卡片)
        let ro: ResizeObserver | null = null
        if (typeof ResizeObserver !== 'undefined') {
          ro = new ResizeObserver(() => chart.resize())
          ro.observe(ref.current)
        }
        cleanup = () => {
          window.removeEventListener('resize', resize)
          if (ro) ro.disconnect()
          try {
            chart.dispose()
          } catch (e) {}
          if (inst.current === chart) inst.current = null
        }
      } catch (e) {
        console.error('Chart error:', e)
      }
    }, 100)
    return () => {
      clearTimeout(timer)
      if (cleanup) {
        try {
          cleanup()
        } catch (e) {}
      }
    }
  }, [option, theme])
  return <div ref={ref} style={{ width: '100%', height }} />
}
