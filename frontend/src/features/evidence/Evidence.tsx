import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FileCode, MagnifyingGlass, ArrowUpRight, GitBranch } from '@phosphor-icons/react'
import { shortSha, type Run, type Reference } from '../../api/client'
import { Empty, Notice, SectionHeading } from '../../components/ui'
import { Citation } from './Inspector'

export function Evidence({ run }: { run: Run }) {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [side, setSide] = useState('all')
  const references = run.references ?? []
  const visible = references.filter(
    (item) =>
      (side === 'all' ||
        (side === 'source' ? item.ref.repo_id === 'source' : item.ref.repo_id !== 'source')) &&
      `${item.ref.path} ${item.ref.symbol} ${item.category}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  )
  const coverage = side === 'target' ? run.profile?.coverage : run.coverage
  function inspect(reference: Reference) {
    const next = new URLSearchParams(params)
    next.set('ref', reference.id)
    setParams(next)
  }
  return (
    <>
      <SectionHeading title="Follow the evidence">
        Pinned code, stored excerpts, and the context behind the result.
      </SectionHeading>
      <div className="evidence-snapshots">
        {[
          { label: 'Source snapshot', snapshot: run.source },
          { label: 'Target snapshot', snapshot: run.target },
        ].map((item) => (
          <div key={item.label}>
            <GitBranch size={20} />
            <section>
              <span>{item.label}</span>
              <strong>
                {item.snapshot?.origin.startsWith('https://github.com/')
                  ? item.snapshot.origin.replace('https://github.com/', '')
                  : (item.snapshot?.repo_id ?? 'Not recorded')}
              </strong>
              <code title={item.snapshot?.commit_sha}>
                {item.snapshot?.commit_sha ?? 'No pinned snapshot published'}
              </code>
            </section>
          </div>
        ))}
      </div>
      {run.profile && (
        <details className="profile-section surface" open>
          <summary>
            Target profile
            <span>
              {run.profile.stack.language} / {run.profile.stack.python_requirement}
            </span>
          </summary>
          <div className="profile-body">
            <div>
              <h3>Architecture & integration</h3>
              {run.profile.architecture.modules.map((module) => (
                <div className="profile-claim" key={module.path}>
                  <code>{module.path}</code>
                  <p>{module.role}</p>
                  {module.refs.slice(0, 1).map((ref, index) => (
                    <Citation key={index} run={run} reference={ref} />
                  ))}
                </div>
              ))}
              <h3 className="mt-5">Style & error handling</h3>
              <p>{run.profile.style.naming}</p>
              <p className="mt-2">{run.profile.style.error_handling}</p>
              <p className="mt-2">{run.profile.style.configuration}</p>
            </div>
            <div>
              <h3>Existing capabilities</h3>
              {run.profile.capabilities.map((claim) => (
                <div className="profile-claim" key={claim.id}>
                  <p>{claim.behavior}</p>
                  {claim.refs.slice(0, 1).map((ref, index) => (
                    <Citation key={index} run={run} reference={ref} />
                  ))}
                </div>
              ))}
              <h3 className="mt-5">Gaps</h3>
              {run.profile.gaps.length ? (
                run.profile.gaps.map((gap) => (
                  <div className="profile-claim" key={gap.id}>
                    <span className="badge">
                      {gap.basis === 'observed_behavior'
                        ? 'Observed in captured tests'
                        : 'Static inference'}
                    </span>
                    <p className="mt-2">{gap.description}</p>
                    {gap.refs.map((ref, index) => (
                      <Citation key={index} run={run} reference={ref} />
                    ))}
                  </div>
                ))
              ) : (
                <p>No supported gap was recorded.</p>
              )}
            </div>
          </div>
        </details>
      )}
      <div className="surface overflow-hidden">
        <div className="table-toolbar">
          <div className="segmented" aria-label="Evidence repository">
            {['all', 'source', 'target'].map((value) => (
              <button
                key={value}
                onClick={() => setSide(value)}
                aria-pressed={side === value}
                className={side === value ? 'segment-active' : ''}
              >
                {value[0].toUpperCase() + value.slice(1)}
              </button>
            ))}
          </div>
          <div className="search-input compact">
            <MagnifyingGlass size={17} />
            <input
              aria-label="Search evidence"
              placeholder="File, symbol, or category…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
        {visible.length ? (
          <ul className="reference-list">
            {visible.map((reference) => (
              <li key={reference.id}>
                <span className="reference-file-icon">
                  <FileCode size={21} />
                </span>
                <button onClick={() => inspect(reference)} className="reference-link">
                  <strong>{reference.ref.symbol ?? reference.ref.path.split('/').at(-1)}</strong>
                  <span>
                    {reference.ref.path}:{reference.ref.start_line}–{reference.ref.end_line}
                  </span>
                </button>
                <div className="reference-side">
                  <span className="badge">
                    {reference.ref.repo_id === 'source' ? 'Source' : 'Target'}
                  </span>
                  <small>{reference.category}</small>
                </div>
                <code className="reference-sha">{shortSha(reference.ref.commit_sha)}</code>
                <button
                  className="icon-button"
                  aria-label={`Inspect ${reference.ref.path} lines ${reference.ref.start_line} to ${reference.ref.end_line}`}
                  onClick={() => inspect(reference)}
                >
                  <ArrowUpRight size={19} />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No matching evidence">
            {references.length
              ? 'Try a different search or repository.'
              : 'No validated references were published in this run.'}
          </Empty>
        )}
        <div className="table-footer">
          <span>
            {visible.length} references shown / {references.length} recorded
          </span>
          <span>Source tests are cited, not executed</span>
        </div>
      </div>
      <div className="coverage-section">
        <SectionHeading title="Coverage and its limits">
          Read windows establish bounded evidence, not exhaustive semantic coverage.
        </SectionHeading>
        {coverage ? (
          <div className="surface p-6">
            <div className="coverage-numbers">
              <div>
                <strong>{coverage.examined_paths.length}</strong>
                <span>Examined paths</span>
              </div>
              <div>
                <strong>{coverage.unexamined_areas.length}</strong>
                <span>Unexamined areas</span>
              </div>
              <div>
                <strong>{coverage.excluded_paths.length}</strong>
                <span>Excluded paths</span>
              </div>
            </div>
            <Notice>
              Examined files can contain unread lines. The evidence tree is static; it does not
              claim to be a verified runtime call graph.
            </Notice>
            {[
              { label: 'Examined paths', values: coverage.examined_paths },
              { label: 'Unexamined areas', values: coverage.unexamined_areas },
              { label: 'Excluded paths', values: coverage.excluded_paths },
              { label: 'Limits reached', values: coverage.limits_reached },
            ].map((group) => (
              <details className="coverage-paths" key={group.label}>
                <summary>
                  {group.label} ({group.values.length})
                </summary>
                <ul>
                  {group.values.map((item) => (
                    <li key={item}>
                      <code>{item}</code>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
        ) : (
          <Notice>
            No coverage report was published. Coverage cannot be inferred from the available
            results.
          </Notice>
        )}
      </div>
    </>
  )
}
