import { useRef, useState } from 'react'
import { GitBranch, GithubLogo } from '@phosphor-icons/react'
import { api, shortSha, type ProjectSelection } from '../../api/client'
import { Notice } from '../../components/ui'

export function ProjectPin({
  role,
  onPin,
}: {
  role: 'source' | 'target'
  onPin: (project: ProjectSelection | null) => void
}) {
  const [url, setUrl] = useState('')
  const [sha, setSha] = useState('')
  const [root, setRoot] = useState('')
  const [project, setProject] = useState<ProjectSelection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)

  function edit(update: () => void) {
    generation.current++
    update()
    setProject(null)
    onPin(null)
    setError('')
  }
  async function pin() {
    if (
      !/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(url.trim()) ||
      !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(sha.trim())
    ) {
      setError('Enter a public GitHub URL and its full lowercase commit SHA.')
      return
    }
    const current = ++generation.current
    setBusy(true)
    setError('')
    try {
      const result = await api<ProjectSelection>('/projects', {
        method: 'POST',
        body: JSON.stringify({
          url: url.trim(),
          commit_sha: sha.trim(),
          project_root: root.trim(),
          role,
        }),
      })
      if (generation.current !== current) return
      setProject(result)
      onPin(result)
    } catch (reason) {
      if (generation.current === current) setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="border-t border-line py-6" aria-label={`${role} project`}>
      <h3 className="mb-4 capitalize">{role} project</h3>
      <div className="field-group">
        <label htmlFor={`${role}-url`}>
          {role === 'source' ? 'Source' : 'Target'} public repository
        </label>
        <div className="input-with-icon">
          <GithubLogo size={20} />
          <input
            id={`${role}-url`}
            type="url"
            value={url}
            placeholder="https://github.com/owner/repository"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) =>
              edit(() => {
                setUrl(event.target.value)
                setSha('')
              })
            }
          />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="field-group">
          <label htmlFor={`${role}-sha`}>
            {role === 'source' ? 'Source' : 'Target'} commit SHA
          </label>
          <input
            id={`${role}-sha`}
            value={sha}
            spellCheck={false}
            placeholder="Full immutable commit SHA"
            className="font-mono text-xs"
            onChange={(event) => edit(() => setSha(event.target.value))}
          />
        </div>
        <div className="field-group">
          <label htmlFor={`${role}-root`}>
            {role === 'source' ? 'Source' : 'Target'} project root
          </label>
          <input
            id={`${role}-root`}
            value={root}
            placeholder="Repository root (leave empty)"
            aria-describedby={`${role}-root-help`}
            spellCheck={false}
            onChange={(event) => edit(() => setRoot(event.target.value))}
          />
          <p className="field-help" id={`${role}-root-help`}>
            For a monorepo, enter the directory containing pyproject.toml.
          </p>
        </div>
      </div>
      <button type="button" className="button-secondary mt-4" disabled={busy} onClick={pin}>
        {busy ? 'Inspecting public snapshot…' : `Inspect ${role} project`}
      </button>
      {busy && (
        <div
          role="status"
          className="skeleton mt-4 h-12 w-full"
          aria-label={`Inspecting ${role} project`}
        />
      )}
      {error && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {project && (
        <div className="mt-5 space-y-3" role="status">
          <p className="flex flex-wrap items-center gap-2">
            <GitBranch size={16} />
            <strong>{project.support.name ?? 'Project inspected'}</strong>
            <span className="font-mono text-xs">{shortSha(project.snapshot.commit_sha)}</span>
            <span className="badge">
              {project.support.capability === 'browse_index' ? 'Browse only' : 'Evidence analysis'}
            </span>
          </p>
          <p className="field-help">
            {project.support.python_files} Python files. Project root:{' '}
            {project.requested.project_root || '/'}.{' '}
            {project.support.execution_supported
              ? 'Native offline tests supported.'
              : 'Native tests unsupported.'}
          </p>
          {(project.support.reasons ?? []).length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {(project.support.reasons ?? []).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {project.root_choices.some((choice) => choice !== project.requested.project_root) && (
            <div className="field-group">
              <label htmlFor={`${role}-roots`}>Inspected Python project roots</label>
              <select
                id={`${role}-roots`}
                value=""
                onChange={(event) => {
                  if (event.target.value)
                    edit(() => setRoot(event.target.value === '/' ? '' : event.target.value))
                }}
              >
                <option value="">Choose a root, then inspect again</option>
                {project.root_choices.map((choice) => (
                  <option key={choice} value={choice || '/'}>
                    {choice || '/'}
                  </option>
                ))}
              </select>
            </div>
          )}
          <p className="field-help">Adaptation unavailable: {project.support.adaptation_reason}</p>
        </div>
      )}
    </section>
  )
}
