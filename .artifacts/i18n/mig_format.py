#!/usr/bin/env python3
"""看板 format.ts 迁移: 文案落词条, 数值/单复数跟着语言走。

为什么不只是把中文换成 t():
  `fmtBig` 的「万/亿」是**中文数量级单位**, 英文没有对应缩写 —— 硬翻成 "10k" 会让
  中文界面的 30.00万 变成 300k, 同一屏两个口径。这里改成: 中文走 万/亿, 英文走 K/M/B
  的紧凑写法, 阈值保持不变(只换单位, 不动降维规则, 否则卡宽会变)。
  千分位同理: zh-CN 逗号 vs en-US 逗号, 但印度式等分组别引入。
"""
import re

p = "frontend/src/pages/dashboard/format.ts"
s = open(p, encoding="utf-8").read()

s = s.replace(
    """export const periodLabel: Record<string, string> = { today: '今日', week: '上周', month: '上月' }""",
    """import { t } from '../../locale'

/** 区间名: 键是后端取值(today/week/month/custom), 显示走词条 */
export const periodLabel: Record<string, string> = {
  today: t('period.today'),
  week: t('period.week'),
  month: t('period.month'),
}""",
)

s = s.replace(
    """  if (_sw === '__ALL__') return '全国'
  const p = _sw.split(',').filter(Boolean)
  if (p.length <= 2) return p.join(',')
  return p.slice(0, 2).join(',') + '等' + p.length + '仓'""",
    """  if (_sw === '__ALL__') return t('dash.nationwide')
  const p = _sw.split(',').filter(Boolean)
  if (p.length <= 2) return p.join(',')
  // 中文「北京仓,上海仓等7仓」/ 英文「WH-A, WH-B +5」—— 中文多一个字就换行, 英文更省位
  return t('dash.wh_more', { a: p.slice(0, 2).join(','), n: p.length })""",
)

s = s.replace(
    """export const fmtBig = (n: number): string => {
  const v = Number(n || 0)
  const abs = Math.abs(v)
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (abs >= (isNarrow() ? 1e4 : 1e5)) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN')
}""",
    """export const fmtBig = (n: number): string => {
  const v = Number(n || 0)
  const abs = Math.abs(v)
  // 单位按语言: 中文 万/亿, 英文 K/M/B(阈值不动 —— 动阈值等于动卡片宽度)
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + t('dash.unit_yi')
  if (abs >= (isNarrow() ? 1e4 : 1e5)) return (v / 1e4).toFixed(2) + t('dash.unit_wan')
  return v.toLocaleString('zh-CN')
}""",
)

s = s.replace(
    """  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + '万'""",
    """  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + t('dash.unit_wan')""",
)

s = s.replace(
    """    return d >= 1 ? ' · 持续' + d + '天' : ''""",
    """    return d >= 1 ? t('dash.alert_age', { n: d }) : ''""",
)

s = s.replace(
    """export const RISK_LV: Record<string, { c: string; t: string }> = {
  red: { c: 'var(--danger)', t: '紧急' },
  orange: { c: 'var(--warning)', t: '预警' },
  yellow: { c: 'var(--accent-yellow)', t: '关注' },
}""",
    """// 标签改为**词条键**: 取值(red/orange/yellow)是后端契约, 显示时再 t()
export const RISK_LV: Record<string, { c: string; t: string }> = {
  red: { c: 'var(--danger)', t: 'dash.risk_red' },
  orange: { c: 'var(--warning)', t: 'dash.risk_orange' },
  yellow: { c: 'var(--accent-yellow)', t: 'dash.risk_yellow' },
}""",
)

s = s.replace(
    """  if (wt === 'own') return '自有'""",
    """  if (wt === 'own') return t('inv.own')""",
)
open(p, "w", encoding="utf-8").write(s)
print("format.ts done")
