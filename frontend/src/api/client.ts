import type { components, operations } from './schema'

type Schemas = components['schemas']
export type Run = Schemas['RunDetail']
export type RunSummary = Schemas['RunSummary']
export type RunPage = Schemas['RunPage']
export type Capabilities = Schemas['Capabilities']
export type Target = Schemas['Target']
export type Reference = Schemas['Reference']
export type Ref = Schemas['Ref']
export type ReferenceDetail = Schemas['ReferenceDetail']
export type Artifact = Schemas['Artifact']
export type ArtifactText = Schemas['ArtifactText']
export type Event = Schemas['Event']
export type EventPage = Schemas['EventPage']
export type Session = Schemas['SessionView']
export type RepositoryPage = Schemas['RepositoryPage']
export type Snapshot = Schemas['SnapshotView']
export type ProjectSelection = Schemas['ProjectSelection']
export type InstallationPage = Schemas['InstallationPage']
export type Feature = Schemas['FeatureCard']
export type Catalog = Schemas['CatalogView']
export type CatalogPage = Schemas['FeaturePage']
export type CatalogFeature = Schemas['FeatureDetail']
export type Assessment = Schemas['AssessmentView']
export type FeatureAssessment = Schemas['FeatureAssessment']
export type RunRequest =
  operations['create_run_api_runs_post']['requestBody']['content']['application/json']

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  if (options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase())) {
    const session = await api<Session>('/session', { signal: options.signal })
    if (session.csrf_token) headers.set('X-CSRF-Token', session.csrf_token)
  }
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new Error('Cannot reach the local backend. Start the server on port 8000, then retry.')
  }
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new window.Event('upgrade:session-expired'))
    const body = await response.json().catch(() => null)
    const message =
      typeof body?.detail === 'string'
        ? body.detail
        : Array.isArray(body?.detail)
          ? body.detail.map((item: { msg: string }) => item.msg).join(' ')
          : `Request failed (${response.status}). Try again.`
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export function createRun(request: RunRequest, key: string) {
  return api<RunSummary>('/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    body: JSON.stringify(request),
  })
}

export const artifactUrl = (runId: string, id: string) =>
  `/api/runs/${encodeURIComponent(runId)}/artifacts/${id}`
export const shortSha = (sha?: string | null) => (sha ? sha.slice(0, 8) : 'Not recorded')
export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}
export const kindLabel: Record<RunSummary['kind'], string> = {
  fit_select: 'Feature selection',
  source_trace: 'Source trace',
  pinned_baseline: 'Committed baseline',
  local_baseline: 'Working-file baseline',
  meta_smoke: 'Meta check',
  project_analysis: 'Project analysis',
  catalog_discovery: 'Source discovery',
  relevance_assessment: 'Relevance assessment',
}
