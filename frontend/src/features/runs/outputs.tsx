import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  DownloadSimple,
  FileText,
  GitDiff,
  MagnifyingGlass,
  ArrowRight,
} from '@phosphor-icons/react'
import { artifactUrl, type Run } from '../../api/client'
import { Empty, Notice, SectionHeading } from '../../components/ui'
import { InspectArtifact } from '../evidence/Inspector'

export function Exports({ run }: { run: Run }) {
  const [query, setQuery] = useState('')
  const artifacts = run.artifacts ?? []
  const visible = artifacts.filter((item) => item.label.toLowerCase().includes(query.toLowerCase()))
  return (
    <>
      <SectionHeading title="Your evidence, ready to inspect">
        Reports, trace bundles, captures, and source notices from this run.
      </SectionHeading>
      <div className="mb-5">
        <Notice>
          Downloads are checked against the recorded manifest. Public exports omit local host paths
          and command arguments; displayed SHA-256 values describe the exported bytes.
        </Notice>
      </div>
      <div className="surface overflow-hidden">
        <div className="table-toolbar">
          <div className="search-input">
            <MagnifyingGlass size={17} />
            <input
              aria-label="Search exports"
              placeholder="Find an artifact…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <span className="text-muted text-xs">
            {artifacts.filter((item) => item.available).length} available
          </span>
        </div>
        {visible.length ? (
          <div className="table-scroll">
            <table className="data-table export-table">
              <thead>
                <tr>
                  <th>Artifact</th>
                  <th>Type</th>
                  <th>Size</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="flex items-center gap-3">
                        <FileText size={19} />
                        <div>
                          <span className="font-medium break-all">{item.label}</span>
                          <details className="artifact-hash">
                            <summary>SHA-256</summary>
                            <code>{item.sha256}</code>
                          </details>
                        </div>
                      </div>
                    </td>
                    <td>{item.kind}</td>
                    <td className="font-mono text-xs whitespace-nowrap">
                      {item.available ? `${(item.size / 1024).toFixed(1)} KB` : 'Unavailable'}
                    </td>
                    <td>
                      {item.available ? (
                        <div className="flex items-center gap-4">
                          <InspectArtifact artifactId={item.id}>Preview</InspectArtifact>
                          <a
                            className="icon-button"
                            aria-label={`Download ${item.label}`}
                            href={artifactUrl(run.id, item.id)}
                            download
                          >
                            <DownloadSimple size={18} />
                          </a>
                        </div>
                      ) : (
                        <span className="text-muted text-xs">Missing or changed</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title={artifacts.length ? 'No matching artifacts' : 'No published artifacts yet'}>
            {artifacts.length
              ? 'Try a different artifact name.'
              : 'Completed outputs will appear here. A failed run may retain partial evidence.'}
          </Empty>
        )}
      </div>
      <p className="footnote">
        A trace bundle contains static evidence and source notices. It is not an executable
        adaptation or a patch.
      </p>
    </>
  )
}

export function Changes({ run }: { run: Run }) {
  return (
    <div className="changes-empty surface">
      <span className="changes-icon">
        <GitDiff size={40} />
      </span>
      <span className="badge">Analysis only</span>
      <h2>This run made no code changes.</h2>
      <p>
        Selection identifies a suitable feature and explains its fit. Patch generation,
        implementation, and final verification require the future adaptation controller.
      </p>
      <div className="changes-boundaries">
        <div>
          <span>Current output</span>
          <strong>Evidence & proposed behavior</strong>
        </div>
        <div>
          <span>Your project</span>
          <strong>
            {run.original_target_unchanged === true
              ? 'Preserved by controller checks'
              : 'See preservation evidence'}
          </strong>
        </div>
      </div>
      <Link className="button-secondary" to={`/runs/${run.id}/features`}>
        Review the proposed feature
        <ArrowRight size={16} />
      </Link>
    </div>
  )
}
