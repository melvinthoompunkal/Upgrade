import { Link } from 'react-router-dom'
import { type Run } from '../../api/client'

export function AdaptationOverview({ run }: { run: Run }) {
  const result = run.adaptation
  return (
    <section className="surface p-5 space-y-4">
      <h2>
        {run.status !== 'finished'
          ? 'Verifying the selected adaptation.'
          : result?.verdict === 'verified'
            ? 'The patch passed verification.'
            : result?.verdict === 'already_present'
              ? 'The acceptance behavior is already present.'
              : 'The adaptation remains unverified.'}
      </h2>
      <p>
        {run.status !== 'finished'
          ? 'Planning, frozen acceptance, original baseline and production verification appear as they complete.'
          : 'Review the production diff, frozen tests, captures and source notices in Changes.'}
      </p>
      {result && (
        <dl className="space-y-2">
          <div>
            <dt>Production files / changed lines</dt>
            <dd>
              {result.changed_files} / {result.changed_lines}
            </dd>
          </div>
          <div>
            <dt>Repair rounds</dt>
            <dd>{result.repair_count}</dd>
          </div>
          <div>
            <dt>Protected tests</dt>
            <dd>
              {result.protected_tests_unchanged
                ? 'Unchanged and hash checked'
                : 'Verification incomplete'}
            </dd>
          </div>
          <div>
            <dt>Original target</dt>
            <dd>{run.original_target_unchanged ? 'Preserved' : 'See preservation evidence'}</dd>
          </div>
        </dl>
      )}
      <div className="flex flex-wrap gap-4">
        <Link className="text-action" to={`/runs/${run.id}/changes`}>
          Review changes
        </Link>
        <Link className="text-action" to={`/runs/${run.id}/tests`}>
          Test evidence
        </Link>
        <Link className="text-action" to={`/runs/${run.id}/exports`}>
          Patch and evidence exports
        </Link>
      </div>
    </section>
  )
}
