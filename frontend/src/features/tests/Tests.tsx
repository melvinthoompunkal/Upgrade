import { useState } from 'react'
import { CheckCircle, WarningCircle, CaretDown, Flask, TerminalWindow } from '@phosphor-icons/react'
import type { Run } from '../../api/client'
import { Empty, Notice, SectionHeading } from '../../components/ui'
import { InspectArtifact } from '../evidence/Inspector'

const suiteNames: Record<string, string> = {
  isolation: 'Runner isolation',
  baseline: 'Ordinary baseline',
  acceptance: 'Retry behavior diagnostic',
  project_acceptance: 'Frozen feature acceptance',
  project_baseline: 'Native regression suite',
  project_unittest: 'Native unittest suite',
}

export function Tests({ run }: { run: Run }) {
  const [filter, setFilter] = useState('all')
  const tests = run.tests ?? []
  if (!tests.length)
    return (
      <Empty title="No captured tests published">
        {run.kind === 'source_trace' || run.kind === 'meta_smoke'
          ? 'This workflow does not execute target test suites. Source test citations are available under Evidence when tracing is performed.'
          : 'Captured cases will appear when target testing completes. Check the run outcome if execution stopped early.'}
      </Empty>
    )
  const diagnostic = tests.find((suite) => suite.suite === 'acceptance')
  return (
    <>
      <SectionHeading title="Observed behavior, captured in tests">
        {run.kind === 'verified_adaptation'
          ? 'Original baseline, frozen acceptance and each production verification round are separate results.'
          : 'Isolation, baseline, and the retry diagnostic are separate results.'}
      </SectionHeading>
      {diagnostic?.expected_diagnostic ? (
        <div className="mb-6">
          <Notice>
            <strong>Missing retry behavior was observed.</strong>
            <p>
              The retry diagnostic’s expected assertion failures demonstrate the gap. Passing
              ordinary baseline tests establish existing behavior before any adaptation.
            </p>
          </Notice>
        </div>
      ) : run.retry_diagnostic_status === 'already_present' ? (
        <div className="mb-6">
          <Notice>
            The target already satisfies this retry diagnostic. This behavior is not a missing gap.
          </Notice>
        </div>
      ) : null}
      <div className="flex items-center justify-between mb-5 gap-4">
        <span className="text-muted text-sm">
          {tests.reduce((count, suite) => count + suite.cases.length, 0)} captured cases /{' '}
          {tests.length} suites
        </span>
        <div className="filter-select">
          <select
            aria-label="Filter test results"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">All cases</option>
            <option value="failed">Failed assertions</option>
            <option value="passed">Passing cases</option>
            <option value="error">Errors</option>
            <option value="skipped">Skipped</option>
          </select>
          <CaretDown size={14} />
        </div>
      </div>
      <div className="test-suites">
        {tests.map((suite) => {
          const visible = suite.cases.filter((item) => filter === 'all' || item.outcome === filter)
          const passed = suite.cases.filter((item) => item.outcome === 'passed').length
          const logs =
            run.artifacts?.filter(
              (item) =>
                item.available &&
                item.label.startsWith(
                  `test-captures/${suite.phase ? `${suite.phase}/` : ''}${suite.suite}/`,
                ) &&
                /(?:stdout|stderr)/.test(item.label),
            ) ?? []
          return (
            <details
              open
              className="test-suite surface"
              key={`${suite.phase ?? ''}:${suite.suite}`}
            >
              <summary>
                <span className="suite-icon">
                  <Flask size={23} />
                </span>
                <div>
                  <h3>
                    {suite.phase && `${suite.phase}: `}
                    {suiteNames[suite.suite] ?? suite.suite}
                  </h3>
                  <p>
                    {suite.expected_diagnostic
                      ? 'Expected missing-behavior diagnostic'
                      : suite.status === 'passed'
                        ? 'All registered cases passed'
                        : 'Inspect failures before continuing'}
                  </p>
                </div>
                <span className={`badge ${suite.status === 'passed' ? 'badge-positive' : ''}`}>
                  {suite.expected_diagnostic ? 'Expected assertions' : suite.status}
                </span>
                <span className="suite-duration">{(suite.elapsed_ms / 1000).toFixed(2)}s</span>
              </summary>
              <div className="suite-body">
                <div className="suite-counters">
                  <span>{passed} passed</span>
                  <span>
                    {suite.cases.filter((item) => item.outcome === 'failed').length} failed
                  </span>
                  <code title={suite.image_id}>Image {suite.image_id.slice(0, 19)}…</code>
                </div>
                <div className="test-cases">
                  {visible.map((test) => (
                    <details className="test-case" key={test.name}>
                      <summary>
                        {test.outcome === 'passed' ? (
                          <CheckCircle size={17} className="text-positive" />
                        ) : (
                          <WarningCircle size={17} className="text-accent" />
                        )}
                        <code>{test.name}</code>
                        <span>{test.outcome}</span>
                      </summary>
                      <div>
                        {test.message && <p>{test.message}</p>}
                        {test.detail ? (
                          <pre tabIndex={0}>{test.detail}</pre>
                        ) : (
                          <p className="text-muted">
                            No additional output was recorded for this case.
                          </p>
                        )}
                      </div>
                    </details>
                  ))}
                  {visible.length === 0 && (
                    <p className="text-muted p-4 text-sm">
                      No cases in this suite match the filter.
                    </p>
                  )}
                </div>
                {suite.diagnostic && <p className="field-help mt-3">{suite.diagnostic}</p>}
                <div className="suite-logs">
                  <TerminalWindow size={17} />
                  <span>Captured output</span>
                  {logs.length ? (
                    logs.map((log) => (
                      <InspectArtifact key={log.id} artifactId={log.id}>
                        {log.label.split('/').at(-1)}
                      </InspectArtifact>
                    ))
                  ) : (
                    <span className="text-muted">No published logs</span>
                  )}
                </div>
              </div>
            </details>
          )
        })}
      </div>
      <p className="footnote">
        These captures describe this run’s original target. No post-implementation acceptance or
        regression check was performed.
      </p>
    </>
  )
}
