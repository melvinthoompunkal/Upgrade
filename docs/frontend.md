# Upgrade browser workspace

The local browser application lives in `frontend/`. Optional GitHub sign-in and repository
indexing are available as a development preview; see [GitHub setup](github-setup.md).
Local mode remains the default and the full connected-analysis release is not complete.
The UI uses React 19, TypeScript,
Vite 7, Tailwind 4, Phosphor icons, locally packaged IBM Plex fonts, and Motion for
the evidence drawer. Versions are pinned by `frontend/package-lock.json`.

## Start

Requires Node 20.19+ or 22.12+ (Node 24 was used for verification), Python 3.12,
and the existing backend setup. From the project root, use two terminals:

```powershell
uv run --project backend --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/api` and `/health` to FastAPI. No
credentials belong in frontend environment variables. Configure Meta in
`backend/.env`; setup instructions appear in System checks. Docker and the
reviewed image are needed for selection and baseline checks.

For a local production build:

```powershell
cd frontend
npm run build
```

Restart the backend after building, then open <http://127.0.0.1:8000>. FastAPI
serves the compiled assets and explicit application routes. Unknown `/api/*`
requests still return API errors. This is a local development application;
do not bind it to a public interface. No hosted Site was created.

## Workflows and results

- **New run:** supply a public GitHub URL, full source SHA, registered target,
  and goal. The explicit Scrapy example fills its reviewed URL and SHA together.
  Editing the URL clears the SHA. Analysis options include source-only tracing.
- **Runs:** persisted history, global server search, workflow filters, bounded pagination, stable URLs,
  and refresh recovery. Existing known CLI report directories are imported at
  backend startup in local mode and labeled recorded CLI runs. GitHub accounts see only
  their own runs; existing local receipts are not assigned to them. Opening a run does not infer
  again. Press `N` outside a text field to start a new run.
- **Workspace:** actual feature cards, controller scores/gates/rejections,
  source-to-target comparison, architecture/gaps/style, dependency and license
  references, bounded coverage, captured cases/logs, model usage, and exports.
- **Evidence inspector:** exact SHA/file/symbol/line references backed by stored,
  hash-checked excerpts. Source tests are citations, not executed source tests.
  The drawer supports Escape, deep links, and focus return.
- **System checks:** explicitly run the real Meta probe, committed target
  baseline, or working-file baseline. Configuration presence is distinguished
  from an actual connectivity check. No model calls happen on page load.
- **Changes:** explains that current runs perform analysis only. There is no
  simulated implementation, repair history, patch download, or verified badge.
- **GitHub preview:** explicit sign-in, account-scoped sessions and repository indexing.
  Sync/continue loads the current user/App intersection; search and visibility filters
  operate on the server. Indexed repositories display browse/index support and cannot
  be submitted as arbitrary analysis targets. Signing out and disconnecting are separate.
  On an account change, prior run details, history and repository views are cleared
  before the new account's responses arrive. Form drafts remain scoped to their owner.

The `selected` outcome means the proposed feature passed analysis policy. Retry
diagnostic assertions may be expected failures and remain distinct from ordinary
baseline failures. Unavailable evidence and unrecorded values remain explicit.

## Local API bridge

`backend/app/api/` provides capabilities, targets, run creation/history, ordered
event pages, typed result projections, reference inspection, artifact previews,
and downloads. HTTP types are generated from FastAPI's OpenAPI schema into
`frontend/src/api/schema.d.ts`; regenerate with `npm run generate:api` while the
backend is running.

The API calls the canonical workflow services. `TraceInputs` and `SelectionInputs`
are shared with CLI adapters; it never constructs browser-supplied shell strings.
Selection emits progress at actual check/trace/profile/assessment boundaries.

SQLite state is stored under `data/web/`, and reports remain under
`data/artifacts/`. One process-owned worker executes one workflow at a time,
with four active/queued slots per owner, sixteen globally, and per-owner idempotent
submission keys. OS locks exclude another API worker and concurrent CLI execution.
The execution lock is always the workspace's `data/execution.lock`, including when
the API stores reports in a custom data directory.
The worker waits before claiming while a CLI owns execution. Explicit workflow context
keeps settings and report roots consistent between CLI/API adapters. Graceful shutdown
drains its active workflow. On restart,
running jobs whose prior worker stopped are marked interrupted; paid analysis is
never replayed automatically. Queued work can be claimed normally. Run IDs in the
API identify queue records; controller report IDs remain in exported provenance.

Downloads use opaque manifest IDs, deny traversal/links/junctions, and recheck
hashes. Public JSON/text exports omit host paths and command arguments. The
displayed digest describes the resulting export bytes; embedded controller
digests still describe the original evidence. Source trace bundles include the
published notices and are analysis artifacts, not executable packages.
Source-trace and pinned-baseline publication failures retain sanitized JSON and intact
evidence when the report destination is still writable. An unwritable report destination
produces a sanitized terminal/API failure. See the [F0 follow-up receipt](../audit/full-feature-foundations/f0-follow-up.md).

## Verification

```powershell
cd frontend
npm run build
npm run smoketest
```

Smoke tests require both local servers and installed Google Chrome. They launch
an isolated headless Chrome profile. Browser fixtures are confined to `tests/`
and never imported by the application. Tests also inspect actual saved local
results without starting inference. Screenshots, traces, and the HTML test report
are written beneath ignored `data/` directories. The recorded-run test is skipped
when the workspace has no successful selection receipt.

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests
```

API tests cover validation, duplicate submissions, queue limits, event cursors,
persistence, worker ownership/restart handling, changed reports/artifacts,
redacted exports, read-backed excerpts, and shared-service result publication.
These tests do not call Meta or Docker. The new foundation tests also cover auth replay,
browser binding, CSRF, token refresh/rotation, two-owner isolation on result surfaces,
legacy migration, multi-page repository sync, stale cache and disconnect races. The
browser uses compact status polling and retains at most 200 recent events.
The [F1 identity receipt](../audit/full-feature-foundations/f1-identity.md) adds
populated two-owner artifact/excerpt access, failure and migration boundaries, and
desktop/mobile checks for stale results and drafts during account switching.

## Remaining backend milestones

This delivers the local frontend and its browser bridge, not completion of every
package in the original integration plan. Stage 4 audit findings U0 are repaired;
the original review remains historical and the [new closure receipt](../audit/full-feature-foundations/report.md)
records the reproductions and verification.
The Phase 5 planner/Implementer/Verifier, expanded feature catalog, public targets,
and arbitrary local targets remain unavailable.

The worker is a thread owned by the local API process, rather than a distributed
worker service. CLI entry points now share the API execution lock. Hard process
termination does not perform Docker cleanup; inspect and stop any owned leftover
runner container before starting a replacement execution. Do not treat SQLite
queue persistence as resumable model execution or a production deployment system.
