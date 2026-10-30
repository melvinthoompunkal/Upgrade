import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  ArrowClockwise,
  BracketsCurly,
  CheckCircle,
  Copy,
  Cube,
  GitBranch,
  TerminalWindow,
  FileCode,
} from '@phosphor-icons/react'
import { createRun, formatDate, workflowEnabled, type RunSummary } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { ErrorState, Notice, SectionHeading, Skeleton, StatusMark } from '../../components/ui'
import { DataLifecycle } from './DataLifecycle'
import { SupportMatrix } from './SupportMatrix'

const checks = [
  {
    kind: 'meta_smoke',
    title: 'Meta connectivity',
    description:
      'Validate structured output and a harmless tool round trip. This makes real model requests.',
    icon: BracketsCurly,
    action: 'Run Meta check',
  },
  {
    kind: 'pinned_baseline',
    title: 'Committed target baseline',
    description:
      'Pin committed HEAD, then run isolation, baseline, and the retry diagnostic in Docker.',
    icon: GitBranch,
    action: 'Test committed target',
  },
  {
    kind: 'local_baseline',
    title: 'Working-file baseline',
    description:
      'Run the same restricted suites against the local fixture files, including local changes.',
    icon: FileCode,
    action: 'Test local fixture files',
  },
] as const

export function SystemChecks() {
  const { capabilities, history, loading, error, refresh } = useWorkspace()
  const navigate = useNavigate()
  const [starting, setStarting] = useState<string | null>(null)
  const [runError, setRunError] = useState('')
  const pending = useRef(false)
  const submission = useRef({ kind: '', key: '' })
  const [copied, setCopied] = useState('')
  async function start(kind: (typeof checks)[number]['kind']) {
    if (pending.current) return
    pending.current = true
    setStarting(kind)
    setRunError('')
    if (submission.current.kind !== kind) submission.current = { kind, key: crypto.randomUUID() }
    try {
      const run = await createRun({ kind }, submission.current.key)
      refresh()
      navigate(`/runs/${run.id}/overview`)
    } catch (reason) {
      setRunError((reason as Error).message)
    } finally {
      pending.current = false
      setStarting(null)
    }
  }
  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(text)
    } catch {
      setRunError('Clipboard access is unavailable. Select and copy the setup command below.')
    }
  }
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>System checks</h1>
          <p>Review service availability and saved checks.</p>
        </div>
        <button className="button-secondary" onClick={refresh}>
          <ArrowClockwise size={17} />
          Refresh setup
        </button>
      </div>
      {error && <ErrorState message={error} retry={refresh} />}
      {loading ? (
        <Skeleton />
      ) : (
        <div className="setup-summary surface">
          <div>
            <StatusMark passed={capabilities?.model_configured ?? null} />
            <section>
              <h3>Meta configuration</h3>
              <p>
                {capabilities?.model_configured
                  ? `${capabilities.model_id} / Key present`
                  : 'Configure MODEL_API_KEY in backend/.env'}
              </p>
              <small>A configured key does not verify connectivity.</small>
            </section>
          </div>
          <div>
            <Cube size={21} />
            <section>
              <h3>Docker runner</h3>
              <p>
                {capabilities?.target_execution_supported === false
                  ? 'Target execution unavailable'
                  : capabilities?.image_receipt_present
                    ? 'Reviewed image receipt found'
                    : 'Prepared image receipt missing'}
              </p>
              <small>
                {capabilities?.target_execution_supported === false
                  ? 'Public code analysis is supported separately.'
                  : 'Run a baseline to check Docker and image validity.'}
              </small>
            </section>
          </div>
          <div>
            <StatusMark passed={capabilities?.worker_available ?? null} />
            <section>
              <h3>
                {capabilities?.deployment_mode === 'hosted' ? 'Analysis worker' : 'Local worker'}
              </h3>
              <p>
                {capabilities?.worker_available
                  ? 'Available / one workflow at a time'
                  : 'Unavailable / retry when the service is ready'}
              </p>
              <small>Queue limits are enforced before each submission.</small>
            </section>
          </div>
        </div>
      )}
      {capabilities && (
        <p className="footnote">
          Configuration observed {formatDate(capabilities.checked_at)}. No model request or Docker
          suite runs automatically.
        </p>
      )}
      <div className="mt-9">
        <SectionHeading title="Run a diagnostic">
          Each check creates a saved run with inspectable results.
        </SectionHeading>
      </div>
      {runError && (
        <div className="mb-4">
          <ErrorState message={runError} />
        </div>
      )}
      <div className="surface diagnostic-list">
        {checks.map((check) => {
          const previous: RunSummary | undefined = history?.items.find(
            (run) => run.kind === check.kind && run.status === 'finished',
          )
          return (
            <div className="diagnostic-row" key={check.kind}>
              <span className="diagnostic-icon">
                <check.icon size={24} />
              </span>
              <section>
                <h3>{check.title}</h3>
                <p>{check.description}</p>
                {previous ? (
                  <button
                    className="text-action mt-2"
                    onClick={() => navigate(`/runs/${previous.id}/overview`)}
                  >
                    Last recorded: {previous.outcome} / {formatDate(previous.created_at)}
                  </button>
                ) : (
                  <small>No completed check in the latest run history.</small>
                )}
              </section>
              <button
                className="button-secondary"
                onClick={() => start(check.kind)}
                disabled={
                  starting !== null ||
                  !capabilities?.worker_available ||
                  !workflowEnabled(capabilities, check.kind) ||
                  (check.kind === 'meta_smoke' &&
                    (!capabilities.model_configured || capabilities.diagnostics_allowed === false))
                }
              >
                {starting === check.kind ? 'Starting…' : check.action}
                <ArrowRight size={16} />
              </button>
            </div>
          )
        })}
      </div>
      {capabilities?.deployment_mode === 'hosted' ? (
        <Notice>
          Public code analysis is available for the approved workflows. Contact your service
          operator if a workflow is unavailable.
        </Notice>
      ) : (
        <div className="setup-instructions">
          <div>
            <TerminalWindow size={24} />
            <h2>Configure once, check when needed.</h2>
            <p>
              Keep credentials in your local backend environment. Build the reviewed Docker image
              before running target tests.
            </p>
            <Notice>
              A retry diagnostic can intentionally fail assertions to demonstrate missing behavior.
              Ordinary baseline failures are reported separately.
            </Notice>
          </div>
          <div className="surface p-6">
            <h3 className="mb-4">From the project root</h3>
            {[
              {
                label: 'Install backend dependencies',
                command: 'uv sync --project backend --locked',
              },
              {
                label: 'Create local configuration, if missing',
                command:
                  'if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }',
              },
              {
                label: 'Build the reviewed runner image',
                command: 'uv run --project backend --locked python scripts/build_runner.py',
              },
            ].map((item) => (
              <div className="setup-command" key={item.command}>
                <label>{item.label}</label>
                <div>
                  <code>{item.command}</code>
                  <button
                    className="icon-button"
                    aria-label={`Copy ${item.label.toLowerCase()}`}
                    onClick={() => copy(item.command)}
                  >
                    {copied === item.command ? <CheckCircle size={16} /> : <Copy size={16} />}
                  </button>
                </div>
              </div>
            ))}
            <p className="field-help">
              Only create backend/.env if it does not exist. Set MODEL_API_KEY locally, then restart
              the backend. Never paste a key into the browser.
            </p>
          </div>
        </div>
      )}
      <DataLifecycle />
      <SupportMatrix />
    </div>
  )
}
