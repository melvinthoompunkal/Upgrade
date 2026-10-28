import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, type Capabilities, type RunPage, type Session, type Target } from '../api/client'

type Workspace = {
  session: Session | null
  capabilities: Capabilities | null
  targets: Target[]
  history: RunPage | null
  error: string
  loading: boolean
  refresh: () => void
}
const Context = createContext<Workspace | null>(null)
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [targets, setTargets] = useState<Target[]>([])
  const [history, setHistory] = useState<RunPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision((value) => value + 1), [])
  useEffect(() => {
    const expired = () => {
      setSession(null)
      setHistory(null)
      setTargets([])
      setCapabilities(null)
      setLoading(true)
      refresh()
    }
    window.addEventListener('upgrade:session-expired', expired)
    window.addEventListener('focus', refresh)
    return () => {
      window.removeEventListener('upgrade:session-expired', expired)
      window.removeEventListener('focus', refresh)
    }
  }, [refresh])
  useEffect(() => {
    const controller = new AbortController()
    api<Session>('/session', { signal: controller.signal })
      .then(async (current) => {
        if (controller.signal.aborted) return
        setSession(current)
        if (!current.authenticated) {
          setHistory(null)
          setTargets([])
          setCapabilities(null)
          setError('')
          return
        }
        const [cap, projects, runs] = await Promise.all([
          api<Capabilities>('/capabilities', { signal: controller.signal }),
          api<Target[]>('/targets', { signal: controller.signal }),
          api<RunPage>('/runs', { signal: controller.signal }),
        ])
        if (controller.signal.aborted) return
        setCapabilities(cap)
        setTargets(projects)
        setHistory(runs)
        setError('')
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [revision])
  return (
    <Context.Provider value={{ session, capabilities, targets, history, error, loading, refresh }}>
      {children}
    </Context.Provider>
  )
}
export function useWorkspace() {
  const value = useContext(Context)
  if (!value) throw new Error('Workspace provider is required.')
  return value
}
