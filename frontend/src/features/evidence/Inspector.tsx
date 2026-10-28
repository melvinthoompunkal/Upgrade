import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { X, DownloadSimple, FileCode, GitBranch, CheckCircle } from '@phosphor-icons/react'
import {
  api,
  artifactUrl,
  shortSha,
  type ArtifactText,
  type ReferenceDetail,
  type Run,
  type Ref,
} from '../../api/client'
import { ErrorState, Notice, Skeleton } from '../../components/ui'

export function referenceId(run: Run, ref: Ref) {
  return run.references?.find(
    (item) =>
      item.ref.repo_id === ref.repo_id &&
      item.ref.commit_sha === ref.commit_sha &&
      item.ref.path === ref.path &&
      item.ref.start_line === ref.start_line &&
      item.ref.end_line === ref.end_line &&
      item.ref.symbol === ref.symbol,
  )?.id
}

export function Citation({ run, reference, label }: { run: Run; reference: Ref; label?: string }) {
  const [params, setParams] = useSearchParams()
  const id = referenceId(run, reference)
  if (!id)
    return <span className="unavailable-reference">{label ?? reference.path} (unavailable)</span>
  return (
    <button
      className="citation"
      onClick={() => {
        const next = new URLSearchParams(params)
        next.delete('artifact')
        next.set('ref', id)
        setParams(next)
      }}
    >
      <FileCode size={14} />
      <span>{label ?? `${reference.path}:${reference.start_line}–${reference.end_line}`}</span>
    </button>
  )
}

export function InspectArtifact({
  artifactId,
  children,
}: {
  artifactId: string
  children: React.ReactNode
}) {
  const [params, setParams] = useSearchParams()
  return (
    <button
      className="text-action"
      onClick={() => {
        const next = new URLSearchParams(params)
        next.delete('ref')
        next.set('artifact', artifactId)
        setParams(next)
      }}
    >
      {children}
    </button>
  )
}

