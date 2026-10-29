import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ArrowUpRight,
  CheckCircle,
  FileCode,
  GitBranch,
  MagnifyingGlass,
  ShieldCheck,
  WarningCircle,
  DownloadSimple,
} from '@phosphor-icons/react'
import { artifactUrl, shortSha, type Run } from '../../api/client'
import { SectionHeading } from '../../components/ui'
import { Citation } from '../evidence/Inspector'
import { ProjectOverview } from './ProjectOverview'

export function Overview({ run }: { run: Run }) {
  if (run.kind === 'project_analysis') return <ProjectOverview run={run} />
  const features = run.features ?? []
  const feature =
    features.find((item) => item.feature_id === run.fit?.selected_feature_id) ?? features[0]
  const candidate = run.fit?.candidates.find((item) => item.feature_id === feature?.feature_id)
  const assessment = run.assessment?.assessments.find(
    (item) => item.feature_id === feature?.feature_id,
  )
  const selected = run.outcome === 'selected'
  const sourceRepo = run.source?.origin.split('/').slice(-2).join('/')
  const totalTests = (run.tests ?? []).reduce((sum, suite) => sum + suite.cases.length, 0)
  const report =
    run.artifacts?.find((item) => item.label === 'report.md' && item.available) ??
    run.artifacts?.find((item) => item.label === 'report.json' && item.available)
  const title =
    run.status !== 'finished'
      ? 'Your analysis is underway.'
      : selected
        ? 'A useful idea. A supported fit.'
        : run.outcome === 'no_safe_fit'
          ? 'A clear result, even without a fit.'
          : run.outcome === 'passed'
            ? 'The check is complete.'
            : 'Here’s where the run stopped.'
  return (
    <div className="overview-grid">
      <div className="overview-main">
        <section className={`outcome-banner ${selected ? 'outcome-selected' : ''}`}>
          <span className="outcome-icon">
            {selected || run.outcome === 'passed' ? (
              <CheckCircle size={30} />
            ) : run.status !== 'finished' ? (
              <MagnifyingGlass size={30} />
            ) : (
              <WarningCircle size={30} />
            )}
          </span>
          <div>
            <h2>{title}</h2>
            <p>
              {run.fit?.selection_reason ??
                (run.status !== 'finished'
                  ? 'The worker is collecting evidence. Progress and completed results will appear here.'
                  : (run.error?.message ??
                    'Inspect the evidence, captured tests, and published artifacts below.'))}
            </p>
          </div>
          {selected && candidate && (
            <div className="fit-score">
              <strong>{candidate.total}</strong>
              <span>/ 100 fit score</span>
            </div>
          )}
        </section>
        {feature && (
          <>
            <SectionHeading title={selected ? 'The selected behavior' : 'The behavior considered'}>
              <span>{feature.name}</span>
            </SectionHeading>
            <div className="comparison surface">
              <section>
                <div className="comparison-label">
                  <FileCode size={18} />
                  Source behavior<span>{sourceRepo ?? 'Pinned source'}</span>
                </div>
                <h3>{feature.name}</h3>
                <p>{feature.behavior}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {feature.entry_points.slice(0, 2).map((ref, index) => (
                    <Citation
                      key={index}
                      run={run}
                      reference={ref}
                      label="Inspect implementation"
                    />
                  ))}
                </div>
              </section>
              <div className="comparison-bridge">
                <ArrowRight size={18} />
              </div>
              <section>
                <div className="comparison-label">
                  <GitBranch size={18} />
                  Target behavior<span>{run.target?.repo_id ?? 'Pinned target'}</span>
                </div>
                <h3>
                  {run.profile?.gaps[0] ? 'An observed gap to consider' : 'What exists today'}
                </h3>
                <p>
                  {run.profile?.gaps[0]?.description ??
                    run.profile?.capabilities[0]?.behavior ??
                    'A target profile was not published for this run.'}
                </p>
                {run.profile?.gaps[0] && (
                  <span className="basis-label">
                    {run.profile.gaps[0].basis === 'observed_behavior'
                      ? 'Observed in captured tests'
                      : 'Inferred from static evidence'}
                  </span>
                )}
                <div className="mt-4 flex flex-wrap gap-2">
                  {run.profile?.integration_points.slice(0, 1).map((ref, index) => (
                    <Citation
                      key={index}
                      run={run}
                      reference={ref}
                      label="Inspect target boundary"
                    />
                  ))}
                </div>
              </section>
            </div>
            {assessment && (
              <section className="proposed-change">
                <span className="proposal-icon">
                  <ArrowUpRight size={20} />
                </span>
                <div>
                  <h3>Proposed adaptation</h3>
                  <p>{assessment.adaptation.rationale}</p>
                  <small>
                    Proposal only. No implementation or post-implementation verification was
                    performed.
                  </small>
                </div>
              </section>
            )}
            <Link className="button-primary mt-6" to={`/runs/${run.id}/features`}>
              Explore feature and fit
              <ArrowRight size={17} />
            </Link>
          </>
        )}
        {!feature && (
          <div className="surface p-7 mt-5">
            <h3>
              {run.kind === 'meta_smoke'
                ? 'Structured output and tool connectivity'
                : run.status !== 'finished'
                  ? 'Evidence will appear as analysis completes'
                  : 'Inspect the recorded check results'}
            </h3>
            <p className="text-muted leading-relaxed mt-2">
              {run.analyzed_feature_count === 0
                ? 'This run stopped before analyzing a source feature. No candidate or fit score is available.'
                : run.kind === 'meta_smoke'
                  ? 'The connectivity check exercises a synthetic plan and a harmless read-only tool. It does not analyze a repository.'
                  : 'Test suites, logs, and output artifacts are available in the run views.'}
            </p>
            <div className="flex flex-wrap gap-3 mt-5">
              <Link className="button-secondary" to={`/runs/${run.id}/tests`}>
                Inspect tests
                <ArrowRight size={15} />
              </Link>
              <Link className="text-action" to={`/runs/${run.id}/exports`}>
                Browse exports
                <ArrowRight size={15} />
              </Link>
            </div>
          </div>
        )}
        {run.fit && run.fit.rejected.length > 0 && (
          <section className="rejection-summary">
            <h3>Why a candidate was rejected</h3>
            {run.fit.rejected.map((item) => (
              <div key={item.feature_id}>
                <p className="font-medium">
                  {features.find((feature) => feature.feature_id === item.feature_id)?.name ??
                    item.feature_id}
                </p>
                <ul>
                  {item.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        )}
      </div>
      <aside className="run-facts">
        <div className="surface">
          <h3>Run at a glance</h3>
          <dl className="fact-list">
            <div>
              <dt>Analyzed features</dt>
              <dd>
                {run.analyzed_feature_count ?? (features.length ? features.length : 'Not recorded')}
              </dd>
            </div>
            <div>
              <dt>Eligible features</dt>
              <dd>{run.eligible_feature_count ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Captured test cases</dt>
              <dd>{totalTests || 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Published artifacts</dt>
              <dd>{run.artifacts?.filter((item) => item.available).length ?? 0}</dd>
            </div>
          </dl>
          <div className="fact-assurance">
            <ShieldCheck size={19} />
            <span>
              {run.original_target_unchanged === true
                ? 'Original target preserved'
                : run.original_target_unchanged === false
                  ? 'Preservation check failed'
                  : 'Preservation not recorded'}
            </span>
          </div>
        </div>
        <div className="pinned-facts">
          <h3>Pinned inputs</h3>
          <div>
            <span>Source</span>
            <strong>{sourceRepo ?? 'Not recorded'}</strong>
            <code>{shortSha(run.source?.commit_sha)}</code>
          </div>
          <div>
            <span>Target</span>
            <strong>
              {run.target?.repo_id ??
                (run.kind === 'local_baseline' ? 'Working fixture files' : 'Not recorded')}
            </strong>
            <code>{shortSha(run.target?.commit_sha)}</code>
          </div>
          <Link className="text-action" to={`/runs/${run.id}/evidence`}>
            Inspect provenance
            <ArrowUpRight size={14} />
          </Link>
        </div>
        {report && (
          <a className="report-download" href={artifactUrl(run.id, report.id)} download>
            <DownloadSimple size={22} />
            <div>
              <strong>Take the evidence with you</strong>
              <span>Download the analysis report</span>
            </div>
            <ArrowUpRight size={16} />
          </a>
        )}
        <details className="usage-details">
          <summary>Model usage & run details</summary>
          <dl className="metadata-list mt-4">
            <div>
              <dt>Requests</dt>
              <dd>{run.usage?.requests ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Input tokens</dt>
              <dd>{run.usage?.input_tokens?.toLocaleString() ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Output tokens</dt>
              <dd>{run.usage?.output_tokens?.toLocaleString() ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Request allowance</dt>
              <dd>{run.limits?.requests ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Run ID</dt>
              <dd className="font-mono break-all">{run.id}</dd>
            </div>
          </dl>
          {(run.calls ?? []).map((call, index) => (
            <div className="model-call" key={index}>
              <strong>{call.stage}</strong>
              <span>{call.model_id}</span>
              <code>{call.response_id}</code>
            </div>
          ))}
        </details>
      </aside>
    </div>
  )
}
