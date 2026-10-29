import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  api,
  shortSha,
  type Catalog as CatalogData,
  type CatalogPage,
  type CatalogFeature,
  type RunSummary,
} from '../../api/client'
import { Empty, ErrorState, Notice, Skeleton } from '../../components/ui'
import { AssessmentPanel } from './AssessmentPanel'
import { AssessmentDetail } from './AssessmentDetail'
import { assessmentFinished, useAssessment } from './useAssessment'

const finished = (catalog: CatalogData) => !['queued', 'running'].includes(catalog.state)

export function Catalog() {
  const { catalogId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const revision = params.get('revision') ?? ''
  const selected = params.get('feature') ?? ''
  const [catalog, setCatalog] = useState<CatalogData | null>(null)
  const [page, setPage] = useState<CatalogPage | null>(null)
  const [feature, setFeature] = useState<CatalogFeature | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [detailError, setDetailError] = useState('')
  const pending = useRef(false)
  const submission = useRef({ body: '', key: '' })
  const detailTitle = useRef<HTMLHeadingElement>(null)
  const q = params.get('q') ?? ''
  const status = params.get('status') ?? ''
  const cursor = params.get('cursor') ?? ''
  const assessmentId = params.get('assessment') ?? ''
  const mode = params.get('mode') ?? (assessmentId ? 'relevant' : 'all')
  const category = params.get('category') ?? ''
  const sort = params.get('sort') ?? ''
  const relevanceStatus = params.get('relevance_status') ?? ''
  const fitStatus = params.get('fit_status') ?? ''
  const { assessment, assessmentError } = useAssessment(assessmentId)
  const matchingAssessment =
    assessment?.catalog_id === catalogId && String(assessment.catalog_revision) === revision
  const assessmentReady = Boolean(
    matchingAssessment && assessment && assessmentFinished(assessment),
  )
  const previousSelected = useRef(selected)
  const listScroll = useRef(0)
  useEffect(() => {
    if (previousSelected.current && !selected) {
      document.getElementById(`feature-${previousSelected.current}`)?.focus({ preventScroll: true })
      window.scrollTo(0, listScroll.current)
    }
    previousSelected.current = selected
  }, [selected])
  function change(fields: Record<string, string | null>) {
    const next = new URLSearchParams(params)
    for (const [name, value] of Object.entries(fields)) {
      if (value) next.set(name, value)
      else next.delete(name)
    }
    setParams(next)
  }
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    setCatalog(null)
    setPage(null)
    setFeature(null)
    setError('')
    const path = `/catalogs/${catalogId}${revision ? `?revision=${revision}` : ''}`
    async function load() {
      try {
        const result = await api<CatalogData>(path, { signal: controller.signal })
        if (controller.signal.aborted) return
        setCatalog(result)
        if (revision && !finished(result)) {
          async function poll() {
            try {
              const run = await api<RunSummary>(`/runs/${result.run_id}/status`, {
                signal: controller.signal,
              })
              if (run.status === 'finished') await load()
              else timer = setTimeout(poll, 2000)
            } catch (reason) {
              if (!controller.signal.aborted) setError((reason as Error).message)
            }
          }
          timer = setTimeout(poll, 1000)
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError((reason as Error).message)
      }
    }
    void load()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [catalogId, revision])

  useEffect(() => {
    if (catalog && !revision) {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          next.set('revision', String(catalog.revision))
          return next
        },
        { replace: true },
      )
    }
  }, [catalog, revision, setParams])

  const terminal = catalog ? finished(catalog) : false
  useEffect(() => {
    setPage(null)
    if (!terminal || (assessmentId && !assessmentReady) || (mode === 'relevant' && !assessmentId))
      return
    const controller = new AbortController()
    const query = new URLSearchParams({
      revision,
      q,
      ...(status ? { status } : {}),
      ...(cursor ? { cursor } : {}),
      ...(assessmentId ? { assessment_id: assessmentId } : {}),
      mode,
      ...(category ? { category } : {}),
      ...(sort ? { sort } : {}),
      ...(relevanceStatus ? { relevance_status: relevanceStatus } : {}),
      ...(fitStatus ? { fit_status: fitStatus } : {}),
    })
    const timer = setTimeout(
      () => {
        void api<CatalogPage>(`/catalogs/${catalogId}/features?${query}`, {
          signal: controller.signal,
        })
          .then((result) => {
            if (controller.signal.aborted) return
            setPage(result)
            setError('')
          })
          .catch((reason) => {
            if (!controller.signal.aborted) setError(reason.message)
          })
      },
      q ? 300 : 0,
    )
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [
    catalogId,
    revision,
    terminal,
    q,
    status,
    cursor,
    assessmentId,
    assessmentReady,
    mode,
    category,
    sort,
    relevanceStatus,
    fitStatus,
  ])

  useEffect(() => {
    setFeature(null)
    setDetailError('')
    if (!selected || !terminal) return
    const controller = new AbortController()
    api<CatalogFeature>(`/catalogs/${catalogId}/features/${selected}?revision=${revision}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setFeature(result)
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setDetailError(reason.message)
      })
    return () => controller.abort()
  }, [catalogId, revision, selected, terminal])
  useEffect(() => {
    if (feature) detailTitle.current?.focus()
  }, [feature])

  async function continueDiscovery(featureId?: string) {
    if (!catalog || pending.current) return
    const body = JSON.stringify({
      base_revision: catalog.revision,
      options: { max_files: featureId ? 0 : 32, trace_limit: featureId ? 1 : 8 },
      trace_feature_ids: featureId ? [featureId] : [],
    })
    if (submission.current.body !== body) submission.current = { body, key: crypto.randomUUID() }
    pending.current = true
    setBusy(true)
    setActionError('')
    try {
      const result = await api<CatalogData>(`/catalogs/${catalogId}/continuations`, {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': submission.current.key },
      })
      setParams({ revision: String(result.revision) })
    } catch (reason) {
      setActionError((reason as Error).message)
    } finally {
      pending.current = false
      setBusy(false)
    }
  }

  if (!catalog)
    return (
      <div className="page">{error ? <ErrorState message={error} /> : <Skeleton rows={8} />}</div>
    )
  return (
    <div className={`page catalog-page ${selected ? 'catalog-detail-open' : ''}`}>
      <Link className="back-link" to="/runs">
        All runs
      </Link>
      <div className="page-heading">
        <div>
          <h1>Source feature catalog</h1>
          <p className="text-muted mt-2">All discovered features · Revision {catalog.revision}</p>
        </div>
        <span className="badge">{catalog.state.replaceAll('_', ' ')}</span>
      </div>
      <div className="surface p-5 mb-5 space-y-3">
        <p>
          <code>{shortSha(catalog.source_sha)}</code> · Project root {catalog.project_root || '/'} ·
          Scope {catalog.scope || '/'}
        </p>
        <p className="field-help">{catalog.qualification}</p>
        <p aria-live="polite">
          {catalog.counts.discovered} discovered; {catalog.counts.code_supported} code supported;{' '}
          {catalog.counts.deeply_traced} deeply traced; {catalog.counts.unverified} unverified.
        </p>
        <p className="field-help">
          {catalog.processed_paths.length} files scanned; {catalog.remaining_paths.length} still to
          scan. {catalog.excluded_paths.length} excluded paths. Unread bodies and unsupported areas
          remain outside the proof.
        </p>
        {catalog.limit_reasons.length > 0 && (
          <Notice>{catalog.limit_reasons.join(', ').replaceAll('_', ' ')}</Notice>
        )}
        {catalog.failure_code && (
          <Notice tone="error">
            Discovery encountered {catalog.failure_code.replaceAll('_', ' ')}. Saved batches remain
            available.
          </Notice>
        )}
        <p className="field-help">
          This job: {catalog.usage.requests ?? 0} model attempts. Lineage:{' '}
          {catalog.cumulative_usage.requests ?? 0} attempts.
        </p>
        <Link className="text-action" to={`/runs/${catalog.run_id}/exports`}>
          Job activity, receipts and full notices
        </Link>
        {catalog.base_revision && (
          <p>
            <Link
              className="text-action"
              to={`/catalogs/${catalogId}?revision=${catalog.base_revision}`}
            >
              Open previous revision
            </Link>
          </p>
        )}
        {!terminal && (
          <p role="status">Discovery is {catalog.state}. You can leave and return to this URL.</p>
        )}
        {terminal && catalog.continuation_available && (
          <div>
            <p className="field-help mb-3">
              Continue with up to 32 more files and 8 deep traces. Additional allowance: 32 model
              attempts, 400,000 input / 40,000 output tokens, 20 minutes. Earlier revisions stay
              saved.
            </p>
            <button
              className="button-secondary"
              disabled={busy}
              onClick={() => continueDiscovery()}
            >
              {busy ? 'Starting…' : 'Continue discovery'}
            </button>
          </div>
        )}
        <details>
          <summary>Scope and exclusions</summary>
          <div className="mt-3 space-y-3 field-help">
            <p>
              Unexamined:{' '}
              {catalog.unexamined_areas.join(', ') ||
                'None recorded at file level; read windows define line coverage.'}
            </p>
            <p>Excluded: {catalog.excluded_paths.join(', ') || 'None recorded.'}</p>
          </div>
        </details>
      </div>
      {actionError && <Notice tone="error">{actionError}</Notice>}
      {terminal && (
        <>
          <AssessmentPanel
            key={`${catalog.id}-${catalog.revision}`}
            catalog={catalog}
            assessment={matchingAssessment ? assessment : null}
            selected={selected}
            onCreated={(result) =>
              change({
                assessment: result.id,
                mode: 'relevant',
                cursor: null,
                feature: null,
                relevance_status: null,
                fit_status: null,
              })
            }
          />
          {assessmentError && <ErrorState message={assessmentError} />}
          {assessment && !matchingAssessment && (
            <Notice tone="error">
              This assessment belongs to an earlier catalog revision. Clear the assessment or
              request a new context.
            </Notice>
          )}
          <div className="catalog-list">
            {assessmentReady && Boolean(assessment?.top3.length) && (
              <section className="mb-5 space-y-2" aria-label="Eligible recommendations">
                <h3>Eligible recommendations in assessed scope</h3>
                {assessment?.top3.map((id) => (
                  <p key={id}>
                    <Link
                      className="text-action"
                      to={`?${new URLSearchParams({ ...Object.fromEntries(params), feature: id })}`}
                    >
                      {page?.items.find((item) => item.feature_id === id)?.name ??
                        `Open eligible feature ${id}`}
                    </Link>
                  </p>
                ))}
              </section>
            )}
            <div
              className="flex flex-wrap gap-3 mb-5"
              role="group"
              aria-label="Feature browsing mode"
            >
              <button
                className={mode === 'all' ? 'button-primary' : 'button-secondary'}
                aria-pressed={mode === 'all'}
                onClick={() => change({ mode: 'all', cursor: null, feature: null, sort: null })}
              >
                All features
              </button>
              <button
                className={mode === 'relevant' ? 'button-primary' : 'button-secondary'}
                aria-pressed={mode === 'relevant'}
                disabled={!assessmentReady}
                onClick={() =>
                  change({ mode: 'relevant', cursor: null, feature: null, sort: null })
                }
              >
                Relevant to your project
              </button>
              {assessmentId && (
                <button
                  className="button-secondary"
                  onClick={() =>
                    change({
                      assessment: null,
                      mode: 'all',
                      cursor: null,
                      feature: null,
                      sort: null,
                      relevance_status: null,
                      fit_status: null,
                    })
                  }
                >
                  Clear assessment
                </button>
              )}
            </div>
            {assessmentReady && assessment?.counts.eligible === 0 && (
              <p className="field-help mb-4">
                No safe adaptation fit in the assessed scope. Relevant entries may still be useful
                to investigate.
              </p>
            )}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 mb-5">
              <div className="field-group">
                <label htmlFor="catalog-search">Search saved features</label>
                <input
                  id="catalog-search"
                  value={q}
                  maxLength={200}
                  onChange={(event) =>
                    change({ q: event.target.value, cursor: null, feature: null })
                  }
                />
              </div>
              <div className="field-group">
                <label htmlFor="catalog-category">Category</label>
                <input
                  id="catalog-category"
                  maxLength={60}
                  value={category}
                  placeholder="Exact category, or leave empty"
                  onChange={(e) =>
                    change({ category: e.target.value, cursor: null, feature: null })
                  }
                />
              </div>
              <div className="field-group">
                <label htmlFor="catalog-sort">Sort features</label>
                <select
                  id="catalog-sort"
                  value={sort || (mode === 'relevant' ? 'relevance' : 'category')}
                  onChange={(e) => change({ sort: e.target.value, cursor: null, feature: null })}
                >
                  <option value="category">Category, then name</option>
                  <option value="name">Name</option>
                  {assessmentReady && <option value="relevance">Relevance, then name</option>}
                </select>
              </div>
              {assessmentReady && (
                <>
                  <div className="field-group">
                    <label htmlFor="catalog-relevance">Assessment status</label>
                    <select
                      id="catalog-relevance"
                      value={relevanceStatus}
                      onChange={(e) =>
                        change({ relevance_status: e.target.value, cursor: null, feature: null })
                      }
                    >
                      <option value="">All assessment statuses</option>
                      {['unassessed', 'relevant', 'less_relevant', 'assessment_failed'].map((s) => (
                        <option key={s} value={s}>
                          {s.replaceAll('_', ' ')}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field-group">
                    <label htmlFor="catalog-fit">Fit eligibility</label>
                    <select
                      id="catalog-fit"
                      value={fitStatus}
                      onChange={(e) =>
                        change({ fit_status: e.target.value, cursor: null, feature: null })
                      }
                    >
                      <option value="">All fit statuses</option>
                      {['unassessed', 'ineligible', 'eligible'].map((s) => (
                        <option key={s}>{s}</option>
                      ))}
                    </select>
                  </div>
                </>
              )}
              <div className="field-group">
                <label htmlFor="catalog-status">Evidence status</label>
                <select
                  id="catalog-status"
                  value={status}
                  onChange={(event) =>
                    change({ status: event.target.value, cursor: null, feature: null })
                  }
                >
                  <option value="">All evidence statuses</option>
                  {['unverified', 'supported', 'partial', 'unsupported'].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
            {mode === 'relevant' && !assessmentId ? (
              <Notice>Request a goal/target assessment above to browse relevant entries.</Notice>
            ) : assessmentId && !assessmentReady ? (
              <Notice>
                {assessmentError ||
                  'Waiting for a matching finished assessment. All features remains available by clearing the assessment.'}
              </Notice>
            ) : error ? (
              <ErrorState message={error} />
            ) : !page ? (
              <Skeleton rows={5} />
            ) : (
              <>
                <p className="text-sm text-muted mb-3" role="status">
                  {page.matching_filter} matching features. Saved browsing uses no model calls.
                </p>
                {page.items.length === 0 && (
                  <Empty title="No matching features">
                    Change filters or review this scan’s coverage and failure state.
                  </Empty>
                )}
                <div className="space-y-3">
                  {page.items.map((item) => {
                    const link = new URLSearchParams(params)
                    link.set('feature', item.feature_id)
                    return (
                      <article className="border-t border-line py-5" key={item.feature_id}>
                        <div className="flex flex-wrap items-center gap-3">
                          <h2 className="text-lg">
                            <Link
                              id={`feature-${item.feature_id}`}
                              className="text-action"
                              to={`?${link}`}
                              onClick={() => {
                                listScroll.current = window.scrollY
                              }}
                            >
                              {item.name}
                            </Link>
                          </h2>
                          <span className="badge">{item.evidence_status}</span>
                        </div>
                        <p className="text-sm my-2 line-clamp-2">{item.summary}</p>
                        {item.assessment && (
                          <p className="text-sm mb-2">
                            {item.assessment.relevance_status.replaceAll('_', ' ')} · Fit:{' '}
                            {item.assessment.fit_status}. {item.assessment.reason}
                          </p>
                        )}
                        <p className="field-help">
                          {item.category} · {item.group} · {item.trace_state}
                          {item.parent_feature_id && ' · Subfeature (see detail for parent)'}
                        </p>
                      </article>
                    )
                  })}
                </div>
                <nav className="flex flex-wrap gap-4 my-5" aria-label="Catalog pages">
                  {cursor && (
                    <button
                      className="button-secondary"
                      onClick={() => change({ cursor: null, feature: null })}
                    >
                      First page
                    </button>
                  )}
                  {page.next_cursor && (
                    <button
                      className="button-secondary"
                      onClick={() => change({ cursor: page.next_cursor, feature: null })}
                    >
                      Next page
                    </button>
                  )}
                </nav>
              </>
            )}
          </div>
          {selected && page && !page.items.some((item) => item.feature_id === selected) && (
            <Notice>
              The selected feature is outside the visible result page or current filters. Its
              identity remains unchanged.
            </Notice>
          )}
          {selected && (detailError || !feature) && (
            <button className="button-secondary mb-4" onClick={() => change({ feature: null })}>
              Back to features
            </button>
          )}
          {selected &&
            (detailError ? (
              <ErrorState message={detailError} />
            ) : !feature ? (
              <Skeleton rows={4} />
            ) : (
              <section
                className="surface p-5 space-y-4"
                aria-label="Feature detail"
                onKeyDown={(e) => {
                  if (e.key === 'Escape') change({ feature: null })
                }}
              >
                <button className="button-secondary" onClick={() => change({ feature: null })}>
                  Back to features
                </button>
                <h2 ref={detailTitle} tabIndex={-1}>
                  {feature.name}
                </h2>
                <p>{feature.card?.behavior ?? feature.summary}</p>
                <p className="field-help">{feature.qualification}</p>
                {feature.card && (
                  <details>
                    <summary>Source dependencies and notices</summary>
                    <div className="space-y-3 mt-3 text-sm">
                      <p>
                        Notice identification: {feature.card.license_spdx ?? 'Unknown'}. Reuse
                        requires a separate policy decision.
                      </p>
                      <p>
                        Complete source notices are available in the discovery job exports above.
                      </p>
                      <ul className="space-y-2">
                        {feature.card.dependencies.map((dep) => (
                          <li key={`${dep.kind}-${dep.name}`}>
                            {dep.name} ({dep.kind}); cited at{' '}
                            {dep.refs.map((ref) => `${ref.path}:${ref.start_line}`).join(', ')}
                          </li>
                        ))}
                      </ul>
                      {!feature.card.dependencies.length && (
                        <p>No direct imports recorded. This does not prove dependency closure.</p>
                      )}
                    </div>
                  </details>
                )}
                {assessmentReady && (
                  <AssessmentDetail assessmentId={assessmentId} featureId={selected} />
                )}
                {feature.aliases.length > 0 && (
                  <p>Also discovered as: {feature.aliases.join(', ')}</p>
                )}
                {feature.parent_feature_id && (
                  <Link
                    className="text-action"
                    to={`?${new URLSearchParams({ ...Object.fromEntries(params), feature: feature.parent_feature_id })}`}
                  >
                    Open parent capability
                  </Link>
                )}
                {feature.missing_evidence.map((reason) => (
                  <Notice key={reason}>{reason}</Notice>
                ))}
                {feature.failure_code && (
                  <Notice tone="error">Trace failed: {feature.failure_code}</Notice>
                )}
                {feature.trace_state !== 'validated' && (
                  <>
                    <p className="field-help">
                      Deep trace uses an additional job: up to 32 attempts, 400,000 input / 40,000
                      output tokens and 20 minutes.
                    </p>
                    <button
                      className="button-secondary"
                      disabled={busy}
                      onClick={() => continueDiscovery(feature.feature_id)}
                    >
                      Corroborate this feature
                    </button>
                  </>
                )}
                <h3>Read-backed evidence</h3>
                {feature.evidence.map((item, index) => (
                  <details key={index}>
                    <summary>
                      {String(item.ref.path)}:{String(item.ref.start_line)}–
                      {String(item.ref.end_line)}
                    </summary>
                    <pre className="code-block mt-2">{String(item.text)}</pre>
                  </details>
                ))}
              </section>
            ))}
        </>
      )}
    </div>
  )
}
