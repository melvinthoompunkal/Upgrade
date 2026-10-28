import { useEffect } from 'react'
import { Link, NavLink, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowUpRight,
  Clock,
  GitBranch,
  DownloadSimple,
  CheckCircle,
} from '@phosphor-icons/react'
import { formatDate, kindLabel, shortSha, artifactUrl } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Empty, ErrorState, Notice, RunBadge, Skeleton } from '../../components/ui'
import { Inspector } from '../evidence/Inspector'
import { useRun } from './useRun'
import { Overview } from './Overview'
import { Features } from '../evidence/Features'
import { Evidence } from '../evidence/Evidence'
import { Tests } from '../tests/Tests'
import { Exports, Changes } from './outputs'

const views = ['overview', 'features', 'evidence', 'tests', 'changes', 'exports'] as const

export function RunWorkspace() {
  const { runId = '', view = 'overview' } = useParams()
  const { run, events, error, reconnecting } = useRun(runId)
  const { refresh } = useWorkspace()
  useEffect(() => {
    if (run?.status === 'finished') refresh()
  }, [run?.status, runId, refresh])
  useEffect(() => {
    document.title = run ? `${run.title} — Upgrade` : 'Run — Upgrade'
    return () => {
      document.title = 'Upgrade — Feature workspace'
    }
  }, [run?.title])
  if (!run)
    return (
      <div className="page">{error ? <ErrorState message={error} /> : <Skeleton rows={9} />}</div>
    )
  const report =
    run.artifacts?.find((item) => item.label === 'report.md' && item.available) ??
    run.artifacts?.find((item) => item.label === 'report.json' && item.available)
  return (
    <div className="page run-page">
      <Link className="back-link" to="/runs">
        <ArrowLeft size={15} />
        All runs
      </Link>
      <div className="page-heading run-heading">
        <div>
          <div className="flex items-center gap-3 mb-3">
            <span className="text-muted text-xs">{kindLabel[run.kind]}</span>
            {run.historical && <span className="badge">Recorded CLI run</span>}
          </div>
          <h1>{run.title}</h1>
        </div>
        <RunBadge run={run} />
      </div>
      <div className="run-input-strip">
        <span>
          <GitBranch size={15} />
          Source{' '}
          <code title={run.source?.commit_sha}>
            {run.source
              ? `${run.source.origin.split('/').slice(-2).join('/')} @ ${shortSha(run.source.commit_sha)}`
              : 'Not recorded'}
          </code>
        </span>
        <span>
          <GitBranch size={15} />
          Target{' '}
          <code title={run.target?.commit_sha}>
            {run.target
              ? `${run.target.repo_id} @ ${shortSha(run.target.commit_sha)}`
              : run.kind === 'local_baseline'
                ? 'Working fixture files'
                : 'Not recorded'}
          </code>
        </span>
        <span className="run-created">
          <Clock size={15} />
          {formatDate(run.created_at)}
        </span>
      </div>
      {reconnecting && (
        <div className="my-4">
          <Notice>
            Connection lost. Reconnecting to this run; its execution continues on the server.
          </Notice>
        </div>
      )}
      {run.status !== 'finished' && (
        <div className="progress-region" role="status">
          <span className="progress-indicator" />
          <div>
            <strong>
              {run.status === 'queued'
                ? 'Waiting for the local worker'
                : ({
                    inputs: 'Pinning inputs',
                    checks: 'Running checks',
                    analysis: 'Analyzing source and target',
                    selection: 'Assessing feature fit',
                  }[run.stage] ?? 'Analysis in progress')}
            </strong>
            <p>
              {events.at(-1)?.message ?? 'The run is saved. You can leave and return to this URL.'}
            </p>
          </div>
          <span className="badge">{run.status === 'queued' ? 'Queued' : 'Running'}</span>
        </div>
      )}
      {run.error && (
        <div className="my-4">
          <Notice tone="error">
            <strong>{run.error.code.replaceAll('_', ' ')}</strong>
            <p>{run.error.message}</p>
          </Notice>
        </div>
      )}
      {(run.warnings ?? []).map((warning) => (
        <div key={warning} className="my-4">
          <Notice>{warning}</Notice>
        </div>
      ))}
      <div className="run-tabs" aria-label="Run views">
        <nav>
          {views.map((tab) => (
            <NavLink
              key={tab}
              to={`/runs/${runId}/${tab}`}
              className={view === tab ? 'tab-active' : ''}
            >
              {tab[0].toUpperCase() + tab.slice(1)}
              {tab === 'features' &&
                run.analyzed_feature_count !== null &&
                run.analyzed_feature_count !== undefined && (
                  <span>{run.analyzed_feature_count}</span>
                )}
            </NavLink>
          ))}
        </nav>
        {report && (
          <a href={artifactUrl(run.id, report.id)} download className="tab-download">
            <DownloadSimple size={16} />
            <span>Report</span>
          </a>
        )}
      </div>
      <div className="run-view">
        {view === 'overview' ? (
          <Overview run={run} />
        ) : view === 'features' ? (
          <Features run={run} />
        ) : view === 'evidence' ? (
          <Evidence run={run} />
        ) : view === 'tests' ? (
          <Tests run={run} />
        ) : view === 'changes' ? (
          <Changes run={run} />
        ) : view === 'exports' ? (
          <Exports run={run} />
        ) : (
          <Empty title="Unknown run view">Choose a view above to inspect this run.</Empty>
        )}
      </div>
      <details className="activity-panel">
        <summary>
          <Clock size={16} />
          Run activity
          <span>
            {events.length
              ? `${events.length} recent events (up to 200 shown)`
              : run.historical
                ? 'Historical receipt'
                : 'No events yet'}
          </span>
        </summary>
        <div className="activity-list">
          {events.length ? (
            events.map((event) => (
              <div key={event.sequence}>
                <CheckCircle size={15} />
                <time>{formatDate(event.timestamp)}</time>
                <span>{event.message}</span>
              </div>
            ))
          ) : (
            <p className="text-muted text-sm">
              {run.historical
                ? 'This run was completed from the CLI. Its report contains the original calls, usage, captures, and controller provenance.'
                : 'Activity will appear when the worker starts.'}
            </p>
          )}
        </div>
      </details>
      {run.status === 'finished' && (
        <div className="run-finish-note">
          <CheckCircle size={16} />
          <span>
            Saved locally.{' '}
            {run.kind === 'fit_select'
              ? 'Selection is an analysis result; no adaptation was implemented.'
              : 'Open any evidence or export to inspect the result.'}
          </span>
          <Link to="/">
            New run
            <ArrowUpRight size={14} />
          </Link>
        </div>
      )}
      <Inspector run={run} />
    </div>
  )
}
