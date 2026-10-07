import { useEffect } from 'react'
export default function useKeyboard(handlers) {
  useEffect(() => {
    const handler = e => {
      for (const [key, fn] of Object.entries(handlers)) {
        const parts = key.split('+')
        const match = parts.every(p => {
          if (p === 'meta') return e.metaKey
          if (p === 'ctrl') return e.ctrlKey
          if (p === 'shift') return e.shiftKey
          if (p === 'alt') return e.altKey
          if (p === 'esc') return e.key === 'Escape'
          if (p === 'enter') return e.key === 'Enter'
          if (p === 'left') return e.key === 'ArrowLeft'
          if (p === 'right') return e.key === 'ArrowRight'
          return e.key.toLowerCase() === p.toLowerCase()
        })
        if (match) {
          e.preventDefault()
          fn(e)
          return
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handlers])
}
