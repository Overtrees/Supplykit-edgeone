/**
 * 渠道显示名: 内置渠道走 i18n 键, 自定义渠道原样显示(那是用户自己的数据)。
 */
import { t } from '../locale'

export interface ChannelLike {
  code?: string
  name?: string
  name_key?: string
}

/** 渠道展示名: name_key → t(); 内置渠道(jd/other)即使接口漏发 name_key 也走 i18n
 *  (2026-11-04: 英文界面头部胶囊曾显示中文"京东" —— 接口表在首屏未到/缓存旧结构时
 *   只有 name, 而 name 是库里的中文数据); 自定义渠道回落 name 原文(用户自己的数据) */
const BUILTIN_KEYS: Record<string, string> = {
  jd: 'channel.jd',
  other: 'channel.other',
}

/** 渠道展示名: name_key → t(), 否则回落 name, 再否则回落 code */
export function channelName(c: ChannelLike | undefined | null): string {
  if (!c) return ''
  const key = c.name_key || BUILTIN_KEYS[String(c.code || '')] || ''
  if (key) return t(key)
  return c.name || c.code || ''
}

/** 接口未返回渠道表时的兜底(与后端 channels 表首屏默认一致) */
export const FALLBACK_CHANNELS: ChannelLike[] = [
  { code: 'jd', name_key: 'channel.jd' },
  { code: 'other', name_key: 'channel.other' },
]
