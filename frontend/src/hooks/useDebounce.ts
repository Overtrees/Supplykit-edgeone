import { useState, useEffect } from 'react'

export function useDebouncedSearch(
  initialValue: string,
  onChange: (v: string) => void,
  delay = 300,
) {
  const [local, setLocal] = useState(initialValue)
  useEffect(() => {
    setLocal(initialValue)
  }, [initialValue])
  useEffect(() => {
    const _tid = setTimeout(() => {
      if (local !== initialValue) onChange(local)
    }, delay)
    return () => clearTimeout(_tid)
  }, [local])
  return [local, setLocal] as const
}
