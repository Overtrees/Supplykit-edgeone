/**
 * 枚举值的展示文案: 仓库类型 / 订单状态 / 效期档位 / 批次消耗档位。
 *
 * 共同点: **取值是后端契约(中文或英文码), 不能改**; 要本地化的是"显示"这一层。
 * 所以这里只做 值 → i18n 键 的映射, 页面不再写 `es === 'ok' ? '✓ 正常' : ...` 这类三元。
 */
import { t } from '../locale'

/** 仓库类型(own/platform/platform_b): 与后端 warehouse_type 取值一致 */
export function whTypeLabel(wt: string): string {
  if (wt === 'own') return t('inv.own')
  if (wt === 'platform') return t('inv.platform')
  if (wt === 'platform_b') return t('inv.warehouse_b')
  return wt || ''
}

/**
 * 订单状态: 库里存中文(导入文件原样), 不做数据迁移 —— 只把 5 个已知值映射到词条,
 * 未知值原样显示(用户数据不动)。
 */
const ORDER_STATUS_KEYS: Record<string, string> = {
  已完成: 'oms.st.done',
  待发货: 'oms.st.to_ship',
  已发货: 'oms.st.shipped',
  待确认: 'oms.st.to_confirm',
  申请退款: 'oms.st.refund',
}

export function orderStatusLabel(v: string): string {
  if (!v) return t('common.all')
  const k = ORDER_STATUS_KEYS[v]
  return k ? t(k) : v
}

/**
 * 订单阶段分布的桶名(后端 dashboard funnel 的 name 是状态原值 + "总订单")。
 * 与 orderStatusLabel 同源映射, 未知值原样显示。
 */
export function orderStageLabel(v: string): string {
  if (v === '总订单') return t('dash.funnel_total')
  return orderStatusLabel(v)
}

/** 订单状态 pill 配色: 库里存的是导入原样中文, 按关键词判档; 未知值给 warning */
export function orderStatusTone(status: string): 'success' | 'danger' | 'info' | 'warning' {
  const s = status || ''
  if (s.includes('完成') || s.includes('签收') || s.includes('收货')) return 'success'
  if (s.includes('退款') || s.includes('取消') || s.includes('退货') || s.includes('售后'))
    return 'danger'
  if (s.includes('发货') || s.includes('出库')) return 'info'
  return 'warning'
}

/** 效期档位码白名单 —— 后端只可能有这四种; 未登记值渲染 '-' 而不是裸键名 */
const EXP_STATUS_CODES = new Set(['ok', 'warn', 'no', 'expired'])

/**
 * 效期档位(批次/行级): 后端给 ok / warn / no / expired 四种码。
 * 带符号的写法只在表格里用(密度高), 语义与文字一致, 便于扫读。
 */
export function expiryStatusLabel(code: string, withIcon = true): string {
  // 2026-11-06: 原来先选字面量 key 再 `t(key + '_icon')`, 审计脚本只认前缀拼接,
  //   于是 3 条 `inv.exp.*_icon` 被算成死键。改成直接前缀拼接(审计可识别), 但保留
  //   白名单兜底 —— 后端若返回未登记的档位码, 仍返回 '-' 而不是渲染出裸键名。
  if (!EXP_STATUS_CODES.has(code)) return '-'
  return withIcon ? t('inv.exp.' + code + '_icon') : t('inv.exp.' + code)
}

/** 效期档位的颜色变量(与文案同源, 避免两处各写一套 if) */
export function expiryStatusColor(code: string): string {
  return code === 'ok'
    ? 'var(--success)'
    : code === 'warn'
      ? 'var(--warning)'
      : code === 'no'
        ? 'var(--danger)'
        : code === 'expired'
          ? 'var(--accent-purple)'
          : 'var(--muted2)'
}

/** 批次消耗进度档位(按已消耗比例): 与库存表「备注」列同口径 */
export function batchUseLabel(pct: number): string {
  return pct >= 67
    ? t('inv.use.half_icon')
    : pct >= 40
      ? t('inv.use.mid_icon')
      : pct > 0
        ? t('inv.use.normal_icon')
        : t('inv.use.fresh_icon')
}

/** 效期动作建议(超 1/3 / 临近 / 过期): 库存表备注列的第二段 */
export function expiryActionLabel(code: string, pct: number): string {
  if (code === 'ok') return pct > 30 ? t('inv.act.slow') : t('inv.act.normal')
  if (code === 'warn') return t('inv.act.promo')
  if (code === 'no') return t('inv.act.clear')
  if (code === 'expired') return t('inv.act.scrap')
  return ''
}
