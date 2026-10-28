import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowUpRight, MagnifyingGlass, Plus, CaretDown, GitBranch } from '@phosphor-icons/react'
import { api, formatDate, kindLabel, type RunPage, type RunSummary } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Empty, ErrorState, RunBadge, Skeleton } from '../../components/ui'

export function History() {
  const { error, refresh, session } = useWorkspace()
  const [params, setParams] = useSearchParams()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const query = params.get('q') ?? ''
  const filter = params.get('kind') ?? 'all'
  const currentCursor = Math.max(0, Number(params.get('cursor')) || 0)
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState('')
  const [revision, setRevision] = useState(0)
  const requestQuery = params.toString()
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    const timer = window.setTimeout(() => {
      api<RunPage>(`/runs?${requestQuery}`, { signal: controller.signal })
        .then((page) => {
          if (controller.signal.aborted) return
          setRuns(page.items)
          setCursor(page.next_cursor)
          setPageError('')
        })
        .catch((reason: Error) => {
          if (!controller.signal.aborted) setPageError(reason.message)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, 200)
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [requestQuery, revision])
  useEffect(() => {
    if (!runs.some((run) => run.status !== 'finished')) return
    const timer = window.setTimeout(() => setRevision((value) => value + 1), 2000)
    return () => clearTimeout(timer)
  }, [runs, revision])
  function change(key: string, value: string) {
    const next = new URLSearchParams(params)
    next.delete('cursor')
    value && value !== 'all' ? next.set(key, value) : next.delete(key)
    setParams(next, { replace: key === 'q' })
  }
  function goTo(cursor: number) {
    const next = new URLSearchParams(params)
    next.set('cursor', String(cursor))
    setParams(next)
  }
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Your runs</h1>
          <p>Every analysis, and the evidence that came with it.</p>
        </div>
        <Link className="button-primary" to="/">
          <Plus size={17} />
          New run
        </Link>
      </div>
      {error && <ErrorState message={error} retry={refresh} />}
      <div className="surface overflow-hidden">
        <div className="table-toolbar">
          <div className="search-input">
            <MagnifyingGlass size={18} />
            <input
              aria-label="Search runs"
              placeholder="Search runs…"
              value={query}
              onChange={(event) => change('q', event.target.value)}
            />
          </div>
          <div className="filter-select">
            <select
              aria-label="Filter run kind"
              value={filter}
              onChange={(event) => change('kind', event.target.value)}
            >
              <option value="all">All workflows</option>
              {Object.entries(kindLabel).map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
            <CaretDown size={14} />
          </div>
        </div>
        {loading ? (
          <div className="px-6">
            <Skeleton rows={6} />
          </div>
        ) : runs.length ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Workflow</th>
                  <th>Outcome</th>
                  <th>Started</th>
                  <th>
                    <span className="sr-only">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link className="run-title-link" to={`/runs/${run.id}/overview`}>
                        <GitBranch size={18} />
                        <span>
                          {run.title}
                          <small>
                            {run.historical ? 'Recorded CLI run' : 'Browser run'} /{' '}
                            {run.id.slice(-8)}
                          </small>
                        </span>
                      </Link>
                    </td>
                    <td>{kindLabel[run.kind]}</td>
                    <td>
                      <RunBadge run={run} />
                    </td>
                    <td className="whitespace-nowrap text-muted">{formatDate(run.created_at)}</td>
                    <td>
                      <Link
                        className="icon-button"
                        to={`/runs/${run.id}/overview`}
                        aria-label={`Open ${run.title}`}
                      >
                        <ArrowUpRight size={18} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={
              query || filter !== 'all' ? 'No matching runs' : 'Your next improvement starts here'
            }
          >
            {query || filter !== 'all'
              ? 'Try another search or workflow filter.'
              : 'Start an analysis to see its progress and return to the results later.'}
          </Empty>
        )}
        <div className="table-footer">
          <span>
            {runs.length} shown{cursor !== null ? ' / more available' : ''}
          </span>
          <div className="flex gap-3">
            {currentCursor > 0 && (
              <button
                className="button-secondary"
                onClick={() => goTo(Math.max(0, currentCursor - 30))}
                disabled={loading}
              >
                Previous page
              </button>
            )}
            {cursor !== null && (
              <button className="button-secondary" onClick={() => goTo(cursor)} disabled={loading}>
                Next page
              </button>
            )}
          </div>
        </div>
      </div>
      {pageError && (
        <div className="mt-4">
          <ErrorState message={pageError} />
        </div>
      )}
      <p className="footnote">
        {session?.mode === 'github'
          ? 'Only your account’s runs appear here. Existing local reports stay in the local workspace.'
          : 'Recorded CLI runs are imported into the local workspace at server startup.'}{' '}
        Opening a result never starts another model call.
      </p>
    </div>
  )
}
