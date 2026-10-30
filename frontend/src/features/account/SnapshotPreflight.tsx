import { useEffect, useRef, useState } from 'react'
import { api, type ProjectSelection, type Snapshot } from '../../api/client'
import type { components } from '../../api/schema'
import { ErrorState } from '../../components/ui'

type Disclosure = components['schemas']['Disclosure']

export function SnapshotPreflight({ snapshot }: { snapshot: Snapshot }) {
  const [project, setProject] = useState<ProjectSelection | null>(null)
  const [disclosure, setDisclosure] = useState<Disclosure | null>(null)
  const [source, setSource] = useState(snapshot.role === 'source' ? snapshot.id : '')
  const [target, setTarget] = useState(snapshot.role === 'target' ? snapshot.id : '')
  const [accepted, setAccepted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  async function action(kind: 'inspect' | 'disclose' | 'consent') {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    try {
      if (kind === 'inspect') {
        const value = await api<ProjectSelection>(`/projects/from-snapshot/${snapshot.id}`, {
          method: 'POST',
          signal: controller.signal,
        })
        if (!controller.signal.aborted) setProject(value)
      } else {
        const inputs = { source_snapshot: source, target_snapshot: target || null }
        const value = await api<Disclosure>(
          `/privacy/model-${kind === 'consent' ? 'consent' : 'disclosure'}`,
          {
            method: 'POST',
            signal: controller.signal,
            body: JSON.stringify(
              kind === 'consent'
                ? {
                    ...inputs,
                    identity: disclosure!.identity,
                    policy: disclosure!.policy,
                    accepted,
                  }
                : inputs,
            ),
          },
        )
        if (!controller.signal.aborted) {
          setDisclosure(value)
          setAccepted(value.consented)
        }
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message)
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }
  return (
    <div className="mt-4 grid gap-3">
      <button
        type="button"
        className="button-secondary justify-self-start"
        disabled={busy}
        onClick={() => void action('inspect')}
      >
        Inspect snapshot support
      </button>
      {project && (
        <div className="text-sm" aria-live="polite">
          <p>
            {project.support.name ?? 'Python project'}: {project.support.python_files} Python files.
          </p>
          <p>
            {project.support.execution_supported
              ? `Offline adapter: ${project.support.adapter_version}.`
              : 'Offline tests unsupported.'}
          </p>
          {project.support.reasons?.map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
          <p>
            Private analysis, discovery, relevance and adaptation are disabled pending live consent,
            access, revocation and organization-policy validation.
          </p>
        </div>
      )}
      <details>
        <summary className="cursor-pointer">Review private-code disclosure</summary>
        <div className="mt-3 grid gap-3">
          <p className="text-sm">
            Review which pinned code could be sent to Meta. Saving consent keeps analysis disabled.
          </p>
          <label className="field-group">
            Source snapshot ID
            <input
              value={source}
              disabled={busy || snapshot.role === 'source'}
              maxLength={32}
              onChange={(event) => {
                setSource(event.target.value)
                setDisclosure(null)
              }}
            />
          </label>
          <label className="field-group">
            Target snapshot ID (optional)
            <input
              value={target}
              disabled={busy || snapshot.role === 'target'}
              maxLength={32}
              onChange={(event) => {
                setTarget(event.target.value)
                setDisclosure(null)
              }}
            />
          </label>
          <button
            type="button"
            className="button-secondary justify-self-start"
            disabled={
              busy || !/^[a-f0-9]{32}$/.test(source) || (!!target && !/^[a-f0-9]{32}$/.test(target))
            }
            onClick={() => void action('disclose')}
          >
            Review disclosure
          </button>
          {disclosure && (
            <>
              <p className="text-sm break-all">
                Provider: {disclosure.provider_endpoint}. {disclosure.transmission}
              </p>
              {disclosure.snapshots.map((item) => (
                <p className="text-sm break-all" key={item.id}>
                  {item.role}: {item.repository} at {item.sha}, root {item.root || '/'}.
                </p>
              ))}
              <label className="flex items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  className="project-check mt-1"
                  checked={accepted}
                  disabled={busy}
                  onChange={(event) => setAccepted(event.target.checked)}
                />
                I consent to this disclosure for these exact snapshots.
              </label>
              <button
                type="button"
                className="button-secondary justify-self-start"
                disabled={busy}
                onClick={() => void action('consent')}
              >
                Save consent decision
              </button>
              <p className="field-help" role="status">
                Consent {disclosure.consented ? 'saved' : 'absent'}. Analysis disabled.
              </p>
            </>
          )}
        </div>
      </details>
      {busy && (
        <p role="status" className="field-help">
          Checking pinned snapshot access…
        </p>
      )}
      {error && <ErrorState message={error} />}
    </div>
  )
}
