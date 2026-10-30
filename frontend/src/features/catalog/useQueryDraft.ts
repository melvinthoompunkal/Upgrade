import { useEffect, useState } from 'react'
import type { SetURLSearchParams } from 'react-router-dom'

// Keep keystrokes synchronous while the router commits the shareable query after typing.
export function useQueryDraft(value: string, key: 'q' | 'category', setParams: SetURLSearchParams) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  useEffect(() => {
    if (draft === value) return
    const timer = setTimeout(() => {
      setParams((current) => {
        const next = new URLSearchParams(current)
        if (draft) next.set(key, draft)
        else next.delete(key)
        next.delete('cursor')
        next.delete('feature')
        return next
      })
    }, 300)
    return () => clearTimeout(timer)
  }, [draft, value, key, setParams])
  return [draft, setDraft] as const
}
