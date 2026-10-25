import { useEffect } from 'react'

/** 液态玻璃动态光追: 指针移动 → 写 --light-x/--light-y 全局 CSS 变量(rAF 节流, 默认 30%/30% 兜底) */
export default function useLightTracker(): void {
  useEffect(() => {
    let raf = 0
    const onMove = (e: PointerEvent) => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const root = document.documentElement
        const x = Math.max(-50, Math.min(150, (e.clientX / window.innerWidth) * 100))
        const y = Math.max(-50, Math.min(150, (e.clientY / window.innerHeight) * 100))
        root.style.setProperty('--light-x', `${x}%`)
        root.style.setProperty('--light-y', `${y}%`)
      })
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      window.removeEventListener('pointermove', onMove)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])
}
