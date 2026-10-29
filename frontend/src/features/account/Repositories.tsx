import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowSquareOut, GithubLogo, MagnifyingGlass } from '@phosphor-icons/react'
import { api, type InstallationPage, type RepositoryPage } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Empty, ErrorState, Notice, Skeleton } from '../../components/ui'
import { PinRepository } from './PinRepository'

export function Repositories() {
  const { session } = useWorkspace()
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState<RepositoryPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [revision, setRevision] = useState(0)
  const [installations, setInstallations] = useState<InstallationPage | null>(null)
  const [checking, setChecking] = useState(false)
  const query = params.toString()
  const cursor = Math.max(0, Number(params.get('cursor')) || 0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    const timer = window.setTimeout(() => {
      api<RepositoryPage>(`/repositories?${query}`, { signal: controller.signal })
        .then((result) => {
          if (!controller.signal.aborted) {
            setPage(result)
            setError('')
          }
        })
        .catch((reason: Error) => {
          if (!controller.signal.aborted) setError(reason.message)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, 200)
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [query, revision])
  function change(key: string, value: string) {
    const next = new URLSearchParams(params)
    next.delete('cursor')
    value ? next.set(key, value) : next.delete(key)
    setParams(next, { replace: key === 'q' })
  }
  async function sync() {
    setSyncing(true)
    setError('')
    try {
      await api<RepositoryPage>(`/repositories/sync?restart=${page?.sync_status !== 'partial'}`, {
        method: 'POST',
      })
      setRevision((value) => value + 1)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSyncing(false)
    }
  }
  async function checkInstallations(number = 1) {
    setChecking(true)
    try {
      setInstallations(await api<InstallationPage>(`/github/installations?page_number=${number}`))
      setError('')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setChecking(false)
    }
  }
  return (
    <div className="page">
      <div className="page-heading flex-wrap">
        <div>
          <h1>Your repositories</h1>
          <p>Projects that both your GitHub account and the Upgrade app can access.</p>
        </div>
        <button
          className="button-primary"
          onClick={() => void sync()}
          disabled={syncing || session?.connection !== 'connected'}
        >
          {syncing
            ? 'Syncing repositories…'
            : page?.sync_status === 'partial'
              ? 'Continue sync'
              : 'Sync repositories'}
        </button>
      </div>
      <Notice>
        Repository connections currently support browsing and indexing. Project analysis requires a
        supported adapter; the crawler example remains available in New run.
      </Notice>
      <div className="flex flex-wrap gap-4 items-center my-6 text-muted">
        <span aria-live="polite">
          {page
            ? `${page.discovered_count} indexed / ${page.sync_status.replaceAll('_', ' ')}`
            : 'Loading connection state…'}
        </span>
        {session?.install_url && (
          <a
            className="inline-flex gap-2 items-center text-accent"
            href={session.install_url}
            target="_blank"
            rel="noreferrer"
          >
            Manage repository access
            <ArrowSquareOut size={15} />
          </a>
        )}
        <button
          className="text-accent"
          disabled={checking || session?.connection !== 'connected'}
          onClick={() => void checkInstallations()}
        >
          {checking ? 'Checking installations…' : 'Check installations'}
        </button>
      </div>
      {installations && (
        <div className="surface p-5 mb-5 text-sm" aria-live="polite">
          {installations.items.length ? (
            <ul className="grid gap-2">
              {installations.items.map((installation) => (
                <li key={installation.id}>
                  {installation.account.login}:{' '}
                  {installation.suspended
                    ? 'Suspended'
                    : !installation.contents_readable
                      ? 'Contents permission required'
                      : `${installation.repository_selection} repositories`}
                </li>
              ))}
            </ul>
          ) : (
            <p>
              No approved installations are accessible. Use Manage repository access; an
              organization may require administrator approval.
            </p>
          )}
          {installations.next_page && (
            <button
              className="button-secondary mt-4"
              disabled={checking}
              onClick={() => void checkInstallations(installations.next_page!)}
            >
              Next installations
            </button>
          )}
        </div>
      )}
      {error && (
        <div className="mb-5">
          <ErrorState message={error} retry={() => setRevision((value) => value + 1)} />
        </div>
      )}
      <div className="surface overflow-hidden">
        <div className="table-toolbar flex-wrap">
          <div className="search-input">
            <MagnifyingGlass size={18} />
            <input
              aria-label="Search repositories"
              placeholder="Search repositories…"
              value={params.get('q') ?? ''}
              onChange={(event) => change('q', event.target.value)}
            />
          </div>
          <input
            aria-label="Repository owner"
            className="max-w-44"
            placeholder="Owner or organization"
            maxLength={100}
            value={params.get('owner') ?? ''}
            onChange={(event) => change('owner', event.target.value)}
          />
          <select
            aria-label="Repository visibility"
            className="max-w-44"
            value={params.get('visibility') ?? 'all'}
            onChange={(event) => change('visibility', event.target.value)}
          >
            <option value="all">All visibility</option>
            <option value="private">Private</option>
            <option value="public">Public</option>
          </select>
        </div>
        {loading ? (
          <div className="p-6">
            <Skeleton rows={5} />
          </div>
        ) : page?.items.length ? (
          <ul className="divide-y divide-line">
            {page.items.map((repo) => (
              <li key={repo.id} className="flex gap-4 items-start p-5">
                <GithubLogo size={22} className="shrink-0 mt-1 text-muted" />
                <div className="min-w-0">
                  <a
                    className="font-medium break-all hover:text-accent"
                    href={`https://github.com/${repo.full_name}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {repo.full_name}
                  </a>
                  <p className="text-muted mt-2 text-xs">
                    {repo.private ? 'Private' : 'Public'} /{' '}
                    {repo.archived ? 'Archived' : repo.default_branch} / Browse and index
                  </p>
                  <p className="text-muted mt-2 text-xs">{repo.support_reason}</p>
                  {session?.private_fetch_enabled && <PinRepository repo={repo} />}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty
            title={
              page?.sync_status === 'not_synced'
                ? 'Connect your project list'
                : 'No matching repositories'
            }
          >
            Sync repositories to load current grants. Use Manage repository access if a project is
            missing.
          </Empty>
        )}
        <div className="table-footer">
          <span>{page?.items.length ?? 0} shown</span>
          <div className="flex gap-3">
            {cursor > 0 && (
              <button
                className="button-secondary"
                onClick={() => {
                  const next = new URLSearchParams(params)
                  next.set('cursor', String(Math.max(0, cursor - 25)))
                  setParams(next)
                }}
              >
                Previous page
              </button>
            )}
            {page?.next_cursor != null && (
              <button
                className="button-secondary"
                onClick={() => {
                  const next = new URLSearchParams(params)
                  next.set('cursor', String(page.next_cursor))
                  setParams(next)
                }}
              >
                Next page
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
