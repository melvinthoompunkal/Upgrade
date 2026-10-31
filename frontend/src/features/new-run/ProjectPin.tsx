import { useRef, useState } from 'react'
import { GitBranch, GithubLogo } from '@phosphor-icons/react'
import { api, shortSha, type ProjectSelection } from '../../api/client'
import { Notice } from '../../components/ui'
import { useWorkspace } from '../../app/workspace-context'
import { RevisionInput, type RevisionSelection } from './RevisionInput'

export function ProjectPin({
  role,
  onPin,
  structureOnly = false,
  initialProfile = 'python-evidence-v1',
}: {
  role: 'source' | 'target'
  onPin: (project: ProjectSelection | null) => void
  structureOnly?: boolean
  initialProfile?: 'python-evidence-v1' | 'repository-text-v1'
}) {
  const { capabilities, session } = useWorkspace()
  const [profile, setProfile] = useState(initialProfile)
  const [url, setUrl] = useState('')
  const [revision, setRevision] = useState<RevisionSelection>({ kind: 'latest' })
  const [root, setRoot] = useState('')
  const [project, setProject] = useState<ProjectSelection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)

  function edit(update: () => void) {
    generation.current++
    update()
    setProject(null)
    onPin(null)
    setError('')
  }
  async function pin() {
    if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(url.trim())) {
      setError('Enter a public GitHub repository URL.')
      return
    }
    const sha = revision.kind === 'latest' ? '' : revision.sha.trim()
    if ((revision.kind === 'branch' || revision.kind === 'tag') && !sha) {
      setError(`Resolve the selected ${revision.kind} before inspecting the project.`)
      return
    }
    if (sha && !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(sha)) {
      setError('Enter the full lowercase commit SHA, or leave it blank for the latest snapshot.')
      return
    }
    const current = ++generation.current
    setBusy(true)
    setError('')
    try {
      const result = await api<ProjectSelection>('/projects', {
        method: 'POST',
        body: JSON.stringify({
          url: url.trim(),
          commit_sha: sha || undefined,
          project_root: root.trim(),
          role,
          analysis_profile:
            !structureOnly && profile === 'repository-text-v1' ? profile : undefined,
        }),
      })
      if (generation.current !== current) return
      setProject(result)
      onPin(result)
    } catch (reason) {
      if (generation.current === current) setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="border-t border-line py-6" aria-label={`${role} project`}>
      <h3 className="mb-4 capitalize">{role} project</h3>
      {!structureOnly &&
        session?.mode === 'local' &&
        capabilities?.deployment_mode !== 'hosted' && (
          <div className="field-group mb-4">
            <label htmlFor={`${role}-profile`}>Analysis profile</label>
            <select
              id={`${role}-profile`}
              value={profile}
              onChange={(event) => edit(() => setProfile(event.target.value as typeof profile))}
            >
              <option value="python-evidence-v1">Reviewed Python analysis</option>
              <option value="repository-text-v1">All languages — text analysis preview</option>
            </select>
            <p className="field-help">
              The text preview reads source without a manifest, parser or runner. Live semantic
              evaluation is pending.
            </p>
          </div>
        )}
      <div className="field-group">
        <label htmlFor={`${role}-url`}>
          {role === 'source' ? 'Source' : 'Target'} public repository
        </label>
        <div className="input-with-icon">
          <GithubLogo size={20} />
          <input
            id={`${role}-url`}
            type="url"
            value={url}
            placeholder="https://github.com/owner/repository"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) =>
              edit(() => {
                setUrl(event.target.value)
                setRevision((previous) =>
                  previous.kind === 'latest' ? previous : { ...previous, sha: '' },
                )
              })
            }
          />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <RevisionInput
          label={role === 'source' ? 'Source' : 'Target'}
          value={revision}
          repository={{ url: url.trim() }}
          onChange={(value) => edit(() => setRevision(value))}
          allowLatest
        />
        <div className="field-group">
          <label htmlFor={`${role}-root`}>
            {role === 'source' ? 'Source' : 'Target'} project root
          </label>
          <input
            id={`${role}-root`}
            value={root}
            placeholder="Repository root (leave empty)"
            aria-describedby={`${role}-root-help`}
            spellCheck={false}
            onChange={(event) => edit(() => setRoot(event.target.value))}
          />
          <p className="field-help" id={`${role}-root-help`}>
            {structureOnly
              ? 'For Python structure, select the directory containing pyproject.toml.'
              : 'Optional: narrow analysis to a directory. This is the hard boundary for source reads.'}
          </p>
        </div>
      </div>
      <button type="button" className="button-secondary mt-4" disabled={busy} onClick={pin}>
        {busy ? 'Inspecting public snapshot…' : `Inspect ${role} project`}
      </button>
      {busy && (
        <div
          role="status"
          className="skeleton mt-4 h-12 w-full"
          aria-label={`Inspecting ${role} project`}
        />
      )}
      {error && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {project && (
        <div className="mt-5 space-y-3" role="status">
          <p className="flex flex-wrap items-center gap-2">
            <GitBranch size={16} />
            <strong>{project.support.name ?? 'Project inspected'}</strong>
            <span className="font-mono text-xs" title={project.snapshot.commit_sha}>
              Snapshot {shortSha(project.snapshot.commit_sha)}
            </span>
            <span className="badge">
              {project.support.capability === 'browse_index'
                ? 'No analysis available'
                : 'Ready for feature discovery'}
            </span>
          </p>
          <p className="field-help">
            {'readable_files' in project.support
              ? `${project.support.readable_files} readable files; ${project.support.readable_source_files} source files. Languages: ${Object.keys(project.support.detected_languages).join(', ') || 'documentation only'}.`
              : `${project.support.python_files} Python files.`}{' '}
            Project root: {project.requested.project_root || '/'}.{' '}
            {project.support.capability === 'evidence_analysis' &&
              'Ready for feature discovery and comparison.'}
          </p>
          {'inventory_partial' in project.support && project.support.inventory_partial && (
            <Notice>
              Partial inventory: {project.support.inventory_limits.join(', ').replaceAll('_', ' ')}.
              Narrow the project root for a new inspection.
            </Notice>
          )}
          {project.support.capability === 'browse_index' &&
            (project.support.reasons ?? []).length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {(project.support.reasons ?? []).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          {project.root_choices.some((choice) => choice !== project.requested.project_root) && (
            <div className="field-group">
              <label htmlFor={`${role}-roots`}>Suggested directory scopes</label>
              <select
                id={`${role}-roots`}
                value=""
                onChange={(event) => {
                  if (event.target.value)
                    edit(() => setRoot(event.target.value === '/' ? '' : event.target.value))
                }}
              >
                <option value="">Choose a root, then inspect again</option>
                {project.root_choices.map((choice) => (
                  <option key={choice} value={choice || '/'}>
                    {choice || '/'}
                  </option>
                ))}
              </select>
            </div>
          )}
          <details className="field-help">
            <summary>Test and implementation support</summary>
            <p className="mt-2">
              {project.support.execution_supported
                ? 'Native offline tests supported.'
                : 'Native tests unsupported.'}{' '}
              This does not block feature analysis.
            </p>
            {!!project.support.execution_reasons?.length && (
              <ul className="list-disc space-y-1 pl-5 mt-2">
                {project.support.execution_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
            <p className="mt-2">{project.support.adaptation_reason}</p>
          </details>
        </div>
      )}
    </section>
  )
}
