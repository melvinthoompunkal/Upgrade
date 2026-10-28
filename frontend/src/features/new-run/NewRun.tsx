import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CaretDown,
  GitBranch,
  GithubLogo,
  MagnifyingGlass,
  ShieldCheck,
  Sparkle,
  Stack,
  Target as TargetIcon,
} from '@phosphor-icons/react'
import { createRun, formatDate, shortSha, type RunRequest } from '../../api/client'
import { useWorkspace } from '../../app/workspace-context'
import { ErrorState, Notice, RunBadge } from '../../components/ui'

const SCRAPY_URL = 'https://github.com/scrapy/scrapy'
const SCRAPY_SHA = '5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac'

export function NewRun() {
  const { capabilities, targets, history, error, refresh, loading, session } = useWorkspace()
  const navigate = useNavigate()
  const draftKey = `upgrade-run-draft:${session?.user?.id ?? 'local'}`
  const [draft] = useState<Record<string, string>>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(draftKey) ?? '{}')
      return value && typeof value === 'object' ? value : {}
    } catch {
      return {}
    }
  })
  const [source, setSource] = useState(typeof draft.source === 'string' ? draft.source : '')
  const [sha, setSha] = useState(typeof draft.sha === 'string' ? draft.sha : '')
  const [goal, setGoal] = useState(typeof draft.goal === 'string' ? draft.goal : '')
  const [targetId, setTargetId] = useState('crawler-demo')
  const [kind, setKind] = useState<'fit_select' | 'source_trace'>('fit_select')
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const pending = useRef(false)
  const submission = useRef({ body: '', key: '' })
  const target = targets.find((item) => item.id === targetId)
  const recent = history?.items.find((run) => run.kind === 'fit_select') ?? history?.items[0]
  const canRun =
    capabilities?.model_configured &&
    capabilities.worker_available &&
    (kind === 'source_trace' || capabilities.image_receipt_present)

  useEffect(() => {
    try {
      sessionStorage.setItem(draftKey, JSON.stringify({ source, sha, goal }))
    } catch {
      /* Browser storage may be disabled. */
    }
  }, [draftKey, source, sha, goal])

  function useExample() {
    setSource(SCRAPY_URL)
    setSha(SCRAPY_SHA)
    if (!goal) setGoal('Improve recovery from temporary HTTP errors in my crawler.')
    setErrors({})
    setSubmitError('')
  }
  function changeSource(value: string) {
    setSource(value)
    if (value.trim() !== source.trim()) {
      setSha('')
      if (value.trim()) setOptionsOpen(true)
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (pending.current) return
    const next: Record<string, string> = {}
    if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?$/.test(source.trim()))
      next.source = 'Enter a public GitHub repository URL, without credentials or query parameters.'
    if (!/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(sha.trim()))
      next.sha = 'Enter the full commit SHA for this repository, or use the Scrapy example.'
    if (!goal.trim() || goal.trim().length > 2000)
      next.goal = 'Describe your goal in 1–2,000 characters.'
    if (!targets.some((item) => item.id === targetId))
      next.target = 'Choose an available registered target.'
    setErrors(next)
    if (Object.keys(next).length) {
      if (next.sha) setOptionsOpen(true)
      requestAnimationFrame(() => document.getElementById(`field-${Object.keys(next)[0]}`)?.focus())
      return
    }
    const request: RunRequest = {
      kind,
      source_url: source.trim(),
      source_sha: sha.trim(),
      target: 'crawler-demo',
      goal: goal.trim(),
    }
    const body = JSON.stringify(request)
    if (submission.current.body !== body) submission.current = { body, key: crypto.randomUUID() }
    pending.current = true
    setSubmitting(true)
    setSubmitError('')
    try {
      const run = await createRun(request, submission.current.key)
      try {
        sessionStorage.removeItem(draftKey)
      } catch {
        /* Draft storage is optional. */
      }
      refresh()
      navigate(`/runs/${run.id}/overview`)
    } catch (reason) {
      setSubmitError((reason as Error).message)
    } finally {
      pending.current = false
      setSubmitting(false)
    }
  }

  return (
    <div className="page new-run-page">
      <div className="page-intro">
        <div className="intro-kicker">
          <span className="mini-rule" />A better starting point
        </div>
        <h1>
          Great ideas deserve
          <br />a place in your code.
        </h1>
        <p>
          Find a useful feature in another repository.
          <br className="hidden sm:block" /> Understand why it fits yours, with evidence at every
          step.
        </p>
      </div>
      {error && (
        <div className="mb-6">
          <ErrorState message={error} retry={refresh} />
        </div>
      )}
      <div className="new-run-layout">
        <form className="run-form surface" onSubmit={submit} noValidate>
          <div className="form-heading">
            <div>
              <h2>Start a new analysis</h2>
              <p>A source, a target, and something to improve.</p>
            </div>
            <span className="badge">Analysis only</span>
          </div>
          <div className="form-body">
            <div className="repository-pair">
              <div className="field-group">
                <label htmlFor="field-source">
                  <span className="field-step">1</span>Source repository
                </label>
                <div className={`input-with-icon ${errors.source ? 'input-invalid' : ''}`}>
                  <GithubLogo size={20} />
                  <input
                    id="field-source"
                    type="url"
                    placeholder="https://github.com/owner/repo"
                    value={source}
                    onChange={(event) => changeSource(event.target.value)}
                    aria-invalid={!!errors.source}
                    aria-describedby="source-help source-error"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
                <div id="source-help" className="field-help">
                  A public repository to learn from.
                </div>
                {errors.source && (
                  <p id="source-error" className="field-error">
                    {errors.source}
                  </p>
                )}
                <button type="button" className="example-button" onClick={useExample}>
                  <Sparkle size={14} />
                  Use Scrapy example
                  <ArrowUpRight size={12} />
                </button>
              </div>
              <span className="pair-arrow" aria-hidden="true">
                <ArrowRight size={19} />
              </span>
              <div className="field-group">
                <label htmlFor="field-target">
                  <span className="field-step">2</span>Target project
                </label>
                <div className="input-with-icon">
                  <TargetIcon size={20} />
                  <select
                    id="field-target"
                    value={targetId}
                    onChange={(event) => setTargetId(event.target.value)}
                    aria-describedby="target-help"
                  >
                    {targets.length ? (
                      targets.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))
                    ) : (
                      <option value="crawler-demo">
                        {loading ? 'Loading targets…' : 'Targets unavailable'}
                      </option>
                    )}
                  </select>
                  <CaretDown size={14} />
                </div>
                <div id="target-help" className="field-help">
                  {target?.stack ?? 'Registered local projects'}
                </div>
                <span className="target-tag">
                  <GitBranch size={13} />
                  {target?.head ? shortSha(target.head) : 'Committed inputs'}
                </span>
                {errors.target && <p className="field-error">{errors.target}</p>}
              </div>
            </div>
            <div className="field-group mt-7">
              <label htmlFor="field-goal">
                <span className="field-step">3</span>What would you like to improve?
              </label>
              <textarea
                id="field-goal"
                rows={4}
                placeholder="For example, help my crawler recover from temporary HTTP errors…"
                value={goal}
                maxLength={2000}
                onChange={(event) => setGoal(event.target.value)}
                aria-invalid={!!errors.goal}
                aria-describedby="goal-help goal-error"
              />
              <div className="flex justify-between gap-4">
                <span id="goal-help" className="field-help">
                  Describe the behavior you want, in your own words.
                </span>
                <span className="character-count">{goal.length.toLocaleString()} / 2,000</span>
              </div>
              {errors.goal && (
                <p id="goal-error" className="field-error">
                  {errors.goal}
                </p>
              )}
            </div>
            <div className="analysis-options">
              <button
                type="button"
                className="options-toggle"
                aria-expanded={optionsOpen}
                aria-controls="analysis-options"
                onClick={() => setOptionsOpen(!optionsOpen)}
              >
                <CaretDown size={15} className={optionsOpen ? 'rotate-180' : ''} />
                Analysis options<span>{sha ? 'Source pinned' : 'Pin a source commit'}</span>
              </button>
              {optionsOpen && (
                <div id="analysis-options" className="options-content">
                  <div className="field-group">
                    <label htmlFor="field-sha">Source commit SHA</label>
                    <input
                      id="field-sha"
                      className="font-mono text-xs"
                      value={sha}
                      onChange={(event) => setSha(event.target.value)}
                      placeholder="Full immutable commit SHA"
                      aria-invalid={!!errors.sha}
                      aria-describedby="sha-help"
                      spellCheck={false}
                    />
                    <p id="sha-help" className="field-help">
                      Changing the repository clears this commit. The Scrapy example fills its
                      reviewed URL and SHA together.
                    </p>
                    {errors.sha && <p className="field-error">{errors.sha}</p>}
                  </div>
                  <fieldset>
                    <legend>Analysis mode</legend>
                    <div className="mode-options">
                      <label>
                        <input
                          type="radio"
                          name="mode"
                          checked={kind === 'fit_select'}
                          onChange={() => setKind('fit_select')}
                        />
                        <span>
                          <strong>Find a suitable feature</strong>
                          <small>Trace, profile, test, and assess fit.</small>
                        </span>
                      </label>
                      <label>
                        <input
                          type="radio"
                          name="mode"
                          checked={kind === 'source_trace'}
                          onChange={() => setKind('source_trace')}
                        />
                        <span>
                          <strong>Trace source only</strong>
                          <small>Code references and source notices.</small>
                        </span>
                      </label>
                    </div>
                  </fieldset>
                  <p className="field-help">
                    Current scope: one bounded retry slice. Custom source inputs can be traced;
                    selection requires the reviewed source policy.
                  </p>
                </div>
              )}
            </div>
            {target && (
              <div className="committed-note">
                <GitBranch size={16} />
                <p>
                  Uses the target’s committed HEAD.{' '}
                  {target.dirty
                    ? 'Local changes were detected and will be excluded.'
                    : target.dirty === false
                      ? 'No local target changes detected.'
                      : 'Local change state is unavailable.'}{' '}
                  <span>
                    Checked {formatDate(target.checked_at)}; inputs are pinned again when the run
                    starts.
                  </span>
                </p>
              </div>
            )}
            {submitError && <Notice tone="error">{submitError}</Notice>}
          </div>
          <div className="form-footer">
            <div className="readiness-inline">
              <ShieldCheck size={17} />
              <span>
                {capabilities?.model_configured ? 'Meta configured' : 'Meta setup required'}
                <span className="inline-separator" />
                {capabilities?.image_receipt_present
                  ? 'Image receipt found'
                  : 'Runner setup required'}
              </span>
            </div>
            <button type="submit" className="button-primary" disabled={submitting || !canRun}>
              {submitting
                ? 'Starting analysis…'
                : kind === 'source_trace'
                  ? 'Trace source'
                  : 'Find a suitable feature'}
              <ArrowRight size={18} />
            </button>
            <p>
              {kind === 'fit_select'
                ? 'Runs model analysis and isolated Docker tests.'
                : 'Runs read-only Meta analysis.'}{' '}
              No code changes are made.
            </p>
            {!canRun && !loading && (
              <Link className="text-action" to="/system">
                Review setup in System checks
                <ArrowUpRight size={14} />
              </Link>
            )}
          </div>
        </form>
        <aside className="analysis-guide" aria-label="How analysis works">
          <div className="guide-header">
            <span className="guide-symbol">
              <Stack size={25} />
            </span>
            <span>From source to understanding</span>
          </div>
          <h2>
            A good fit.
            <br /> With the receipts.
          </h2>
          <p>Go beyond a suggestion. See the code, the tradeoffs, and the tests behind it.</p>
          <div className="guide-timeline">
            <div>
              <span>
                <BookOpen size={19} />
              </span>
              <section>
                <h3>Trace the source</h3>
                <p>Follow a feature to its implementation, tests, and dependencies.</p>
              </section>
            </div>
            <div>
              <span>
                <TargetIcon size={19} />
              </span>
              <section>
                <h3>Understand your target</h3>
                <p>Check the architecture and observe existing behavior in isolated tests.</p>
              </section>
            </div>
            <div>
              <span>
                <MagnifyingGlass size={19} />
              </span>
              <section>
                <h3>Find the right fit</h3>
                <p>Inspect the score, policy gates, and reasons behind the decision.</p>
              </section>
            </div>
          </div>
          <div className="guide-assurance">
            <Check size={15} />
            <p>Your original project stays intact. Every conclusion links back to evidence.</p>
          </div>
        </aside>
      </div>
      {recent && (
        <section className="continue-run">
          <div className="continue-icon">
            <GitBranch size={21} />
          </div>
          <div className="min-w-0">
            <span className="text-muted text-xs">Pick up where you left off</span>
            <Link to={`/runs/${recent.id}/overview`}>{recent.title}</Link>
          </div>
          <RunBadge run={recent} />
          <Link
            className="icon-button"
            aria-label="Open recent run"
            to={`/runs/${recent.id}/overview`}
          >
            <ArrowUpRight size={20} />
          </Link>
        </section>
      )}
    </div>
  )
}
