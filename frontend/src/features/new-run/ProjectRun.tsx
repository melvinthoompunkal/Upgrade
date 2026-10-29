import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight } from '@phosphor-icons/react'
import { createRun, type ProjectSelection } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Notice } from '../../components/ui'
import { CatalogStart } from '../catalog/CatalogStart'
import { ProjectPin } from './ProjectPin'

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
