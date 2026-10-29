import { useEffect, useRef, useState } from 'react'
import { api, type RepositoryPage, type Snapshot } from '../../api/client'
import { ErrorState, Notice } from '../../components/ui'

export function PinRepository({ repo }: { repo: RepositoryPage['items'][number] }) {
  const [sha, setSha] = useState('')
  const [role, setRole] = useState<'source' | 'target'>('source')
  const [root, setRoot] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function pin(event: React.FormEvent) {
    event.preventDefault()
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    setSnapshot(null)
    try {
      const result = await api<Snapshot>('/repository-snapshots', {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify({
          repository_id: repo.id,
          commit_sha: sha,
          role,
          project_root: root,
        }),
      })
      if (!controller.signal.aborted) setSnapshot(result)
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message)
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-accent text-sm">Pin a repository snapshot</summary>
      <form className="mt-4 grid gap-4 max-w-xl" onSubmit={(event) => void pin(event)}>
        <p className="text-muted text-sm">
          Fetch the chosen commit into local storage for indexing. This does not send code to Meta.
          General analysis and execution support come in a later milestone.
        </p>
        <label className="grid gap-2 text-sm">
          Full commit SHA
          <input
            required
            pattern="[0-9a-f]{40}([0-9a-f]{24})?"
            maxLength={64}
            value={sha}
            onChange={(event) => setSha(event.target.value)}
            disabled={busy}
          />
        </label>
        <label className="grid gap-2 text-sm">
          Project root (optional)
          <input
            placeholder="e.g. packages/library"
            maxLength={300}
            value={root}
            onChange={(event) => setRoot(event.target.value)}
            disabled={busy}
          />
        </label>
        <label className="grid gap-2 text-sm">
          Repository role
          <select
            value={role}
            onChange={(event) => setRole(event.target.value as typeof role)}
            disabled={busy}
          >
            <option value="source">Source</option>
            <option value="target">Target</option>
          </select>
        </label>
        <button className="button-secondary justify-self-start" disabled={busy}>
          {busy ? 'Fetching snapshot…' : 'Fetch pinned snapshot'}
        </button>
        {error && <ErrorState message={error} />}
        {snapshot && (
          <div aria-live="polite">
            <Notice>
              Pinned {snapshot.file_count} text files at{' '}
              <span className="break-all">{snapshot.commit_sha}</span>.
              {snapshot.limits_reached.length > 0 &&
                ` Partial inventory: ${snapshot.limits_reached.join(', ')}.`}{' '}
              Browse and index support only.
            </Notice>
          </div>
        )}
      </form>
    </details>
  )
}
