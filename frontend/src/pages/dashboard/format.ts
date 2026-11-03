/**
 * 看板页的格式化工具(从 DashboardPage.tsx 原样搬出, 2026-11-03 S3-1)
 *
 * 为什么单独成文件: 这 6 个弹窗(sheets/)都要用 fmtWh/fmtInt/alertAge,
 * 留在页面里就得给每个组件重复写一遍 props, 或者把它们塞进组件内部各写一份。
 */

export const periodLabel: Record<string, string> = { today: '今日', week: '上周', month: '上月' }

// 仓库集合标签截断: 后端 warehouse 可能为 "北京仓,上海仓,成都仓" 集合(逗号连接) → 前2仓+等N仓, title 给完整
export const fmtWh = (w?: string | null): string => {
  if (!w) return ''
  // 2026-10-30 哨兵维度转人话: __BC__(BBCC 一盘 = C+B 合计) / __C__(全国 C 仓) / __ALL__(全仓)
  //   原样输出会把内部标识直接暴露给用户(低库存弹窗显示 "__BC__")
  const _sw = String(w)
  if (_sw === '__BC__') return 'BC'
  if (_sw === '__C__') return 'C'
  if (_sw === '__ALL__') return '全国'
  const p = _sw.split(',').filter(Boolean)
  if (p.length <= 2) return p.join(',')
  return p.slice(0, 2).join(',') + '等' + p.length + '仓'
}

// 2026-10-26 数字降维(看板卡片金额/大数字超 5 位显示问题):
// 金额 fmtBig: ≥1亿 → x.xx亿 / ≥10万(超 5 位) → x.xx万(保留 2 位小数) / 否则千分位
// 2026-10-27 位数阈值按屏幕: 手机窄屏(≤932, 含竖屏/横屏)≥5位(1e4)即降维(空间紧凑);
// 非窄屏(桌面/平板)保留 ≥10万(1e5)完整位数; 亿档两模式一致(大数必降)
export const isNarrow = () => typeof window !== 'undefined' && window.innerWidth <= 932

export const fmtBig = (n: number): string => {
  const v = Number(n || 0)
  const abs = Math.abs(v)
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (abs >= (isNarrow() ? 1e4 : 1e5)) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN')
}

// 计数值 fmtInt: 窄屏≥5位/非窄屏≥6位 → 整数万; 否则原样(计数无千分位逗号)
export const fmtInt = (n: number): string => {
  const v = Math.round(Number(n || 0))
  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + '万'
  return String(v)
}

// 告警持续天数: 后端 created_at 为 "YYYY-MM-DD HH:mm:ss", Safari 不认空格分隔 → 换 T
export const alertAge = (c?: string | null): string => {
  if (!c) return ''
  try {
    const d = Math.floor((Date.now() - new Date(String(c).replace(' ', 'T')).getTime()) / 86400000)
    return d >= 1 ? ' · 持续' + d + '天' : ''
  } catch (e) {
    return ''
  }
}

// 濒临断货三级(P0): red=击穿补货周期(紧急) / orange=逼近且缓冲破位(预警) / yellow=缓冲破位时间尚够(关注)
export const RISK_LV: Record<string, { c: string; t: string }> = {
  red: { c: 'var(--danger)', t: '紧急' },
  orange: { c: 'var(--warning)', t: '预警' },
  yellow: { c: 'var(--accent-yellow)', t: '关注' },
}

/**
 * 弹窗内 SKU 仓库标签(跟随补货模式): bbcc→BC合并+自有; 传统→C+自有(不涉B)
 *
 * 原为 DashboardPage 内的 `_whTag`, 依赖闭包里的 _replMode —— 搬出来后模式必须显式传参,
 * 否则"弹窗里写死 bbcc"这种错只在 other 渠道才看得见。
 */
export const whTag = (wt?: string | null, replMode?: string): string => {
  if (wt === 'own') return '自有'
  if (wt === 'bc') return 'BC'
  if (wt === 'platform_b' || wt === 'platform')
    return replMode === 'bbcc' ? 'BC' : wt === 'platform' ? 'C' : 'B'
  return ''
}
