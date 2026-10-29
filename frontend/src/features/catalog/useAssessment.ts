import { useEffect, useState } from 'react'
import { api, type Assessment, type RunSummary } from '../../api/client'

export const assessmentFinished = (value: Assessment) =>
  !['queued', 'running'].includes(value.state)

export function useAssessment(id: string) {
  const [value, setValue] = useState<Assessment | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    setValue(null)
    setError('')
    if (!id) return
    async function load() {
      try {
        const result = await api<Assessment>(`/relevance-assessments/${id}`, {
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        setValue(result)
        if (!assessmentFinished(result)) {
          async function poll() {
            try {
              const run = await api<RunSummary>(`/runs/${result.run_id}/status`, {
                signal: controller.signal,
              })
              if (controller.signal.aborted) return
              if (run.status === 'finished') await load()
              else timer = setTimeout(poll, 2000)
            } catch (reason) {
              if (!controller.signal.aborted) setError((reason as Error).message)
            }
          }
          timer = setTimeout(poll, 1000)
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError((reason as Error).message)
      }
    }
    void load()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [id])
  return { assessment: value?.id === id ? value : null, assessmentError: error }
}
