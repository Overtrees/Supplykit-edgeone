/**
 * 全局提示单一出口(2026-11-03)
 *
 * 为什么需要它 —— 这不是"提示样式"问题, 是**通道断了**:
 * Toast 是 React Context, 只有 <ToastProvider> 内部的组件能拿到实现。而 App 顶层
 * (header / 全局任务轮询)在 Provider **外面** —— useToast() 拿到的是默认 no-op。
 * 于是「种子填充完成」「数据清洗完成」「导出完成」这些**跨页提示从来没显示过**,
 * 用户只有在任务卡里才看得到结果; 失败更是完全静默。
 *
 * 设计: Provider 挂载时把实现注册进来, 任何模块(store / mutate / App 顶层)都能用;
 *      未挂载时自动降级为 no-op, 不抛错 —— 与原有 _noop 兜底同思路。
 */
export type NotifyApi = {
  success: (msg: string) => void
  error: (msg: string) => void
  warning: (msg: string) => void
  info: (msg: string) => void
}

let _impl: NotifyApi | null = null

/** 由 ToastProvider 在挂载/卸载时调用(卸载传 null, 避免指向已销毁的 setState) */
export function bindNotify(api: NotifyApi | null) {
  _impl = api
}

export const notify: NotifyApi = {
  success: m => _impl?.success(m),
  error: m => _impl?.error(m),
  warning: m => _impl?.warning(m),
  info: m => _impl?.info(m),
}
