import type { FeatureAssessment } from '../../api/client'

export function ExpansionDetail({ value }: { value: FeatureAssessment }) {
  const idea = value.expansion
  if (!idea) return null
  return (
    <section className="space-y-4" aria-label="Expansion recommendation">
      <h3>Expansion idea</h3>
      <dl className="space-y-4 text-sm">
        <div>
          <dt className="font-semibold">Source capability</dt>
          <dd className="mt-1">{idea.source_capability}</dd>
        </div>
        <div>
          <dt className="font-semibold">What your project already does</dt>
          <dd className="mt-1">{idea.existing_target_behavior}</dd>
        </div>
        <div>
          <dt className="font-semibold">Suggested extension</dt>
          <dd className="mt-1">{idea.proposed_extension}</dd>
        </div>
        <div>
          <dt className="font-semibold">Why it helps</dt>
          <dd className="mt-1">{idea.benefit}</dd>
        </div>
      </dl>
      <div>
        <h4>Suggested integration points</h4>
        <ul className="space-y-3 mt-2 text-sm">
          {idea.integration_points.map((point, index) => (
            <li key={index}>
              <code className="break-all">
                {point.target_ref.path}:{point.target_ref.start_line}–{point.target_ref.end_line}
                {point.target_ref.symbol && ` (${point.target_ref.symbol})`}
              </code>
              <p className="mt-1">{point.change}</p>
            </li>
          ))}
        </ul>
      </div>
      {!!idea.constraints.length && (
        <div>
          <h4>Constraints</h4>
          <ul className="list-disc pl-5 mt-2 text-sm space-y-2">
            {idea.constraints.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <h4>Uncertainty</h4>
        <ul className="list-disc pl-5 mt-2 text-sm space-y-2">
          {idea.uncertainties.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      </div>
      <p className="field-help">
        Based on the saved code excerpts below. This idea has not been implemented or tested.
      </p>
    </section>
  )
}
