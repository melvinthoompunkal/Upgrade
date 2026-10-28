import { useEffect, useState } from 'react'
import { api, type Event, type EventPage, type Run } from '../../api/client'

export function useRun(id: string) {
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [error, setError] = useState('')
  const [reconnecting, setReconnecting] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let after = 0
    let failures = 0
    let received = false
    setRun(null)
    setEvents([])
    setError('')
    setReconnecting(false)
    async function poll() {
      try {
        const detail = await api<Run>(`/runs/${encodeURIComponent(id)}`, {
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        received = true
        setRun(detail)
        setError('')
        setReconnecting(false)
        failures = 0
        const page = await api<EventPage>(`/runs/${encodeURIComponent(id)}/events?after=${after}`, {
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        if (page.items.length) {
          after = page.next_cursor
          setEvents((previous) => [
            ...previous,
            ...page.items.filter(
              (item) => !previous.some((existing) => existing.sequence === item.sequence),
            ),
          ])
        }
        if (detail.status !== 'finished' || page.items.length === 100)
          timer = setTimeout(poll, 1000)
      } catch (reason) {
        if (controller.signal.aborted) return
        failures++
        if (received) setReconnecting(true)
        else setError((reason as Error).message)
        timer = setTimeout(poll, Math.min(1000 * 2 ** failures, 12000))
      }
    }
    void poll()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [id])
  return { run, events, error, reconnecting }
}
