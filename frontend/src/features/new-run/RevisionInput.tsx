import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

export function RevisionInput({
  label,
  value,
  onChange,
  repository,
  disabled = false,
}: {
  label: string
  value: string
  onChange: (sha: string) => void
  repository: { url: string } | { repository_id: number }
  disabled?: boolean
}) {
  const [mode, setMode] = useState('commit')
  const [name, setName] = useState('')
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
  function edit(change: () => void) {
    generation.current++
    change()
    onChange('')
    setBusy(false)
    setError('')
  }
  async function resolve() {
    const current = ++generation.current
    setBusy(true)
    setError('')
    onChange('')
    try {
      const result = await api<{ commit_sha: string }>('/projects/resolve-ref', {
        method: 'POST',
        body: JSON.stringify({ ...repository, kind: mode, name: name.trim() }),
      })
      if (generation.current === current) onChange(result.commit_sha)
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
        onChange={(event) => edit(() => setMode(event.target.value))}
      >
        <option value="commit">Commit SHA</option>
        <option value="branch">Branch</option>
        <option value="tag">Tag</option>
      </select>
      {mode !== 'commit' && (
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
            onChange={(event) => edit(() => setName(event.target.value))}
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
      <label htmlFor={`${id}-sha`}>{label} commit SHA</label>
      <input
        id={`${id}-sha`}
        value={value}
        readOnly={mode !== 'commit'}
        disabled={disabled}
        spellCheck={false}
        placeholder="Full immutable commit SHA"
        className="font-mono text-xs"
        onChange={(event) => onChange(event.target.value)}
      />
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
