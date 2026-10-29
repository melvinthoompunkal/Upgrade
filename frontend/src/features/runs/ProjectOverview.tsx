import { type Run, shortSha } from '../../api/client'
import { Citation } from '../evidence/Inspector'

export function ProjectOverview({ run }: { run: Run }) {
  return (
    <div className="space-y-6">
      <section className="surface p-6">
        <h2>
          {run.status !== 'finished'
            ? 'Inspecting pinned projects'
            : run.outcome === 'passed'
              ? 'Project analysis complete'
              : 'Project analysis stopped'}
        </h2>
        <p className="mt-2 text-muted">
          {run.error?.message ??
            'Static code structure and original test evidence. No feature was selected or adapted.'}
        </p>
      </section>
      {(run.project_analyses ?? []).map((analysis) => (
        <section className="surface p-5 md:p-7" key={analysis.project.id}>
          <h2>
            {analysis.project.requested.role === 'source' ? 'Source' : 'Target'}:{' '}
            {analysis.project.support.name}
          </h2>
          <p className="mt-2 break-all font-mono text-xs">
            {analysis.project.requested.url} @ {shortSha(analysis.project.snapshot.commit_sha)}
          </p>
          <p className="mt-2 text-sm">
            Project root: {analysis.project.requested.project_root || '/'} ·{' '}
            {analysis.project.support.python_files} Python files
          </p>
          <p className="field-help mt-3">{analysis.qualification}</p>
          <h3 className="mt-6">Production definitions</h3>
          <ul className="mt-3 divide-y divide-line">
            {analysis.entry_points.map((entry) => (
              <li
                key={`${entry.ref.path}:${entry.name}`}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <span className="min-w-0 break-all">
                  <strong>{entry.name}</strong>
                  <small className="block text-muted">{entry.repository_path}</small>
                </span>
                <Citation run={run} reference={entry.ref} label={`Inspect ${entry.name}`} />
              </li>
            ))}
          </ul>
          {analysis.entry_points.length === 0 && (
            <p className="field-help mt-3">
              No production definitions were available within this inspection’s limits.
            </p>
          )}
          <h3 className="mt-6">Test definitions</h3>
          <p className="field-help mt-2">
            {analysis.source_tests.length} test definitions inspected. Execution results are
            recorded separately in Tests.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {analysis.source_tests.map((entry) => (
              <Citation
                key={`${entry.ref.path}:${entry.name}`}
                run={run}
                reference={entry.ref}
                label={entry.name}
              />
            ))}
          </div>
          <details className="mt-6">
            <summary>Coverage and support limits</summary>
            <p className="field-help mt-3">
              {analysis.coverage.examined_paths.length} files with read windows;{' '}
              {analysis.coverage.unexamined_areas.length} files without read windows. AST indexing
              does not read every body.
            </p>
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
              {[
                ...analysis.coverage.limits_reached,
                ...(analysis.project.support.reasons ?? []),
                analysis.project.support.adaptation_reason,
              ].map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          </details>
        </section>
      ))}
    </div>
  )
}
