import { useState } from 'react'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { useWorkspace } from '../../app/workspace-context'
import { ErrorState, Notice, SectionHeading } from '../../components/ui'

type Usage = components['schemas']['UsageView']
type Cleanup = components['schemas']['CleanupView']

export function DataLifecycle() {
  const { refresh } = useWorkspace()
  const [usage, setUsage] = useState<Usage | null>(null)
  const [receipt, setReceipt] = useState<Cleanup | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function review() {
    setBusy(true)
    setError('')
    try {
      setUsage(await api<Usage>('/usage'))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  async function remove() {
    setBusy(true)
    setError('')
    try {
      setReceipt(await api<Cleanup>('/privacy/data', { method: 'DELETE' }))
      setConfirm(false)
      setUsage(null)
      refresh()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="mt-9" aria-label="Saved data and limits">
      <SectionHeading title="Saved data and limits">
        Review this account’s allowances and remove its saved workspace data.
      </SectionHeading>
      <div className="surface p-6 grid gap-4">
        <button
          className="button-secondary justify-self-start"
          disabled={busy}
          onClick={() => void review()}
        >
          Review storage and limits
        </button>
        {usage && (
          <>
            <p className="text-sm">
              {usage.unfinished_jobs} of {usage.owner_job_limit} jobs unfinished ·{' '}
              {Math.ceil(usage.reserved_bytes / 1024 / 1024)} MiB reserved of{' '}
              {Math.floor(usage.storage_limit_bytes / 1024 / 1024)} MiB.
            </p>
            <p className="text-muted text-sm">
              Reservations include space held for active jobs. Private snapshots expire after{' '}
              {usage.retention_days} days; public workspace data expires after{' '}
              {usage.retention_days} days of inactivity. Active jobs delay cleanup.
            </p>
            <dl className="grid gap-2 text-sm">
              {Object.entries(usage.daily_limits).map(([key, limit]) => (
                <div className="flex flex-wrap justify-between gap-2" key={key}>
                  <dt>{key.replaceAll('_', ' ')}</dt>
                  <dd>
                    {(usage.daily_usage[key] ?? 0).toLocaleString()} / {limit.toLocaleString()}{' '}
                    today (UTC)
                  </dd>
                </div>
              ))}
            </dl>
            <p className="text-muted text-sm">
              Provider usage can be unknown after an interrupted request. These allowances are not a
              billing guarantee. Private model analysis and hosted execution remain disabled.
            </p>
            <label className="flex items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="project-check mt-1"
                checked={confirm}
                disabled={busy || usage.unfinished_jobs > 0}
                onChange={(event) => setConfirm(event.target.checked)}
              />
              <span>
                Delete my saved runs, catalogs, assessments, pinned code and model consents. This
                permanently removes their local exports.
              </span>
            </label>
            <button
              className="button-secondary justify-self-start"
              disabled={!confirm || busy || usage.unfinished_jobs > 0}
              onClick={() => void remove()}
            >
              Delete saved workspace data
            </button>
            {usage.unfinished_jobs > 0 && (
              <p className="field-help">Finish or cancel your jobs before deleting data.</p>
            )}
          </>
        )}
        {receipt && (
          <Notice>
            Deletion {receipt.state}. Removed {receipt.deleted_count} storage directories. Receipt:{' '}
            <code className="break-all">{receipt.id}</code>.
          </Notice>
        )}
        {error && <ErrorState message={error} />}
      </div>
    </section>
  )
}
