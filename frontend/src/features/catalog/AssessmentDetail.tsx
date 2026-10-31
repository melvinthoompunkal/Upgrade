import { useEffect, useState } from 'react'
import { api, type FeatureAssessment } from '../../api/client'
import { ErrorState, Skeleton } from '../../components/ui'
import { StartAdaptation } from './StartAdaptation'
import { ExpansionDetail } from './ExpansionDetail'

export function AssessmentDetail({
  assessmentId,
  featureId,
}: {
  assessmentId: string
  featureId: string
}) {
  const [value, setValue] = useState<FeatureAssessment | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setValue(null)
    setError('')
    api<FeatureAssessment>(`/relevance-assessments/${assessmentId}/features/${featureId}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setValue(result)
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message)
      })
    return () => controller.abort()
  }, [assessmentId, featureId])
  if (error) return <ErrorState message={error} />
  if (!value) return <Skeleton rows={3} />
  return (
    <div className="space-y-3 border-t border-line pt-4">
      <h3>Saved comparison</h3>
      <p>
        {value.relevance_status.replaceAll('_', ' ')} · {value.target_status.replaceAll('_', ' ')}
      </p>
      <p>
        {value.reason ||
          'This feature has not been assessed. Use Assess this feature to request analysis.'}
      </p>
      <ExpansionDetail value={value} />
      {!value.expansion && value.target_status === 'already_present' && (
        <p className="field-help">The inspected target already provides this behavior.</p>
      )}
      {value.failure_code && (
        <p role="alert">
          Assessment failed: {value.failure_code}. Request an explicit reassessment.
        </p>
      )}
      {value.evidence_limit_reached && (
        <p className="field-help">
          The saved evidence preview reached its bound. Additional reads remain in the job exports.
        </p>
      )}
      {value.evidence.map((item, index) => (
        <details key={index}>
          <summary>
            {item.ref.repo_id}: {item.ref.path}:{item.ref.start_line}–{item.ref.end_line}
          </summary>
          <pre className="code-block mt-2">{item.text}</pre>
        </details>
      ))}
      {value.relevance_status === 'relevant' && (
        <details>
          <summary>Implementation and verification</summary>
          <div className="mt-3 space-y-3">
            <p className="field-help">
              Semantic usefulness is separate from readiness to apply a tested patch. Fit:{' '}
              {value.fit_status}.
            </p>
            {value.adaptation_reason && <p className="field-help">{value.adaptation_reason}</p>}
            {value.fit && (
              <p>
                Fit score: {value.fit.total}/100. A rating is a judgment, not a probability of
                success.
              </p>
            )}
            <StartAdaptation
              key={`${assessmentId}:${featureId}`}
              assessmentId={assessmentId}
              feature={value}
            />
          </div>
        </details>
      )}
      {!!value.gates.length && (
        <details>
          <summary>Fit gates and rejection reasons</summary>
          <ul className="mt-3 space-y-3">
            {value.gates.map((gate) => (
              <li key={gate.name}>
                <strong>
                  {gate.name.replaceAll('_', ' ')}: {gate.status}
                </strong>
                <p className="field-help">{gate.reason}</p>
              </li>
            ))}
          </ul>
          <ul className="mt-3 list-disc pl-5 text-sm">
            {value.rejections.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
