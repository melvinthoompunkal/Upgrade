import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { DownloadSimple, FileCode, GitDiff } from '@phosphor-icons/react'
import {
  api,
  artifactUrl,
  type ChangeReview,
  type ChangedFile,
  type DiffPage,
  type Run,
} from '../../api/client'
import { Empty, ErrorState, Notice, Skeleton } from '../../components/ui'
import { InspectArtifact } from '../evidence/Inspector'
import { AnalysisChanges } from './outputs'
import { PublishPatch } from './PublishPatch'
import './changes.css'

export function Changes({ run }: { run: Run }) {
  if (run.kind !== 'verified_adaptation') return <AnalysisChanges run={run} />
  if (run.status !== 'finished')
    return (
      <Empty title="Adaptation is in progress">
        Change review will appear when this run finishes.
      </Empty>
    )
  return <AdaptationChanges run={run} />
}

function AdaptationChanges({ run }: { run: Run }) {
  const [review, setReview] = useState<ChangeReview | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [params, setParams] = useSearchParams()
  const selected = params.get('file')
  const offsetParam = params.get('offset') ?? '0'
  const offset = /^\d{1,6}$/.test(offsetParam) ? Number(offsetParam) : -1
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    setReview(null)
    api<ChangeReview>(`/runs/${run.id}/changes`, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setReview(value)
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message)
      })
    return () => controller.abort()
  }, [run.id, attempt])

  if (error)
    return (
      <div className="space-y-4">
        <ErrorState message={error} />
        <button className="button-secondary" onClick={() => setAttempt(attempt + 1)}>
          Retry change review
        </button>
        <Link className="text-action ml-4" to={`/runs/${run.id}/exports`}>
          Check exports
        </Link>
      </div>
    )
  if (!review) return <Skeleton rows={8} />

  function navigate(file: string, pageOffset = 0) {
    const next = new URLSearchParams(params)
    next.set('file', file)
    next.delete('offset')
    if (pageOffset) next.set('offset', String(pageOffset))
    setParams(next)
  }
  const file = review.files.find((item) => item.id === selected)
  const title = {
    verified: 'Verified production patch',
    unverified: 'Unverified production patch',
    already_present: 'This behavior is already present',
  }[review.verdict]
  return (
    <div className="change-review space-y-6">
      <section className="surface p-5 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <h2 className="flex items-center gap-2">
              <GitDiff size={23} />
              {title}
            </h2>
            <p className="text-muted">
              {review.verdict === 'verified'
                ? 'The saved patch passed frozen acceptance and native regression checks.'
                : review.verdict === 'already_present'
                  ? 'Acceptance passed on the original target. No production patch was needed.'
                  : 'This run did not establish a verified adaptation. Inspect the retained evidence before using a patch.'}
            </p>
          </div>
          {review.patch?.available && (
            <a className="button-primary" href={artifactUrl(run.id, review.patch.id)} download>
              <DownloadSimple size={17} />
              Download exact patch
            </a>
          )}
        </div>
        <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 border-t border-line pt-4">
          <div>
            <dt className="text-muted text-xs">Protected tests</dt>
            <dd className="mt-1">
              {review.protected_tests_unchanged
                ? 'Unchanged and hash checked'
                : 'Preservation not established'}
            </dd>
          </div>
          <div>
            <dt className="text-muted text-xs">Original target</dt>
            <dd className="mt-1">
              {review.original_target_unchanged ? 'Preserved' : 'Preservation not established'}
            </dd>
          </div>
          <div>
            <dt className="text-muted text-xs">Production repairs</dt>
            <dd className="mt-1 font-mono">{review.repair_count}</dd>
          </div>
        </dl>
        {review.patch && (
          <details className="artifact-hash">
            <summary>Exact patch SHA-256</summary>
            <code>{review.patch.sha256}</code>
          </details>
        )}
        <div className="flex flex-wrap gap-5">
          <Link className="text-action" to={`/runs/${run.id}/tests`}>
            Test evidence
          </Link>
          <Link className="text-action" to={`/runs/${run.id}/evidence`}>
            Source and target evidence
          </Link>
          <Link className="text-action" to={`/runs/${run.id}/exports`}>
            All exports and repair history
          </Link>
          {review.mapping?.available && (
            <InspectArtifact artifactId={review.mapping.id}>File hash mapping</InspectArtifact>
          )}
        </div>
      </section>

      {review.files.length ? (
        <section className="change-layout surface" aria-label="Production changes">
          <nav className="change-files" aria-label="Changed files">
            <p className="text-muted text-xs mb-3">
              {review.files.length} changed {review.files.length === 1 ? 'file' : 'files'}
            </p>
            {review.files.map((item) => (
              <button
                key={item.id}
                className="change-file"
                aria-current={selected === item.id ? 'true' : undefined}
                onClick={() => navigate(item.id)}
              >
                <span className="flex gap-2 items-start">
                  <FileCode size={17} className="shrink-0 mt-0.5" />
                  <span className="break-all">{item.path}</span>
                </span>
                <span className="flex justify-between mt-2 text-xs">
                  <span>{item.status}</span>
                  <span className="font-mono">
                    +{item.additions} / -{item.deletions}
                  </span>
                </span>
              </button>
            ))}
          </nav>
          <div className="min-w-0 p-4">
            {file ? (
              <FileDiff
                key={`${file.id}:${offset}`}
                runId={run.id}
                file={file}
                patchSha={review.patch!.sha256}
                offset={offset}
                navigate={navigate}
              />
            ) : selected ? (
              <ErrorState message="This file is not in the saved patch. Choose a changed file." />
            ) : (
              <Empty title="Choose a file to review">
                Diffs load on demand. Opening this list makes no model calls.
              </Empty>
            )}
          </div>
        </section>
      ) : (
        <Empty
          title={
            review.verdict === 'already_present'
              ? 'No patch needed'
              : 'No production patch was published'
          }
        >
          {review.verdict === 'already_present'
            ? 'Review the original acceptance evidence above.'
            : 'Available planning, policy and failure evidence is saved in exports.'}
        </Empty>
      )}

      <section className="surface p-5 space-y-4" aria-label="Verification and notices">
        <h3>Verification and source notices</h3>
        <div className="divide-y divide-line">
          {review.evidence.map((item) => (
            <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <span className="font-mono text-xs break-all">{item.label}</span>
              {item.available ? (
                <div className="flex gap-4 shrink-0">
                  <InspectArtifact artifactId={item.id}>Inspect</InspectArtifact>
                  <a
                    className="text-action"
                    href={artifactUrl(run.id, item.id)}
                    download
                    aria-label={`Download ${item.label}`}
                  >
                    Download
                  </a>
                </div>
              ) : (
                <span className="text-muted text-xs">Missing or changed</span>
              )}
            </div>
          ))}
        </div>
        {!review.publishing_enabled && (
          <Notice>
            Pull request publishing is disabled. Download the patch and notices for review.
          </Notice>
        )}
      </section>
      {review.publishing_enabled && <PublishPatch key={run.id} runId={run.id} />}
    </div>
  )
}

