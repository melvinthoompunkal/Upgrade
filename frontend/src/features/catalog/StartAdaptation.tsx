import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createRun, type FeatureAssessment } from '../../api/client'

export function StartAdaptation({
  assessmentId,
  feature,
}: {
  assessmentId: string
  feature: FeatureAssessment
}) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [key] = useState(() => crypto.randomUUID())
  if (
    feature.relevance_status !== 'relevant' ||
    !['potential_gap', 'already_present'].includes(feature.target_status) ||
    !feature.fit ||
    feature.fit.total < 75
  )
    return null
  async function start() {
    setBusy(true)
    setError('')
    try {
      const run = await createRun(
        {
          kind: 'verified_adaptation',
          assessment_id: assessmentId,
          feature_id: feature.feature_id,
        },
        key,
      )
      navigate(`/runs/${run.id}/overview`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to start adaptation.')
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2">
      <button className="button-primary" disabled={busy} onClick={start}>
        {busy ? 'Starting adaptation…' : 'Verify adaptation'}
      </button>
      <p className="field-help">
        Prepare frozen tests, check the original baseline, and verify a bounded patch. Supports
        synchronous Python functions and standard MIT source notices.
      </p>
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
