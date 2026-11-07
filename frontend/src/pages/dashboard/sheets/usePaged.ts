import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'

/**
 * 底部明细窗的「打开才拉、滚到底再拉」分页钩子（2026-11-07 P2）
 *
 * 背景（实测，复用连接 min 口径）：
 *   · 断货 full=1 一次 897KB / 2.19s —— 弹窗首屏只显示一屏约 6 行；
 *   · 缺货 limit=5000 一次 49KB / 1.2s，卡面却只用 `.slice(0,3)`；
 *   · 自有仓健康 200/页看着是分页，单页仍 2.9s —— 因为后端每次把 1.3 万条全算完再切片。
 *
 * 所以这里做两件事，缺一不可：
 *   ① **打开才拉**：卡面用首屏已有的轻量数据渲染，不为"用户可能点开"预拉全量；
 *   ② **真分页**：翻页请求带 offset，后端 SQL 层切（后端不切页 = 每页重付全量成本，
 *      翻 10 页比一次性拉更慢 —— 这是自有仓健康单页 2.9s 的原因）。
 *
 * 传 `page` 而不是写死 300/500：断货行宽（SKU+仓+天数）适合 300，缺货行窄可以 500。
 */
export interface PagedState<T> {
  rows: T[]
  total: number
  busy: boolean
  end: boolean
  more: () => void
  retry: () => void
}

interface PagedPayload<T> {
  items?: T[]
  total?: number
}

export function usePaged<T>(
  open: boolean,
  buildUrl: (offset: number, page: number) => string,
  page = 300,
  pick?: (payload: unknown) => { items: T[]; total: number },
  key = '',
): PagedState<T> {
  const [rows, setRows] = useState<T[]>([])
  const [total, setTotal] = useState(-1)
  const [busy, setBusy] = useState(false)
  const [end, setEnd] = useState(false)
  const offRef = useRef(0)
  const busyRef = useRef(false)
  const endRef = useRef(false)
  const urlRef = useRef(buildUrl)
  urlRef.current = buildUrl
  const pickRef = useRef(pick)
  pickRef.current = pick

  const load = useCallback(
    function (reset: boolean) {
      if (busyRef.current) return
      if (!reset && endRef.current) return
      busyRef.current = true
      setBusy(true)
      const off = reset ? 0 : offRef.current
      api
        .get<PagedPayload<T> | T[]>(urlRef.current(off, page), { timeout: 45000 })
        .then(function (r) {
          const d = r.data
          // 断货接口是**多维度**结构(bcItems/cItems/ownItems), 由调用方给 pick 收窄成一条列表;
          // 缺省按 {items,total} 解析(缺货/告警都是这个形状)。
          const pk = pickRef.current ? pickRef.current(d) : null
          const add = (pk ? pk.items : Array.isArray(d) ? d : (d && d.items) || []) as T[]
          const tot = pk ? pk.total : Array.isArray(d) ? -1 : Number((d && d.total) ?? -1)
          offRef.current = off + add.length
          if (tot >= 0) setTotal(tot)
          setRows(function (prev) {
            return off === 0 ? add : prev.concat(add)
          })
          // 三种"到底"：不足一页 / 已拿满总数 / 后端明确给了 total 而当前已覆盖
          if (add.length < page || (tot >= 0 && off + add.length >= tot)) {
            endRef.current = true
            setEnd(true)
          }
        })
        .catch(function () {
          // 失败不清空已加载的行；用户可点「加载更多」重试（或关掉重开）
        })
        .then(function () {
          busyRef.current = false
          setBusy(false)
        })
    },
    [page],
  )

  useEffect(
    function () {
      if (!open) return
      offRef.current = 0
      endRef.current = false
      setRows([])
      setEnd(false)
      setTotal(-1)
      load(true)
    },
    [open, load, key],
  )

  const more = useCallback(
    function () {
      load(false)
    },
    [load],
  )
  const retry = useCallback(
    function () {
      load(false)
    },
    [load],
  )

  return { rows, total, busy, end, more, retry }
}

/** 滚动容器触底判定（离底 80px 内算触底，避免像素抖动反复触发） */
export function atBottom(e: { currentTarget: HTMLElement }): boolean {
  const el = e.currentTarget
  return el.scrollTop + el.clientHeight >= el.scrollHeight - 80
}
