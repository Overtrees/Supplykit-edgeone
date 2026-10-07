/// <reference types="vite/client" />

// vite.config.js define 注入的全局常量
declare const __APP_VERSION__: string
declare const __BUILD_DATE__: string

// 浏览器调试探针（App.tsx 挂载的全局计数）
interface Window {
  __seedMissCount?: number
  __cleanMissCount?: number
  __setPage?: (v: string) => void
  __hammerReplenMode?: string
}

// 懒加载 IntersectionObserver 挂在 DOM 节点上的 expando 引用（各列表页滚动加载）
interface HTMLDivElement {
  _observer?: IntersectionObserver | null
  _obs?: IntersectionObserver | null
}
