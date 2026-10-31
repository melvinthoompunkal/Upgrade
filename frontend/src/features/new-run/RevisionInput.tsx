import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

export type RevisionSelection =
  | { kind: 'latest' }
  | { kind: 'commit'; sha: string }
  | { kind: 'branch' | 'tag'; name: string; sha: string }

export function RevisionInput({
  label,
  value,
  onChange,
  repository,
  disabled = false,
  allowLatest = false,
}: {
  label: string
  value: RevisionSelection
  onChange: (revision: RevisionSelection) => void
  repository: { url: string } | { repository_id: number }
  disabled?: boolean
  allowLatest?: boolean
}) {
  const mode = value.kind
  const name = value.kind === 'branch' || value.kind === 'tag' ? value.name : ''
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const repositoryKey = JSON.stringify(repository)
  useEffect(() => {
    generation.current++
    setBusy(false)
    setError('')
    return () => {
      generation.current++
    }
  }, [repositoryKey])
  function edit(revision: RevisionSelection) {
    generation.current++
    onChange(revision)
    setBusy(false)
    setError('')
  }
  async function resolve() {
    if (value.kind !== 'branch' && value.kind !== 'tag') return
    const current = ++generation.current
    setBusy(true)
    setError('')
    onChange({ ...value, sha: '' })
    try {
      const result = await api<{ commit_sha: string }>('/projects/resolve-ref', {
        method: 'POST',
        body: JSON.stringify({ ...repository, kind: mode, name: name.trim() }),
      })
      if (generation.current === current) onChange({ ...value, sha: result.commit_sha })
    } catch (reason) {
      if (generation.current === current) setError((reason as Error).message)
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }
  const id = label.toLowerCase().replaceAll(' ', '-')
  return (
    <div className="field-group gap-2">
      <label htmlFor={`${id}-kind`}>{label} revision type</label>
      <select
        id={`${id}-kind`}
        value={mode}
        disabled={disabled}
        onChange={(event) => {
          const kind = event.target.value
          if (kind === 'latest') edit({ kind })
          else if (kind === 'commit') edit({ kind, sha: '' })
          else if (kind === 'branch' || kind === 'tag') edit({ kind, name: '', sha: '' })
        }}
      >
        {allowLatest && <option value="latest">Latest snapshot (default branch)</option>}
        <option value="commit">Commit SHA</option>
        <option value="branch">Branch</option>
        <option value="tag">Tag</option>
      </select>
      {mode === 'latest' && (
        <p className="field-help">
          Uses the latest default-branch commit when you inspect the repository. That snapshot stays
          fixed for its breakdown and feature discovery. No SHA needed.
        </p>
      )}
      {(value.kind === 'branch' || value.kind === 'tag') && (
        <>
          <label htmlFor={`${id}-ref`}>
            {label} {mode}
          </label>
          <input
            id={`${id}-ref`}
            value={name}
            maxLength={200}
            disabled={disabled}
            spellCheck={false}
            onChange={(event) => edit({ ...value, name: event.target.value, sha: '' })}
          />
          <button
            type="button"
            className="button-secondary justify-self-start"
            disabled={disabled || busy || !name.trim()}
            onClick={() => void resolve()}
          >
            {busy ? 'Resolving revision…' : `Resolve ${mode}`}
          </button>
          <p className="field-help">
            Resolved once. Inspection and later jobs use only the pinned commit.
          </p>
        </>
      )}
      {value.kind !== 'latest' && (
        <>
          <label htmlFor={`${id}-sha`}>
            {label} commit SHA {allowLatest && mode === 'commit' && '(optional)'}
          </label>
          <input
            id={`${id}-sha`}
            value={value.sha}
            readOnly={mode !== 'commit'}
            disabled={disabled}
            spellCheck={false}
            placeholder={
              allowLatest && mode === 'commit'
                ? 'Leave blank for the latest snapshot'
                : 'Full immutable commit SHA'
            }
            className="font-mono text-xs"
            onChange={(event) => edit({ kind: 'commit', sha: event.target.value })}
          />
          {allowLatest && mode === 'commit' && (
            <p className="field-help">Leave blank to use the latest default-branch snapshot.</p>
          )}
        </>
      )}
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="field-help" role="status">
          Resolving {mode} to a commit…
        </p>
      )}
    </div>
  )
}
