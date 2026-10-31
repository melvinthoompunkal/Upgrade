import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  workflowEnabled,
  shortSha,
  type Assessment,
  type Catalog,
  type ProjectSelection,
} from '../../api/client'
import { Notice } from '../../components/ui'
import { useWorkspace } from '../../app/workspace-context'
import { ProjectPin } from '../new-run/ProjectPin'
import { assessmentFinished } from './useAssessment'

export function AssessmentPanel({
  catalog,
  assessment,
  selected,
  onCreated,
}: {
  catalog: Catalog
  assessment: Assessment | null
  selected: string
  onCreated: (assessment: Assessment) => void
}) {
  const { capabilities } = useWorkspace()
  const [goal, setGoal] = useState('')
  const [target, setTarget] = useState<ProjectSelection | null>(null)
  const [includeTarget, setIncludeTarget] = useState(false)
  const [useSavedTarget, setUseSavedTarget] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const submission = useRef({ body: '', key: '' })
  useEffect(() => {
    setGoal(assessment?.goal ?? '')
    setIncludeTarget(Boolean(assessment?.target_project))
    setUseSavedTarget(true)
    setTarget(null)
  }, [assessment?.id])
  const targetId = includeTarget ? (useSavedTarget ? assessment?.target_project : target?.id) : null
  const sameContext = Boolean(
    assessment && goal === assessment.goal && (targetId ?? null) === assessment.target_project,
  )
  const ready =
    workflowEnabled(capabilities, 'relevance_assessment') &&
    capabilities?.worker_available &&
    capabilities?.model_configured &&
    goal.trim() &&
    (!includeTarget || targetId) &&
    (!includeTarget || useSavedTarget || target?.support.capability === 'evidence_analysis')
  async function submit(more: boolean, featureId?: string) {
    if (!ready || pending.current) return
    const body = JSON.stringify({
      catalog_id: catalog.id,
      catalog_revision: catalog.revision,
      target_project: targetId ?? null,
      goal,
      base_assessment: more && sameContext ? assessment?.id : null,
      feature_ids: featureId ? [featureId] : [],
      options: { feature_limit: featureId ? 1 : 8 },
    })
    if (submission.current.body !== body) submission.current = { body, key: crypto.randomUUID() }
    pending.current = true
    setBusy(true)
    setError('')
    try {
      const result = await api<Assessment>('/relevance-assessments', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': submission.current.key },
      })
      onCreated(result)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      pending.current = false
      setBusy(false)
    }
  }
  const running = assessment && !assessmentFinished(assessment)
  return (
    <section className="surface p-5 mb-5 space-y-4" aria-label="Relevance context">
      <h2>Compare with your project</h2>
      {assessment ? (
        <>
          <p className="text-sm break-words">
            <strong>Saved goal:</strong> {assessment.goal}
          </p>
          <p className="field-help">
            {assessment.target_project
              ? `${assessment.target_origin} @ ${shortSha(assessment.target_sha)} / ${assessment.target_root || '/'}`
              : 'Goal-only assessment; no target or fit eligibility.'}
          </p>
          <p role="status">
            {assessment.counts.deeply_assessed} assessed; {assessment.counts.relevant} relevant;{' '}
            {assessment.counts.unassessed} not yet assessed; {assessment.counts.assessment_failed}{' '}
            failed.
          </p>
          {running && (
            <p role="status">Assessment is {assessment.state}. You can return to this saved URL.</p>
          )}
          {assessment.failure_code && (
            <Notice tone="error">
              Assessment stopped: {assessment.failure_code}. Start a new assessment to retry.
            </Notice>
          )}
          {!!assessment.limit_reasons.length && (
            <Notice>
              {assessment.limit_reasons.join(', ').replaceAll('_', ' ')}. Unassessed entries have no
              relevance judgment.
            </Notice>
          )}
          <details>
            <summary>Assessment policy, shortlist and usage</summary>
            <div className="space-y-3 mt-3 field-help">
              <p>{assessment.qualification}</p>
              <p>
                {assessment.shortlist.length} features shortlisted. This job:{' '}
                {assessment.usage.requests ?? 0} attempts. Lineage:{' '}
                {assessment.cumulative_usage.requests ?? 0} attempts.
              </p>
              <p>Shortlist: {assessment.shortlist.join(', ')}</p>
              <Link className="text-action" to={`/runs/${assessment.run_id}/exports`}>
                Assessment receipts and full notices
              </Link>
            </div>
          </details>
          {assessment.base_assessment && (
            <Link
              className="text-action"
              to={`?revision=${catalog.revision}&assessment=${assessment.base_assessment}`}
            >
              Open previous assessment context
            </Link>
          )}
        </>
      ) : (
        <p className="text-muted">
          Browse the source features below. Optionally add your project and a goal to see what it
          already does and which source capabilities could usefully extend it. A goal alone gives a
          relevance assessment.
        </p>
      )}
      <details key={assessment?.id ?? 'new'} open={!assessment}>
        <summary>
          {assessment ? 'Assess more or change context' : 'Request relevance analysis'}
        </summary>
        <div className="space-y-4 mt-4">
          <div className="field-group">
            <label htmlFor="relevance-goal">Your goal</label>
            <textarea
              id="relevance-goal"
              value={goal}
              maxLength={2000}
              onChange={(e) => setGoal(e.target.value)}
            />
          </div>
          <label className="flex gap-2 items-center">
            <input
              className="project-check"
              type="checkbox"
              checked={includeTarget}
              onChange={(e) => {
                setIncludeTarget(e.target.checked)
                if (!assessment?.target_project) setUseSavedTarget(false)
              }}
            />
            Include a target project
          </label>
          {includeTarget && (
            <>
              {assessment?.target_project && (
                <label className="flex gap-2 items-center">
                  <input
                    type="checkbox"
                    className="project-check"
                    checked={useSavedTarget}
                    onChange={(e) => setUseSavedTarget(e.target.checked)}
                  />
                  Use the saved target snapshot
                </label>
              )}
              {!useSavedTarget && (
                <ProjectPin
                  role="target"
                  onPin={setTarget}
                  initialProfile={
                    catalog.analysis_profile === 'repository-text-v1'
                      ? 'repository-text-v1'
                      : 'python-evidence-v1'
                  }
                />
              )}
            </>
          )}
          {assessment && !sameContext && (
            <Notice>
              Edited context. The results below still belong to the saved goal and target above.
              Assess this new context to replace the view.
            </Notice>
          )}
          <p className="field-help">
            Explicit additional allowance: up to 8 features, 32 model attempts, 400,000 input /
            40,000 output tokens and 20 minutes. Public source and target excerpts are sent to Meta.
            Comparison needs no Docker image or passing test suite. No patch is applied.
          </p>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="flex flex-wrap gap-3">
            <button
              className="button-primary"
              disabled={!ready || busy || Boolean(running)}
              onClick={() => submit(false)}
            >
              {busy
                ? 'Starting assessment…'
                : includeTarget
                  ? 'Find expansion ideas'
                  : 'Assess relevance'}
            </button>
            {sameContext && (assessment?.counts.unassessed ?? 0) > 0 && (
              <button
                className="button-secondary"
                disabled={!ready || busy || Boolean(running)}
                onClick={() => submit(true)}
              >
                Assess more
              </button>
            )}
            {selected && (
              <button
                className="button-secondary"
                disabled={!ready || busy || Boolean(running)}
                onClick={() => submit(Boolean(sameContext), selected)}
              >
                Assess this feature
              </button>
            )}
          </div>
        </div>
      </details>
    </section>
  )
}
