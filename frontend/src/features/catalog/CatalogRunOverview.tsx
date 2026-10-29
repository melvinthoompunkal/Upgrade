import { Link } from 'react-router-dom'
import type { Run } from '../../api/client'

export function CatalogRunOverview({ run }: { run: Run }) {
  return (
    <section className="surface p-5 space-y-4">
      <h2>
        {run.status === 'finished' ? 'Source discovery is saved.' : 'Source discovery is underway.'}
      </h2>
      <p>
        Open the catalog for discovered capabilities, evidence status, scan limits and continuation.
        This job does not assess target relevance, select a fit or implement an adaptation.
      </p>
      {run.catalog_id && (
        <Link
          className="text-action"
          to={`/catalogs/${run.catalog_id}?revision=${run.catalog_revision}`}
        >
          Open catalog revision {run.catalog_revision}
        </Link>
      )}
      <p className="field-help">
        Exports retain this job’s usage, batch ledger, input identity and full source notices.
      </p>
    </section>
  )
}