export function Inspector({ run }: { run: Run }) {
  const [params, setParams] = useSearchParams()
  const refId = params.get('ref')
  const artifactId = params.get('artifact')
  const dialog = useRef<HTMLDialogElement>(null)
  const origin = useRef<HTMLElement | null>(null)
  const [reference, setReference] = useState<ReferenceDetail | null>(null)
  const [artifact, setArtifact] = useState<ArtifactText | null>(null)
  const [error, setError] = useState('')
  const selectedArtifact = run.artifacts?.find((item) => item.id === artifactId)
  const opened = !!(refId || artifactId)
  useEffect(() => {
    if (!opened || !dialog.current) return
    origin.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog.current.showModal()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.current?.close()
      document.body.style.overflow = previousOverflow
      origin.current?.focus()
    }
  }, [opened])
  useEffect(() => {
    if (!opened) return
    const controller = new AbortController()
    setError('')
    setReference(null)
    setArtifact(null)
    if (refId)
      api<ReferenceDetail>(`/runs/${run.id}/references/${refId}`, { signal: controller.signal })
        .then(setReference)
        .catch((reason: Error) => {
          if (!controller.signal.aborted) setError(reason.message)
        })
    else if (artifactId)
      api<ArtifactText>(`/runs/${run.id}/artifacts/${artifactId}/text`, {
        signal: controller.signal,
      })
        .then(setArtifact)
        .catch((reason: Error) => {
          if (!controller.signal.aborted) setError(reason.message)
        })
    return () => controller.abort()
  }, [opened, run.id, refId, artifactId])
  function close() {
    const next = new URLSearchParams(params)
    next.delete('ref')
    next.delete('artifact')
    setParams(next)
  }
  if (!opened) return null
  const title = refId ? 'Evidence inspector' : 'Artifact preview'
  return (
    <dialog
      ref={dialog}
      className="inspector"
      aria-labelledby="inspector-title"
      onCancel={(event) => {
        event.preventDefault()
        close()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <motion.div
        className="inspector-content"
        initial={{ x: 28, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
      >
        <div className="inspector-header">
          <div>
            <span className="text-muted text-xs">Run {run.id.slice(-8)}</span>
            <h2 id="inspector-title">{title}</h2>
          </div>
          <button className="icon-button" aria-label="Close inspector" onClick={close}>
            <X size={21} />
          </button>
        </div>
        <div className="inspector-body">
          {error ? (
            <ErrorState message={error} />
          ) : reference ? (
            <>
              <span className="badge">
                {reference.ref.repo_id === 'source' ? 'Source' : 'Target'} / {reference.category}
              </span>
              <h3 className="inspector-path">{reference.ref.path}</h3>
              <div className="reference-meta">
                <GitBranch size={14} />
                <code title={reference.ref.commit_sha}>{shortSha(reference.ref.commit_sha)}</code>
                <span>
                  Lines {reference.ref.start_line}–{reference.ref.end_line}
                </span>
              </div>
              {reference.ref.symbol && (
                <p className="font-mono text-xs my-4">{reference.ref.symbol}</p>
              )}
              {reference.text !== null && reference.text !== undefined ? (
                <>
                  <div className="code-label">
                    <FileCode size={15} />
                    Stored, read-backed excerpt
                  </div>
                  <div className="code-window" tabIndex={0} aria-label="Code excerpt">
                    <pre>
                      {reference.text
                        .replace(/\n$/, '')
                        .split('\n')
                        .map((line, index) => (
                          <div className="code-line" key={index}>
                            <span aria-hidden="true">{reference.ref.start_line + index}</span>
                            <code>{line || ' '}</code>
                          </div>
                        ))}
                    </pre>
                  </div>
                  <div className="excerpt-integrity">
                    <CheckCircle size={16} />
                    <span>Excerpt hash checked against the stored evidence.</span>
                  </div>
                  <details className="mt-5">
                    <summary>Provenance</summary>
                    <dl className="metadata-list mt-4">
                      <div>
                        <dt>Commit</dt>
                        <dd className="font-mono break-all">{reference.ref.commit_sha}</dd>
                      </div>
                      <div>
                        <dt>SHA-256</dt>
                        <dd className="font-mono break-all">{reference.sha256}</dd>
                      </div>
                    </dl>
                  </details>
                </>
              ) : (
                <Notice>
                  This reference has no stored excerpt available. Inspect the published evidence and
                  coverage reports for context.
                </Notice>
              )}
              <div className="mt-6">
                <Notice>
                  {reference.category === 'source tests'
                    ? 'Source tests are code references. They were not executed by this analysis.'
                    : 'This is bounded static evidence. It does not establish a runtime call graph or exhaustive coverage.'}
                </Notice>
              </div>
            </>
          ) : artifact ? (
            <>
              <h3 className="break-all">{selectedArtifact?.label ?? 'Published artifact'}</h3>
              <p className="text-muted text-sm my-3">
                Published content with local host paths and command arguments omitted.
              </p>
              <div className="code-window artifact-code" tabIndex={0}>
                <pre>{artifact.text}</pre>
              </div>
              {artifact.truncated && (
                <Notice>
                  Preview limited to 100,000 characters. Download the artifact for the full export.
                </Notice>
              )}
              {selectedArtifact && (
                <a
                  className="button-secondary mt-5"
                  href={artifactUrl(run.id, selectedArtifact.id)}
                  download={selectedArtifact.label.split('/').at(-1)}
                >
                  <DownloadSimple size={17} />
                  Download artifact
                </a>
              )}
            </>
          ) : (
            <Skeleton rows={8} />
          )}
        </div>
        <div className="inspector-footer">
          <button className="button-secondary" onClick={close}>
            Back to {refId ? 'analysis' : 'run'}
          </button>
        </div>
      </motion.div>
    </dialog>
  )
}
