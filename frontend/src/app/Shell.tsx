import { Suspense, useEffect, useState } from 'react'
import { NavLink, Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowUpRight,
  Plus,
  Stack,
  Pulse,
  CaretRight,
  List,
  X,
  ArrowSquareOut,
  CheckCircle,
  WarningCircle,
  Clock,
  GitBranch,
} from '@phosphor-icons/react'
import { useWorkspace } from './workspace-context'
import { ErrorState, Skeleton } from '../components/ui'
import { AccountActions, SignIn } from '../features/account/Account'

export function Shell() {
  const { capabilities, history, error, session } = useWorkspace()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.key.toLowerCase() !== 'n' ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        document.querySelector('dialog[open]') ||
        (event.target instanceof HTMLElement &&
          (event.target.isContentEditable || /INPUT|SELECT|TEXTAREA/.test(event.target.tagName)))
      )
        return
      event.preventDefault()
      navigate('/')
      setMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])
  const destination =
    location.pathname === '/'
      ? 'New run'
      : location.pathname === '/system'
        ? 'System checks'
        : location.pathname === '/repositories'
          ? 'Repositories'
          : 'Runs'
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`} aria-label="Main navigation">
        <Link to="/" className="brand" onClick={() => setMenuOpen(false)}>
          <span className="brand-mark">
            <ArrowUpRight size={24} weight="bold" />
          </span>
          upgrade<span className="brand-period">.</span>
        </Link>
        <div className="sidebar-workspace">
          <span className="workspace-avatar">
            <GitBranch size={17} />
          </span>
          <div>
            <strong>
              {session?.mode === 'github'
                ? (session.user?.login ?? 'GitHub workspace')
                : 'Local workspace'}
            </strong>
            <span>Source to possibility</span>
          </div>
        </div>
        <nav className="main-nav">
          {[
            { to: '/', text: 'New run', icon: Plus },
            { to: '/runs', text: 'Runs', icon: Stack },
            { to: '/system', text: 'System checks', icon: Pulse },
            ...(session?.mode === 'github' && session.authenticated
              ? [{ to: '/repositories', text: 'Repositories', icon: GitBranch }]
              : []),
          ].map((item) => (
            <NavLink
              to={item.to}
              end={item.to === '/'}
              aria-keyshortcuts={item.to === '/' ? 'N' : undefined}
              onClick={() => setMenuOpen(false)}
              key={item.to}
            >
              <item.icon size={20} />
              <span>{item.text}</span>
              {item.to === '/' && (
                <span className="nav-key" aria-hidden="true">
                  N
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="recent-runs">
          <div className="sidebar-label">
            Recent runs
            <Link to="/runs" aria-label="View all runs">
              <ArrowUpRight size={15} />
            </Link>
          </div>
          {history?.items.slice(0, 4).map((run) => (
            <Link
              to={`/runs/${run.id}/overview`}
              key={run.id}
              onClick={() => setMenuOpen(false)}
              className="recent-run"
            >
              {run.status !== 'finished' ? (
                <Clock size={15} />
              ) : ['selected', 'passed'].includes(run.outcome ?? '') ? (
                <CheckCircle size={15} />
              ) : (
                <WarningCircle size={15} />
              )}
              <span>{run.title}</span>
            </Link>
          ))}
          {history?.items.length === 0 && (
            <p className="text-muted text-xs leading-relaxed">Your analyses will appear here.</p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="local-note">
            <span className={`connection-dot ${error ? 'connection-offline' : ''}`} />
            <strong>{error ? 'Backend unavailable' : 'Runs on your machine'}</strong>
            <p>Evidence stays in your local workspace.</p>
          </div>
          <a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">
            API reference
            <ArrowSquareOut size={15} />
          </a>
          <div className="sidebar-user">
            <span>U</span>
            <div>
              <strong>{session?.user?.login ?? 'Upgrade workspace'}</strong>
              <small>
                {session?.mode === 'github' ? 'Account workspace' : 'Local development'}
              </small>
            </div>
          </div>
          <AccountActions />
        </div>
      </aside>
      {menuOpen && (
        <button
          className="mobile-scrim"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div className="flex items-center gap-3">
            <button
              className="icon-button mobile-menu"
              aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(!menuOpen)}
            >
              {menuOpen ? <X size={20} /> : <List size={20} />}
            </button>
            <span className="text-muted">Workspace</span>
            <CaretRight size={12} className="text-muted" />
            <span>{destination}</span>
          </div>
          <div className="topbar-status">
            <span className={`connection-dot ${error ? 'connection-offline' : ''}`} />
            <span>{error ? 'Offline' : capabilities ? 'Local connection' : 'Connecting'}</span>
            <span className="topbar-divider" />
            <span className="model-label">Muse Spark</span>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          <Suspense
            fallback={
              <div className="page">
                <Skeleton rows={7} />
              </div>
            }
          >
            {!session ? (
              <div className="page">
                {error ? <ErrorState message={error} /> : <Skeleton rows={7} />}
              </div>
            ) : !session.authenticated ? (
              <SignIn />
            ) : (
              <Outlet />
            )}
          </Suspense>
        </main>
        <footer className="page-footer">
          <span>Built on evidence. Made for better software.</span>
          <span>Upgrade / Local workspace</span>
        </footer>
      </div>
    </div>
  )
}
