import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { GithubLogo } from '@phosphor-icons/react'
import { api } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { ErrorState } from '../../components/ui'

export function SignIn() {
  const location = useLocation()
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Your projects, connected</h1>
          <p>Sign in to browse your repositories and keep your analyses private to your account.</p>
        </div>
      </div>
      <div className="surface p-6 max-w-xl">
        <GithubLogo size={32} className="mb-5 text-accent" />
        <h2>Continue with GitHub</h2>
        <p className="text-muted leading-relaxed mt-3 mb-6">
          Sign-in identifies your account. You choose which repositories the Upgrade app can access
          separately on GitHub.
        </p>
        <a
          className="button-primary"
          href={`/api/auth/github/start?return_to=${encodeURIComponent(location.pathname + location.search)}`}
        >
          Sign in with GitHub
        </a>
      </div>
    </div>
  )
}

export function AccountActions() {
  const { session, refresh } = useWorkspace()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (session?.mode !== 'github' || !session.authenticated) return null
  async function action(path: string) {
    setBusy(true)
    setError('')
    try {
      await api(path, { method: 'POST' })
      if (path.endsWith('logout')) window.location.assign('/')
      else refresh()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="px-2 pb-4 flex flex-wrap gap-3 text-xs">
      <button
        className="text-muted hover:text-accent"
        disabled={busy}
        onClick={() => void action('/auth/logout')}
      >
        Sign out
      </button>
      {session.connection === 'connected' ? (
        <button
          className="text-muted hover:text-accent"
          disabled={busy}
          onClick={() => void action('/auth/disconnect')}
        >
          Disconnect GitHub
        </button>
      ) : (
        <a href="/api/auth/github/start">Reconnect GitHub</a>
      )}
      {error && <ErrorState message={error} />}
    </div>
  )
}
