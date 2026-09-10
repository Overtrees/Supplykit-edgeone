import { useEffect, useRef, useState } from 'react'

interface ChartProps {
  option: Record<string, any>
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
    const handler = (e) => setTheme(e.matches ? 'dark' : 'light')
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  useEffect(() => {
    if (!ref.current) return
    const timer = setTimeout(() => {
      if (!ref.current) return
      try {
        if (inst.current) { inst.current.dispose(); inst.current = null }
        const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
        const cs = getComputedStyle(document.documentElement)
        const textColor = cs.getPropertyValue('--text').trim() || '#0f172a'
        const mutedColor = cs.getPropertyValue('--muted').trim() || 'var(--muted)'
        const opt = {
          backgroundColor: 'transparent',
          ...option,
          textStyle: { color: textColor, ...option.textStyle },
          title: { ...option.title, textStyle: { color: textColor, ...(option.title?.textStyle || {}) } },
          ...(option.legend ? { legend: { ...option.legend, textStyle: { color: mutedColor, ...(option.legend.textStyle || {}) } } } : {}),
          ...(option.series ? {
            series: (Array.isArray(option.series) ? option.series : [option.series]).filter(Boolean).map(s => ({
              ...s,
              label: s.label ? { ...s.label, color: textColor, textBorderColor: 'transparent', ...(s.label.color ? {color: s.label.color} : {}) } : s.label,
            }))
          } : {}),
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
        return () => { window.removeEventListener('resize', resize); try { chart.dispose() } catch(e) {} }
      } catch(e) { console.error('Chart error:', e) }
    }, 100)
    return () => clearTimeout(timer)
  }, [option, theme])
  return <div ref={ref} style={{ width: '100%', height }} />
}