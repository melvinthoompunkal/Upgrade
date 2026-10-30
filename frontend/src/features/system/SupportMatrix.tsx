import { useState } from 'react'
import { api } from '../../api/client'
import type { components } from '../../api/schema'
import { ErrorState, SectionHeading } from '../../components/ui'

export function SupportMatrix() {
  const [matrix, setMatrix] = useState<components['schemas']['SupportMatrix'] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function load() {
    setBusy(true)
    setError('')
    try {
      setMatrix(await api('/projects/support-matrix'))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="mt-9" aria-label="Repository support">
      <SectionHeading title="Repository support">
        Review the available execution and acceptance profiles before starting a job.
      </SectionHeading>
      <button className="button-secondary" disabled={busy} onClick={() => void load()}>
        {busy ? 'Loading support…' : 'Review supported profiles'}
      </button>
      {matrix && (
        <div className="mt-4 grid gap-3 text-sm" aria-live="polite">
          <dl className="grid gap-3 break-words">
            <div>
              <dt className="font-medium">Prepared runtime</dt>
              <dd>
                {matrix.runtime_profile}:{' '}
                {Object.entries(matrix.runtime_packages ?? {})
                  .map(([name, version]) => `${name} ${version}`)
                  .join(', ')}
              </dd>
            </div>
            <div>
              <dt className="font-medium">Native tests</dt>
              <dd>{matrix.native_adapters?.join(', ')}</dd>
            </div>
            <div>
              <dt className="font-medium">Acceptance</dt>
              <dd>{matrix.acceptance_adapters?.join(', ')}</dd>
            </div>
            <div>
              <dt className="font-medium">Source reuse</dt>
              <dd>
                {Object.keys(matrix.source_use_profiles ?? {}).join(', ')} with complete standard
                notices
              </dd>
            </div>
            <div>
              <dt className="font-medium">Revisions</dt>
              <dd>Full SHA, branch or tag resolved once to an immutable commit</dd>
            </div>
            <div>
              <dt className="font-medium">Pull requests</dt>
              <dd>
                {matrix.publishing_enabled
                  ? 'Verified public Python patches can be explicitly published as draft pull requests.'
                  : 'Publishing disabled. Exact patch and notice downloads remain available.'}
              </dd>
            </div>
            <div>
              <dt className="font-medium">Private projects</dt>
              <dd>
                {matrix.private_preflight_enabled
                  ? 'Snapshot support inspection available.'
                  : 'Snapshot retrieval disabled.'}{' '}
                Analysis disabled pending live validation.
              </dd>
            </div>
          </dl>
          <p className="font-medium">Unsupported</p>
          <ul className="list-disc pl-5 space-y-1">
            {matrix.unsupported?.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <ErrorState message={error} />}
    </section>
  )
}
