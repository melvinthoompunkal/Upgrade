import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  CaretDown,
  MagnifyingGlass,
  CheckCircle,
  WarningCircle,
  ArrowRight,
} from '@phosphor-icons/react'
import type { Run } from '../../api/client'
import { Empty, Notice, SectionHeading, StatusMark } from '../../components/ui'
import { Citation } from './Inspector'

const criteriaLabels: Record<string, string> = {
  gap_fit: 'Target gap fit',
  isolation: 'Isolation',
  dependency_cost: 'Dependency cost',
  stack_compatibility: 'Stack compatibility',
  testability: 'Testability',
}

export function Features({ run }: { run: Run }) {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [decision, setDecision] = useState('all')
  const features = run.features ?? []
  const selectedId =
    params.get('feature') ?? run.fit?.selected_feature_id ?? features[0]?.feature_id
  const feature = features.find((item) => item.feature_id === selectedId) ?? features[0]
  const candidate = run.fit?.candidates.find((item) => item.feature_id === feature?.feature_id)
  const assessment = run.assessment?.assessments.find(
    (item) => item.feature_id === feature?.feature_id,
  )
  const visible = features.filter((item) => {
    const score = run.fit?.candidates.find((candidate) => candidate.feature_id === item.feature_id)
    return (
      item.name.toLowerCase().includes(query.toLowerCase()) &&
      (decision === 'all' || (decision === 'eligible' ? score?.eligible : score && !score.eligible))
    )
  })
  if (!features.length)
    return (
      <Empty
        title={
          run.analyzed_feature_count === 0
            ? 'No source features were analyzed'
            : 'No feature cards published'
        }
      >
        {run.status === 'finished'
          ? 'This run has no validated source feature cards. Check its outcome and partial exports for details.'
          : 'Source cards will appear after tracing completes.'}
      </Empty>
    )
  return (
    <>
      <SectionHeading title="Every candidate. Every reason.">
        {run.analyzed_feature_count ?? features.length} analyzed /{' '}
        {run.eligible_feature_count ?? 'Not recorded'} eligible. Only actual candidates are shown.
      </SectionHeading>
      <div className="surface overflow-hidden mb-7">
        <div className="table-toolbar">
          <div className="search-input">
            <MagnifyingGlass size={17} />
            <input
              aria-label="Search features"
              placeholder="Search features…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="filter-select">
            <select
              aria-label="Filter feature decisions"
              value={decision}
              onChange={(event) => setDecision(event.target.value)}
            >
              <option value="all">All decisions</option>
              <option value="eligible">Eligible</option>
              <option value="rejected">Not eligible</option>
            </select>
            <CaretDown size={14} />
          </div>
        </div>
        <div className="table-scroll">
          <table className="data-table feature-table">
            <thead>
              <tr>
                <th>Feature</th>
                <th>Decision</th>
                <th>Fit score</th>
                <th>Policy gates</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => {
                const score = run.fit?.candidates.find(
                  (candidate) => candidate.feature_id === item.feature_id,
                )
                const selected = run.fit?.selected_feature_id === item.feature_id
                return (
                  <tr
                    key={item.feature_id}
                    className={item.feature_id === feature?.feature_id ? 'selected-row' : ''}
                  >
                    <td>
                      <button
                        className="feature-selector"
                        aria-pressed={item.feature_id === feature?.feature_id}
                        onClick={() => {
                          const next = new URLSearchParams(params)
                          next.set('feature', item.feature_id)
                          setParams(next)
                        }}
                      >
                        {item.name}
                        <ArrowRight size={15} />
                      </button>
                    </td>
                    <td>
                      <span className={`badge ${selected ? 'badge-positive' : ''}`}>
                        {selected ? <CheckCircle size={14} /> : <WarningCircle size={14} />}
                        {selected
                          ? 'Selected'
                          : score
                            ? score.eligible
                              ? 'Eligible'
                              : 'Not eligible'
                            : 'Traced only'}
                      </span>
                    </td>
                    <td className="font-mono">{score ? `${score.total} / 100` : 'Not assessed'}</td>
                    <td>
                      {score
                        ? `${score.gates.filter((gate) => gate.passed).length} / ${score.gates.length} passed`
                        : 'Not assessed'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {!visible.length && (
          <Empty title="No matching candidates">
            Change the search or decision filter. The analyzed total is unchanged.
          </Empty>
        )}
      </div>
      {feature && (
        <div className="feature-detail-grid">
          <div>
            <div className="feature-title">
              <div>
                <h2>{feature.name}</h2>
                <span className="font-mono text-xs text-muted">{feature.feature_id}</span>
              </div>
              <span className="badge">{feature.evidence_status}</span>
            </div>
            <p className="body-copy mt-4">{feature.behavior}</p>
            {candidate && (
              <section className="mt-7">
                <h3 className="mb-4">How the fit was assessed</h3>
                <div className="criteria-list">
                  {Object.entries(candidate.criteria).map(([key, criterion]) => (
                    <div key={key}>
                      <div className="criterion-top">
                        <strong>{criteriaLabels[key]}</strong>
                        <span className="font-mono">{criterion.rating} / 5</span>
                      </div>
                      <meter
                        min={0}
                        max={5}
                        value={criterion.rating}
                        aria-label={`${criteriaLabels[key]} rating`}
                      />
                      <p>{criterion.reason}</p>
                      <div className="flex flex-wrap gap-2 mt-3">
                        {criterion.evidence.slice(0, 2).map((ref, index) => (
                          <Citation key={index} run={run} reference={ref} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="footnote">
                  Ratings are model assessments. Totals, eligibility, and ordering are computed by
                  the controller.
                </p>
              </section>
            )}
            <section className="scope-section">
              <h3>Transferable slice</h3>
              <p>{feature.transferable_slice}</p>
              <details>
                <summary>Included acceptance behavior</summary>
                <ul>
                  {feature.acceptance_behaviors.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </details>
              <details>
                <summary>Excluded behavior</summary>
                <ul>
                  {feature.excluded_behaviors.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </details>
            </section>
            {assessment && (
              <section className="scope-section">
                <h3>Proposed target scope</h3>
                <p>{assessment.adaptation.rationale}</p>
                <div className="scope-paths">
                  {assessment.adaptation.production_paths.map((path) => (
                    <code key={path}>{path}</code>
                  ))}
                </div>
                <p className="field-help">
                  Estimated changed lines: {assessment.adaptation.estimated_changed_lines}. This is
                  a proposal; actual patch scope has not been checked.
                </p>
              </section>
            )}
          </div>
          <aside>
            <div className="surface gates-panel">
              <h3>Policy gates</h3>
              <p>Every required gate must pass.</p>
              {candidate ? (
                <ul>
                  {candidate.gates.map((gate) => (
                    <li key={gate.name}>
                      <StatusMark passed={gate.passed} />
                      <div>
                        <strong>{gate.name.replaceAll('_', ' ')}</strong>
                        <p>{gate.reason}</p>
                      </div>
                      <span className="gate-status">{gate.passed ? 'Pass' : 'Fail'}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Notice>This trace-only run did not evaluate fit policy gates.</Notice>
              )}
            </div>
            <div className="dependency-panel">
              <h3>Source dependencies</h3>
              <p>Dependencies cited by the source trace. These are not additions to your target.</p>
              {feature.dependencies.map((dependency) => (
                <div key={dependency.name}>
                  <span className="font-mono text-sm">{dependency.name}</span>
                  <span className="badge">{dependency.kind}</span>
                </div>
              ))}
              <h3 className="mt-6">Source license</h3>
              <p>{feature.license_spdx}</p>
              {feature.license_refs.map((ref, index) => (
                <Citation key={index} run={run} reference={ref} label="Inspect source notice" />
              ))}
            </div>
          </aside>
        </div>
      )}
    </>
  )
}
