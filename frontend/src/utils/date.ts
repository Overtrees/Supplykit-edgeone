/** 业务日期统一按**北京时间(UTC+8)**计算
 *
 * 为什么不能直接用 toISOString().slice(0,10):
 *   它取的是 UTC 日期。北京时间 00:00–08:00 之间, UTC 还在昨天 → 页面"今天"错一天;
 *   每月 1 号 00:00–08:00 更会把"本月"算成上个月, 与后端(北京时间自然月)整月对不上。
 */

const CN_OFFSET_MS = 8 * 3600 * 1000

/** 位移后的 Date: 其 getUTC* / toISOString 的日期部分等于北京时间墙上日期 */
export function cnNow(): Date {
  return new Date(Date.now() + CN_OFFSET_MS)
}

/** 北京时间今天 YYYY-MM-DD */
export function todayCn(): string {
  return cnNow().toISOString().slice(0, 10)
}

/** 北京时间 N 天前 YYYY-MM-DD(含当天: 29 = 近 30 天窗口起点) */
export function daysAgoCn(n: number): string {
  return new Date(Date.now() + CN_OFFSET_MS - n * 86400000).toISOString().slice(0, 10)
}

/** YYYY-MM-DD 距北京时间今天的天数(可为负); 空/非法返回 0 */
export function daysSinceCn(dateStr: string): number {
  const s = String(dateStr || '').slice(0, 10)
  if (!s) return 0
  const ms = Date.parse(s + 'T00:00:00+08:00')
  if (Number.isNaN(ms)) return 0
  const todayMs = Date.parse(todayCn() + 'T00:00:00+08:00')
  return Math.round((todayMs - ms) / 86400000)
}
