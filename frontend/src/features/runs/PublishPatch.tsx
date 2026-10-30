import { useEffect, useRef, useState } from 'react'
import { GitPullRequest } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { api, type Publication, type PublishReview } from '../../api/client'
import { ErrorState, Notice, Skeleton } from '../../components/ui'

export function PublishPatch({ runId }: { runId: string }) {
  const [view, setView] = useState<PublishReview | null>(null)
  const [branch, setBranch] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const key = useRef(crypto.randomUUID())
  const heading = useRef<HTMLHeadingElement>(null)
  const endpoint = `/runs/${runId}/pull-request`
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    api<PublishReview>(endpoint, { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return
        setView(value)
        setBranch(value.publication?.base_branch ?? value.base_branch ?? '')
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message)
      })
    return () => controller.abort()
  }, [endpoint, attempt])

  async function publish() {
    if (!view || !reviewed || busy) return
    setBusy(true)
    setError('')
    try {
      const publication = await api<Publication>(endpoint, {
        method: 'POST',
        headers: { 'Idempotency-Key': key.current },
        body: JSON.stringify({
          repository_id: view.repository_id,
          base_branch: branch,
          patch_sha256: view.patch_sha256,
          notices_sha256: view.notices_sha256,
          reviewed: true,
        }),
      })
      setView({ ...view, publication })
      heading.current?.focus()
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Publishing failed. Check its saved status.',
      )
      // An interrupted response may still have created the PR. Read its durable status;
      // another write always requires the user's explicit retry.
      const latest = await api<PublishReview>(endpoint).catch(() => null)
      if (latest) setView(latest)
    } finally {
      setBusy(false)
    }
  }

  const receipt = view?.publication
  const done = receipt?.state === 'published'
  const stale = receipt?.state === 'stale'
  return (
    <section className="surface p-5 space-y-4" aria-label="Publish reviewed patch">
      <h3 ref={heading} tabIndex={-1} className="flex items-center gap-2">
        <GitPullRequest size={20} />
        {done ? 'Draft pull request created' : 'Publish reviewed patch'}
      </h3>
      {error && <ErrorState message={error} />}
      {!view ? (
        error ? (
          <button className="button-secondary" onClick={() => setAttempt(attempt + 1)}>
            Reload publishing status
          </button>
        ) : (
          <Skeleton rows={3} />
        )
      ) : (
        <>
          <p className="text-muted break-all">
            {view.target_origin} <span className="font-mono">@ {view.base_sha?.slice(0, 12)}</span>
          </p>
          {receipt?.pr_url && (
            <a className="text-action" href={receipt.pr_url} target="_blank" rel="noreferrer">
              Open pull request #{receipt.pr_number}
            </a>
          )}
          {stale ? (
            <Notice>
              The base branch moved. Revalidate this adaptation against the current base.
              {receipt.pr_url &&
                ' The existing pull request remains available for inspection.'}{' '}
              <Link className="text-action" to="/">
                Start a fresh adaptation
              </Link>
            </Notice>
          ) : done ? (
            <p role="status">The exact verified patch and source notices are saved on GitHub.</p>
          ) : !view.enabled ? (
            <Notice>{view.reason}</Notice>
          ) : !view.repository_id ? (
            <Notice>
              Sync your GitHub repositories to grant access to this target before publishing.{' '}
              <Link className="text-action" to="/repositories">
                Manage repositories
              </Link>
            </Notice>
          ) : (
            <form
              className="space-y-4 max-w-xl"
              onSubmit={(event) => {
                event.preventDefault()
                void publish()
              }}
            >
              <p className="text-muted">
                Create a unique branch and draft pull request with this exact patch. GitHub access
                and the verified base are checked again when you publish.
              </p>
              <label className="block space-y-2">
                <span>Base branch</span>
                <input
                  value={branch}
                  required
                  maxLength={200}
                  disabled={busy || !!receipt}
                  onChange={(event) => {
                    setBranch(event.target.value)
                    setReviewed(false)
                  }}
                />
              </label>
              <label className="flex gap-3 items-start">
                <input
                  type="checkbox"
                  className="project-check mt-0.5"
                  checked={reviewed}
                  disabled={busy}
                  onChange={(event) => setReviewed(event.target.checked)}
                />
                <span>I reviewed the production patch and source notices for this repository.</span>
              </label>
              {receipt?.state === 'retryable' && (
                <Notice>
                  Publishing was interrupted. Retry to recover the same branch and pull request.
                </Notice>
              )}
              <button className="button-primary" disabled={!reviewed || !branch || busy}>
                <GitPullRequest size={17} />
                {busy
                  ? 'Publishing reviewed patch…'
                  : receipt
                    ? 'Retry draft pull request'
                    : 'Create draft pull request'}
              </button>
              <p className="text-muted text-xs" role="status" aria-live="polite">
                {busy ? 'Checking access, base and saved publication status.' : view.reason}
              </p>
            </form>
          )}
          <details className="artifact-hash">
            <summary>Publication hashes</summary>
            <p>
              Patch: <code>{view.patch_sha256}</code>
            </p>
            <p>
              Notices: <code>{view.notices_sha256}</code>
            </p>
            {receipt && (
              <p>
                Branch: <code>{receipt.branch}</code>
              </p>
            )}
          </details>
        </>
      )}
    </section>
  )
}
