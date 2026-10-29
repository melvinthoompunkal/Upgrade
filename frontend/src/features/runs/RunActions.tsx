import { useState } from 'react'
import { api, type RunSummary } from '../../api/client'
import { ErrorState } from '../../components/ui'

export function RunActions({ run }: { run: RunSummary }) {
  const [requested, setRequested] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (run.status === 'finished') return null
  async function cancel() {
    setBusy(true)
    setError('')
    try {
      await api<RunSummary>(`/runs/${encodeURIComponent(run.id)}/cancel`, { method: 'POST' })
      setRequested(true)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const stopping = requested || run.cancel_requested
  return (
    <div className="my-4 flex flex-wrap items-center gap-3">
      <button
        className="button-secondary"
        disabled={busy || stopping}
        onClick={() => void cancel()}
      >
        {stopping ? 'Cancellation requested' : busy ? 'Requesting cancellation…' : 'Cancel run'}
      </button>
      <p className="text-muted text-sm" role="status">
        {stopping
          ? 'Stopping owned work and preserving intact evidence.'
          : 'Leaving this page keeps the run active.'}
      </p>
      {error && <ErrorState message={error} />}
    </div>
  )
}
