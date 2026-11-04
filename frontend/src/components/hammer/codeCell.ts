/**
 * 码值单元格(条码 / SKU / 订单号 / 供应商编号)的尺寸与渲染。
 *
 * 为什么要单独一个文件: 2026-11-04 订单页出现「69 码与订单号平行重叠」——
 * 两个字符串的像素直接叠印在一起。复盘出三条独立但会叠加的成因, 缺一不可:
 *
 * ① **列宽被钉死**: `.col-sku { max-width: 140px }` 让订单号列固定 145px,
 *    而 `JD-jd059-045376724` 这类单号要 164px;
 * ② **不折行**: 码值列必须 `nowrap`(否则 `break-all` 会把 13 位条码逐字折断,
 *    行高从 58px 撑到 244px, 一屏只放 1~2 行);
 * ③ **溢出可见**: `overflow` 默认 `visible`, ①② 一叠加, 超出的 19px 就骑到
 *    右边条码列的头上 —— 就是用户看到的"平行重叠"。
 *
 * 所以修法不是单点改某个 `!important`, 而是: 列宽**按该表当场最长的那串字符**算
 * (够用就不裁), 再用 `overflow: hidden` 兜底(极端长值截断而不是叠印),
 * 完整值放 `title` 悬浮。
 */
import type { CSSProperties } from 'react'

/**
 * 码值字号(px)。列宽估算必须用**同一个**字号, 否则算出来的宽度和实际渲染对不上。
 * 对应 `--font-xs`(12px); 表格里的码值统一用这个, 不要再各页写 style={{fontSize}}。
 */
export const CODE_FONT_SIZE = 12

/** 单元格水平内边距合计(td 左右各 8px) + 呼吸位, 用于把文字宽加成列宽 */
const CELL_PAD = 16 + 8

/** 等宽字体单字符宽 ≈ 0.62em(Consolas/Menlo 实测 0.60~0.63), 取上限避免偏窄 */
const CHAR_W = 0.62

/** 码值列的宽度上下限(px) */
const MIN_W = 88
const MAX_W = 280

/** 用来量宽的候选值: 只接受非空字符串, 其余跳过 */
function textOf(v: unknown): string {
  if (v == null) return ''
  const s = String(v).trim()
  return s === '-' ? '' : s
}

/**
 * 按一组值里**最长的一串**算列宽。
 *
 * @param values 该列在当前筛选结果里的全部取值(空值/'-' 自动跳过)
 * @param fontSize 字号, 默认 CODE_FONT_SIZE; 传值必须与 cell 实际字号一致
 */
export function codeColWidth(values: unknown[], fontSize = CODE_FONT_SIZE) {
  let longest = ''
  for (const v of values) {
    const s = textOf(v)
    if (s.length > longest.length) longest = s
  }
  let extra = 0 // 非 ASCII(中文/全角)按 1em 宽估, 这里累计多出来的宽度
  for (const ch of longest) {
    if (ch.charCodeAt(0) > 0xff) extra += fontSize * (1 - CHAR_W)
  }
  const textW = longest.length * fontSize * CHAR_W + extra
  const width = Math.min(MAX_W, Math.max(MIN_W, Math.ceil(textW + CELL_PAD)))
  const style: CSSProperties = { width, minWidth: width, maxWidth: width }
  return { width, style, longest }
}
