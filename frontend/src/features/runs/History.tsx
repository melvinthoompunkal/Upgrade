import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, MagnifyingGlass, Plus, CaretDown, GitBranch } from '@phosphor-icons/react'
import { api, formatDate, kindLabel, type RunPage, type RunSummary } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Empty, ErrorState, RunBadge, Skeleton } from '../../components/ui'

export function History() {
  const { history, loading, error, refresh } = useWorkspace()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [pageError, setPageError] = useState('')
  const [moreLoading, setMoreLoading] = useState(false)
  useEffect(() => {
    if (history) {
      setRuns(history.items)
      setCursor(history.next_cursor)
    }
  }, [history])
  useEffect(() => {
    if (!runs.some((run) => run.status !== 'finished')) return
    const timer = window.setTimeout(refresh, 2000)
    return () => clearTimeout(timer)
  }, [runs, refresh])
  async function loadMore() {
    setMoreLoading(true)
    setPageError('')
    try {
      const page = await api<RunPage>(`/runs?cursor=${cursor}`)
      setRuns((previous) => [
        ...previous,
        ...page.items.filter((item) => !previous.some((existing) => existing.id === item.id)),
      ])
      setCursor(page.next_cursor)
    } catch (reason) {
      setPageError((reason as Error).message)
    } finally {
      setMoreLoading(false)
    }
  }
  const visible = runs.filter(
    (run) =>
      (filter === 'all' || run.kind === filter) &&
      `${run.title} ${run.id} ${run.outcome}`.toLowerCase().includes(query.toLowerCase()),
  )
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
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="filter-select">
            <select
              aria-label="Filter run kind"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
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
        ) : visible.length ? (
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
                {visible.map((run) => (
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
          <Empty title={runs.length ? 'No matching runs' : 'Your next improvement starts here'}>
            {runs.length
              ? 'Try another search or workflow filter.'
              : 'Start an analysis to see its progress and return to the results later.'}
          </Empty>
        )}
        <div className="table-footer">
          <span>
            {visible.length} shown{cursor !== null ? ' / more available' : ''}
          </span>
          {cursor !== null && (
            <button className="button-secondary" onClick={loadMore} disabled={moreLoading}>
              {moreLoading ? 'Loading…' : 'Load more runs'}
            </button>
          )}
        </div>
      </div>
      {pageError && (
        <div className="mt-4">
          <ErrorState message={pageError} />
        </div>
      )}
      <p className="footnote">
        Recorded CLI runs are imported from local reports at server startup. Opening a result never
        starts another model call.
      </p>
    </div>
  )
}