function FileDiff({
  runId,
  file,
  patchSha,
  offset,
  navigate,
}: {
  runId: string
  file: ChangedFile
  patchSha: string
  offset: number
  navigate: (file: string, offset?: number) => void
}) {
  const heading = useRef<HTMLHeadingElement>(null)
  const [page, setPage] = useState<DiffPage | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (page) heading.current?.focus({ preventScroll: true })
  }, [page])
  useEffect(() => {
    if (offset < 0 || offset > 100_000) {
      setError('Invalid diff page. Return to the first page.')
      return
    }
    const controller = new AbortController()
    setError('')
    setPage(null)
    api<DiffPage>(`/runs/${runId}/changes/files/${file.id}?offset=${offset}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (controller.signal.aborted) return
        if (value.patch_sha256 !== patchSha) {
          setError('The patch identity changed. Reopen change review.')
          return
        }
        setPage(value)
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message)
      })
    return () => controller.abort()
  }, [runId, file.id, offset, patchSha, attempt])
  return (
    <section aria-label={`Diff for ${file.path}`} className="space-y-4">
      <h3 ref={heading} tabIndex={-1} className="font-mono break-all">
        {file.path}
      </h3>
      <details className="artifact-hash">
        <summary>Before and after SHA-256</summary>
        <p>
          Before: <code>{file.before_sha256 ?? 'New file'}</code>
        </p>
        <p>
          After: <code>{file.after_sha256}</code>
        </p>
      </details>
      {error ? (
        <>
          <ErrorState message={error} />
          <button className="text-action" onClick={() => setAttempt(attempt + 1)}>
            Retry file
          </button>
        </>
      ) : !page ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <p className="text-muted text-xs" role="status">
            Diff lines {page.offset + 1}–{page.offset + page.lines.length} of {page.total_lines}
          </p>
          {page.lines.some((line) => line.truncated) && (
            <Notice>
              Long lines are shortened in this view. Download the exact patch for complete text.
            </Notice>
          )}
          <div className="change-diff" role="region" aria-label="Unified diff" tabIndex={0}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Old</th>
                  <th scope="col">New</th>
                  <th scope="col">Change</th>
                </tr>
              </thead>
              <tbody>
                {page.lines.map((line, index) => (
                  <tr key={page.offset + index} className={`diff-${line.kind}`}>
                    <td>{line.old_line}</td>
                    <td>{line.new_line}</td>
                    <td>
                      <pre>
                        {line.text}
                        {line.truncated ? ' [line shortened]' : ''}
                      </pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          className="button-secondary"
          disabled={offset === 0}
          onClick={() => navigate(file.id)}
        >
          First diff page
        </button>
        {page?.next_offset != null && (
          <button className="button-secondary" onClick={() => navigate(file.id, page.next_offset!)}>
            Next diff page
          </button>
        )}
      </div>
    </section>
  )
}
