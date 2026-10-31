import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight } from '@phosphor-icons/react'
import { createRun, workflowEnabled, type ProjectSelection } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Notice } from '../../components/ui'
import { CatalogStart } from '../catalog/CatalogStart'
import { ProjectPin } from './ProjectPin'

export function ProjectRun() {
  const { capabilities, loading, refresh, session } = useWorkspace()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const structureOnly = params.get('view') === 'structure'
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
    supported(source) &&
    (!includeTarget || supported(target)) &&
    capabilities?.worker_available &&
    workflowEnabled(capabilities, 'project_analysis')

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!structureOnly || !source || !ready || pending.current) return
    const request = {
      kind: 'project_analysis' as const,
      source_project: source.id,
      target_project: includeTarget ? target!.id : null,
      goal: goal.trim(),
      run_baseline:
        includeTarget &&
        baseline &&
        !!target?.support.execution_supported &&
        capabilities?.target_execution_supported !== false,
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
        <h1>Break a repository into features.</h1>
        <p>
          Discover what a public Python repository does, how its capabilities work, and where they
          live in the code. Add your own project later to explore useful extensions.
        </p>
        <Link className="text-action" to="/example">
          Try the crawler example <ArrowRight size={16} />
        </Link>
      </div>
      <form className="surface max-w-4xl p-5 md:p-8" onSubmit={submit}>
        <div className="flex flex-wrap gap-3 mb-6" role="group" aria-label="Analysis type">
          <button
            type="button"
            className={!structureOnly ? 'button-primary' : 'button-secondary'}
            aria-pressed={!structureOnly}
            onClick={() => setParams({})}
          >
            Feature discovery
          </button>
          <button
            type="button"
            className={structureOnly ? 'button-primary' : 'button-secondary'}
            aria-pressed={structureOnly}
            onClick={() => setParams({ view: 'structure' })}
          >
            Structure only
          </button>
        </div>
        <h2>{structureOnly ? 'Inspect project structure' : 'Choose a source repository'}</h2>
        <p className="mb-5 mt-2 text-sm text-muted">
          {structureOnly
            ? 'Inspect definitions, imports and test locations without model calls. This does not discover semantic features.'
            : 'Pin a public repository snapshot, then discover its code-backed capabilities. No target project is required.'}
        </p>
        {session?.mode === 'local' && (
          <p className="field-help mb-4">Local workspace. No GitHub App or sign-in needed.</p>
        )}
        <ProjectPin role="source" onPin={setSource} />
        {!structureOnly ? (
          <CatalogStart source={source} />
        ) : (
          <>
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
                  disabled={
                    !target.support.execution_supported ||
                    capabilities?.target_execution_supported === false
                  }
                  onChange={(event) => setBaseline(event.target.checked)}
                />
                <span>
                  Run the target’s original tests offline
                  <br />
                  <small className="text-muted">
                    {capabilities?.target_execution_supported === false
                      ? 'Target tests are unavailable on this deployment.'
                      : 'Requires the prepared Docker image. Skips and collection errors cannot pass.'}
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
                Saved with the structure report. Use feature discovery for semantic breakdown and
                optional target comparison.
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
          </>
        )}
      </form>
    </div>
  )
}
