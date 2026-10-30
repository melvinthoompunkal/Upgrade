import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, workflowEnabled, type Catalog, type ProjectSelection } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { Notice } from '../../components/ui'

export function CatalogStart({ source }: { source: ProjectSelection | null }) {
  const { capabilities, refresh } = useWorkspace()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [files, setFiles] = useState(32)
  const [traces, setTraces] = useState(8)
  const submission = useRef({ body: '', key: '' })
  const pending = useRef(false)
  async function discover() {
    if (!source || pending.current) return
    const body = JSON.stringify({
      source_project: source.id,
      options: { max_files: files, trace_limit: traces },
    })
    if (submission.current.body !== body) submission.current = { body, key: crypto.randomUUID() }
    pending.current = true
    setBusy(true)
    setError('')
    try {
      const catalog = await api<Catalog>('/catalogs', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': submission.current.key },
      })
      refresh()
      navigate(`/catalogs/${catalog.id}?revision=${catalog.revision}`)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
      pending.current = false
    }
  }
  return (
    <section className="border-t border-line mt-8 pt-6" aria-label="Source feature discovery">
      <h2>Discover source features</h2>
      <p className="field-help mt-2">
        Build a saved catalog from this pinned source. Bounded public code excerpts and notices are
        sent to Meta. Target relevance and adaptation are unassessed.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 my-4">
        <div className="field-group">
          <label htmlFor="catalog-files">Files to scan this job</label>
          <input
            id="catalog-files"
            type="number"
            min={1}
            max={256}
            value={files}
            onChange={(event) => setFiles(Number(event.target.value))}
          />
        </div>
        <div className="field-group">
          <label htmlFor="catalog-traces">Features to corroborate deeply</label>
          <input
            id="catalog-traces"
            type="number"
            min={0}
            max={8}
            value={traces}
            onChange={(event) => setTraces(Number(event.target.value))}
          />
        </div>
      </div>
      <p className="field-help mb-4">
        Up to 500 hints, 32 model attempts, 400,000 returned input tokens, 40,000 returned output
        tokens and 20 minutes. Actual spend is recorded. Saved views use no model calls.
      </p>
      <button
        type="button"
        className="button-secondary"
        onClick={discover}
        disabled={
          busy ||
          !source ||
          source.support.capability !== 'evidence_analysis' ||
          !capabilities?.model_configured ||
          !capabilities?.worker_available ||
          !workflowEnabled(capabilities, 'catalog_discovery') ||
          files < 1 ||
          files > 256 ||
          traces < 0 ||
          traces > 8
        }
      >
        {busy ? 'Starting discovery…' : 'Discover features'}
      </button>
      {!capabilities?.model_configured && (
        <p className="field-help mt-3">
          Configure the backend model key to discover features. Structural analysis remains
          available.
        </p>
      )}
      {error && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
    </section>
  )
}
