import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, GitBranch, GithubLogo } from '@phosphor-icons/react'
import { api, createRun, shortSha, type ProjectSelection } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Notice } from '../../components/ui'
import { CatalogStart } from '../catalog/CatalogStart'

function ProjectPin({
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

export function ProjectRun() {
  const { capabilities, loading, refresh } = useWorkspace()
  const navigate = useNavigate()
  const [source, setSource] = useState<ProjectSelection | null>(null)
  const [target, setTarget] = useState<ProjectSelection | null>(null)
  const [includeTarget, setIncludeTarget] = useState(false)
  const [baseline, setBaseline] = useState(false)
  const [goal, setGoal] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const pending = useRef(false)
  const submission = useRef({ body: '', key: '' })
  const supported = (project: ProjectSelection | null) =>
    project?.support.capability === 'evidence_analysis'
  const ready =
    supported(source) && (!includeTarget || supported(target)) && capabilities?.worker_available

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!source || !ready || pending.current) return
    const request = {
      kind: 'project_analysis' as const,
      source_project: source.id,
      target_project: includeTarget ? target!.id : null,
      goal: goal.trim(),
      run_baseline: includeTarget && baseline && !!target?.support.execution_supported,
    }
    const body = JSON.stringify(request)
    if (submission.current.body !== body) submission.current = { body, key: crypto.randomUUID() }
    pending.current = true
    setSubmitting(true)
    setError('')
    try {
      const run = await createRun(request, submission.current.key)
      refresh()
      navigate(`/runs/${run.id}/overview`)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      pending.current = false
      setSubmitting(false)
    }
  }
  return (
    <div className="page new-run-page">
      <div className="page-intro">
        <h1>Start with your project.</h1>
        <p>
          Inspect pinned Python code, follow its definitions and tests, and see what this workspace
          supports.
        </p>
        <Link className="text-action" to="/example">
          Try the crawler example <ArrowRight size={16} />
        </Link>
      </div>
      <form className="surface max-w-4xl p-5 md:p-8" onSubmit={submit}>
        <h2>Analyze public projects</h2>
        <p className="mb-5 mt-2 text-sm text-muted">
          Inspect a source on its own, or add your target. Structural analysis uses no model calls.
        </p>
        <ProjectPin role="source" onPin={setSource} />
        <label className="flex items-center gap-3 py-3">
          <input
            type="checkbox"
            className="project-check"
            checked={includeTarget}
            onChange={(event) => {
              setIncludeTarget(event.target.checked)
              setTarget(null)
              setBaseline(false)
            }}
          />{' '}
          Add a target project
        </label>
        {includeTarget && (
          <ProjectPin
            role="target"
            onPin={(project) => {
              setTarget(project)
              setBaseline(false)
            }}
          />
        )}
        {includeTarget && target && (
          <label className="flex items-start gap-3 py-3">
            <input
              type="checkbox"
              className="project-check"
              checked={baseline}
              disabled={!target.support.execution_supported}
              onChange={(event) => setBaseline(event.target.checked)}
            />
            <span>
              Run the target’s original tests offline
              <br />
              <small className="text-muted">
                Requires the prepared Docker image. Skips and collection errors cannot pass.
              </small>
            </span>
          </label>
        )}
        <div className="field-group my-5">
          <label htmlFor="project-goal">Goal or run title (optional)</label>
          <textarea
            id="project-goal"
            value={goal}
            maxLength={2000}
            rows={2}
            onChange={(event) => setGoal(event.target.value)}
          />
          <p className="field-help">
            Saved with the report. Goal-based relevance matching is a later stage.
          </p>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="mt-6 flex flex-wrap items-center gap-4">
          <button className="button-primary" disabled={!ready || submitting}>
            {submitting ? 'Starting analysis…' : 'Analyze projects'}
            <ArrowRight size={18} />
          </button>
          <p className="field-help">
            Public snapshots only. Your original repositories stay intact.
          </p>
        </div>
        {!loading && !capabilities?.worker_available && (
          <p className="field-help mt-3">Start the backend worker to analyze projects.</p>
        )}
        <CatalogStart source={source} />
      </form>
    </div>
  )
}
