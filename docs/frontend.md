# Upgrade browser workspace

The local browser application lives in `frontend/`. Its default workflow breaks a public
Python repository into features using `AUTH_MODE=local`; no GitHub App or sign-in is needed.
Only AI discovery/comparison needs `MODEL_API_KEY`. Structural inspection needs no model key,
and semantic analysis needs no Docker runner. Optional GitHub sign-in and repository
indexing are available locally; F2 adds installation states and opt-in pinned private
snapshots. See [GitHub setup](github-setup.md) for configuration and the pending live checks.
Local mode remains the default and the full connected-analysis release is not complete.
F12 also supplies a gated hosted analysis deployment; see [the hosting runbook](hosting.md).
The browser respects the deployment's approved workflows and disables target tests,
adaptation and local diagnostics on hosted services.
For the F10 local public-Python release check, use the
[readiness command and live validation runbook](full-feature-development.md#f10-local-public-python-v1-validation).
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

- **New run:** **Feature discovery** is the default. Inspect a public Python source URL/full SHA/root,
  then choose **Discover features** to build its code-backed capability catalog. A target is optional
  and is added later from the catalog. Choose **Structure only** (`/?view=structure`) for definitions,
  imports and test locations without semantic discovery or model calls.
  Revision type also accepts a branch or tag. **Resolve branch/tag** freezes the returned
  full SHA before inspection; editing the repository/ref invalidates the selection, and
  late responses cannot populate a different repository. Jobs never resolve mutable refs.
  Each inspection shows capability and native-test blockers before submission. Editing
  any pin input clears that selection. **Analyze projects** uses stored project IDs,
  performs structural analysis without model calls, and optionally runs the target's
  original native suite offline. No target or model key is needed for source-only analysis.
  **Try the crawler example** opens `/example`, retaining the reviewed Scrapy retry
  trace/fit workflow and its model/runner requirements.
  In this trace/fit form, **Source commit SHA** is optional: a blank value uses the latest
  default-branch commit when the worker starts and pins it for the entire run. Supplying
  a full SHA selects that exact version. Feature selection still requires the reviewed
  source policy; **Use Scrapy example** fills its reviewed URL and commit together.
- **Source catalog:** after inspecting a supported public source, **Discover features**
  explicitly sends bounded source excerpts to Meta under the displayed allowance.
  `/catalogs/{id}?revision=N` shows all discovered entries, truthful evidence status,
  exclusions, remaining scan scope and per-job/cumulative usage. Search, status filters,
  cursor pages and lazy evidence details use saved data without model calls. **Continue
  discovery** or **Corroborate this feature** creates a new budgeted revision; earlier
  revisions remain readable. Full notices and receipts are linked through job exports.
  **Download feature JSON** in a selected feature saves its explanation, evidence state,
  excerpts, dependencies and exact source/catalog identity directly from saved data.
  **Compare with your project** accepts a goal and optional supported public target.
  **Find expansion ideas** saves the source capability, existing target behavior, proposed extension,
  benefit, existing integration locations, constraints, uncertainty and code references. Suggestions
  are ranked by relevance and stable feature ID, separately from verified-patch eligibility.
  Comparison works even when native target tests are unsupported. A goal without a target uses
  **Assess relevance** and makes no target-specific recommendation. Saved
  **All features** / **Relevant to your project** views share this catalog revision.
  Search, category, evidence/assessment/fit filters, sorts, paging and feature details perform
  no model calls. Counts distinguish assessed, failed and not-yet-assessed entries.
  **Assess more** or **Assess this feature** explicitly starts a fresh budgeted job; changing
  the goal or target creates a new context. **Implementation and verification** in a sufficiently assessed
  feature detail contains the separate **Verify adaptation** action. It
  starts a separate F7 job: it prepares/finalizes behavior and frozen tests,
  checks the original baseline and meaningful assertion red, then verifies a constrained
  production patch with at most three repairs. The current profile supports existing
  sync/async Python functions, existing-class instance methods, new APIs in existing
  modules/classes and injected HTTPX clients with bounded offline scripted exchanges.
  Arguments, constructors and results use JSON. Complete standard MIT or exact
  BSD-2-Clause source notices are required. See the F11 matrix for unsupported shapes.
- **Runs:** persisted history, global server search, workflow filters, bounded pagination, stable URLs,
  and refresh recovery. Existing known CLI report directories are imported at
  backend startup in local mode and labeled recorded CLI runs. GitHub accounts see only
  their own runs; existing local receipts are not assigned to them. Opening a run does not infer
  again. Press `N` outside a text field to start a new run.
- **Workspace:** actual feature cards, controller scores/gates/rejections,
  source-to-target comparison, architecture/gaps/style, dependency and license
  references, bounded coverage, captured cases/logs, model usage, and exports.
  Public project runs instead show bounded production/test definitions, code citations,
  exact project-root identity and coverage; they have Overview/Evidence/Tests/Exports
  views and do not claim feature selection or verified adaptation.
- **Evidence inspector:** exact SHA/file/symbol/line references backed by stored,
  hash-checked excerpts. Source tests are citations, not executed source tests.
  The drawer supports Escape, deep links, and focus return.
- **System checks:** explicitly run the real Meta probe, committed target
  baseline, or working-file baseline. Configuration presence is distinguished
  from an actual connectivity check. No model calls happen on page load.
  **Review supported profiles** loads the execution, acceptance, source-use and private
  release boundaries without running tests or inference.
- **Adaptation runs:** show actual verified/already-present/unverified outcomes, protected-test
  status, production scope and repair count. Tests distinguish the original captures and
  each implementation/repair round. Exports include exact downloadable patches, before/after
  hashes, frozen contracts/tests, complete notices and hash-bound captures. **Review changes**
  opens a file list and loads a bounded unified diff when selected, with old/new line
  numbers, before/after hashes, frozen-test status and direct evidence/notice downloads.
  File and page selections survive reload and Back; long lines are explicitly shortened
  with exact patch download available. Changed or missing required receipts stop review
  with a retry/export action. F13 adds opt-in draft PR publishing after explicit patch/notice
  review, with current write-rights/base checks and durable retry status. It defaults off. Other analysis
  runs continue to identify their analysis-only output.
- **GitHub preview:** explicit sign-in, account-scoped sessions and repository indexing.
  Sync/continue loads the current user/App intersection; search and visibility filters
  operate on the server. Indexed repositories display browse/index support and cannot
  be submitted as arbitrary analysis targets. Signing out and disconnecting are separate.
  On an account change, prior run details, history and repository views are cleared
  before the new account's responses arrive. Form drafts remain scoped to their owner.
  **Check installations** explains suspension/missing permissions. With the explicit
  private-fetch setting enabled, **Pin a repository snapshot** fetches a full SHA/root
  or explicitly resolved authorized branch/tag, without Meta transmission.
  **Inspect snapshot support** adds an access-checked preflight descriptor without copying
  code. **Review private-code disclosure** pairs exact source/target snapshot IDs and
  shows the provider and transmission scope; consent can be saved or withdrawn.
  Private structural analysis, discovery, relevance and adaptation remain disabled even
  after consent until live access/revocation and organization-policy checks pass.
  Disconnect attempts provider revocation
  and reports when manual completion in GitHub settings is needed. Mobile navigation scrolls
  so account controls remain reachable.

The `selected` outcome means the proposed feature passed analysis policy. Retry
diagnostic assertions may be expected failures and remain distinct from ordinary
baseline failures. Unavailable evidence and unrecorded values remain explicit.

## Local API bridge

`backend/app/api/` provides capabilities, targets, run creation/history, ordered
event pages, typed result projections, reference inspection, artifact previews,
and downloads. `/api/catalogs` adds explicit discovery/continuation jobs and revisioned
summary/detail reads on the same queue, execution lock and owner boundaries.
`/api/relevance-assessments` adds explicit goal/target jobs and saved assessment/detail
reads; catalog queries accept `assessment_id`, `mode`, filters and sort. HTTP types are generated from FastAPI's OpenAPI schema into
`frontend/src/api/schema.d.ts`; regenerate with `npm run generate:api` while the
backend is running.

F7 uses the same `/api/runs` queue with
`{"kind":"verified_adaptation","assessment_id":"<saved id>","feature_id":"<catalog feature id>"}`
and the existing `Idempotency-Key` header. Optional `options` can lower the aggregate
attempt/token/time budget or `max_repairs` (0–3). Saved F5 fit gates stay unchanged;
the separate adaptation receipt owns its baseline/gap evidence and final verdict.

F8 adds read-only `GET /api/runs/{id}/changes` for at most five file summaries and
`GET /api/runs/{id}/changes/files/{file_id}?offset=N&limit=200` for a selected diff page.
Each page returns at most 200 records under a 32,000-byte serialized line budget;
individual lines over 8,000 UTF-8 bytes are visibly shortened. The browser mounts one
page in a height-bounded, keyboard-focusable scroll region. The API rechecks saved
patch/mapping/frozen hashes and preserves owner isolation. These routes do not infer,
read live repositories or write GitHub. `publishing_enabled` defaults false. With the F13
development opt-in, verified runs show an isolated publishing form. `GET /api/runs/{id}/pull-request`
reads only saved state. Its explicit `POST` binds to the reviewed patch/notices, fresh
GitHub user/App rights and exact base SHA. Acknowledgment and a button click are required;
opening/reloading the page makes no writes. Draft PR links and retry/stale states survive
reopening. See [publishing setup](github-setup.md#optional-draft-pull-request-publishing-f13).

The API calls the canonical workflow services. `TraceInputs` and `SelectionInputs`
are shared with CLI adapters; it never constructs browser-supplied shell strings.
Selection emits progress at actual check/trace/profile/assessment boundaries.

Public project pins live under `data/projects/`; schema v5 stores their owner-scoped
descriptors in SQLite. Schema v6 adds owner-scoped catalog/revision/feature/operation
records and persistent signed-cursor keys. Schema v7 adds immutable relevance contexts,
per-feature judgments and atomic owner-scoped assessment idempotency. Full commit SHAs are fixed at inspection,
and the worker reopens those objects without fetching a branch again. GitHub private pins remain browse-only
through the separate opt-in F2 workflow.

SQLite state is stored under `data/web/`, and reports remain under
`data/artifacts/`. One process-owned worker executes one workflow at a time,
with four active/queued slots per owner, sixteen globally, and per-owner idempotent
submission keys. OS locks exclude another API worker and concurrent CLI execution.
The execution lock is always the workspace's `data/execution.lock`, including when
the API stores reports in a custom data directory.
The worker waits before claiming while a CLI owns execution. Explicit workflow context
keeps settings and report roots consistent between CLI/API adapters. F6 adds leased claims,
independent heartbeats and fair owner scheduling. Shutdown interrupts its active workflow
and waits for owned cleanup. On restart,
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
produces a sanitized terminal/API failure. See the [F0 audit closure](full-feature-development.md#stage-4-and-f0-closure).

## Verification

F9's complete local check starts its own isolated API and serves a captured production
build. Run from the repository root after installing frontend/backend dependencies:

```powershell
uv run --project backend --locked python scripts/validate_release.py --with-docker --with-audits
```

It runs the controlled browser suite at 390/768/1440 px and real API browsing against
100/1,000/5,000/10,000 synthetic saved entries. It measures response sizes, warm loopback
HTTP latency, heap/DOM growth and search/select long tasks; verifies keyboard focus and
URL restoration; and asserts browsing issues no writes. Catalog search/category inputs
buffer keystrokes locally and commit URL text after 300 ms, preserving fast typing and Back.
With Docker enabled, a fresh controlled-planner offline red-to-green receipt is reopened
through the real API; patch/evidence download digests must match on all viewports.

This uses installed Chrome and `playwright.release.config.ts`. Existing real GitHub,
live-model and historical-receipt integration files remain separate explicit checks;
their absence cannot count as live release evidence. No development server is reused.
The disposable server rejects writes, has no worker/model key and uses its own database.
Optional dependency audits need network access. See [F9's scope and pending gates](full-feature-development.md#f9-local-release-validation).

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
F2 tests additionally exercise restricted installation tokens and JWT signatures, the
ephemeral Git credential helper, private fetches against disposable local Git storage,
webhook tamper/replay/rollback, cross-owner snapshots and revocation during fetch/worker
publication. No live provider account is exercised by those tests.
The [F1 identity audit record](full-feature-development.md#f1-identity-follow-up) covers
populated two-owner artifact/excerpt access, failure and migration boundaries, and
desktop/mobile checks for stale results and drafts during account switching.

## Remaining backend milestones

This delivers the local frontend and its browser bridge, not completion of every
package in the original integration plan. Stage 4 audit findings U0 are repaired;
the [consolidated audit record](full-feature-development.md#stage-4-and-f0-closure)
records the original findings, reproductions and verification.
Arbitrary local targets remain unavailable. Public Python projects use the documented
structural/native-suite profiles and saved F4/F5 catalog/relevance workflows. F7 adds
explicit verified adaptation for the reviewed F11 public Python profiles, and
F8 adds saved diff/evidence review. Broader adapters, live semantic/provider release
receipts and hosted deployment remain open. F13 publishing is implemented locally, defaults
off, and still requires live GitHub exit evidence.

The worker is a thread owned by the local API process, rather than a distributed
worker service. CLI entry points now share the API execution lock. Hard process
termination can leave Docker resources; F6 records generated container ownership before
creation and reconciles registered leftovers under the execution lock before new work.
Failed cleanup stops worker admission and retains its recovery record. Do not treat SQLite
queue persistence as resumable model execution or a production deployment system.

## Local operations and recovery

Run workspaces have **Cancel run**. Queued cancellation settles immediately; running
cancellation waits for the controller to interrupt owned work and preserve intact,
registered evidence. Closing a page keeps its job running. A completed/canceled/interrupted
job never automatically replays inference. Start a new run or explicit continuation to retry.

**System checks → Saved data and limits** reviews this account's queue/storage reservations
and daily provider usage. Deletion requires explicit confirmation and no unfinished jobs.
It removes saved runs, catalogs, assessments, pinned code, exports and consent records,
with a deletion receipt. It preserves sign-in and current daily usage counters. Other
accounts' data is unaffected. Private snapshots expire after 30 days by default; public
workspace data expires after 30 days without a new saved operation. Active jobs delay cleanup.

The local worker periodically applies retention while idle. Settings are documented in
[`backend/.env.example`](../backend/.env.example). `DEPLOYMENT_MODE=local` retains this
workflow; `hosted` uses the separate services and gates in [the hosting runbook](hosting.md).
Paid Meta diagnostics in GitHub mode require an operator ID grant; normal analyses use
their own disclosed allowance. Private snapshot model transmission remains disabled.

Stop the backend before using the offline operator commands, from the project root:

```powershell
uv run --project backend --locked python -m app.jobs.operations metrics
uv run --project backend --locked python -m app.jobs.operations reconcile
uv run --project backend --locked python -m app.jobs.operations prune
uv run --project backend --locked python -m app.jobs.operations backup --destination C:\UpgradeBackups\2026-10-29
uv run --project backend --locked python -m app.jobs.operations restore --source C:\UpgradeBackups\2026-10-29 --destination C:\UpgradeRestore\2026-10-29
```

Use `--data-root` for a custom API data directory. Both worker and workspace execution locks
must be available. Metrics contain IDs/counts/timing/storage metadata, without source text
or credentials. Reconcile interrupts abandoned claims, removes exact registered containers,
discards unfinished snapshot operations and settles inactive artifact reservations to actual
bytes. CLI startup also reconciles registered containers before work and settles its artifact
storage on success or failure. Failed container cleanup blocks new work and keeps its recovery
record. Prune applies retention and preserves unfinished owner jobs.

Backup copies both databases plus managed code/artifact trees and records file hashes;
keep backups private, outside source control, and expire them within 30 days. Keep token
encryption/App keys separately. Restore validates hashes and SQLite integrity into a new
directory, settles unfinished jobs as interrupted, repairs inactive artifact reservations,
and reapplies newer deletion receipts from `--data-root`. Retain that current directory/receipts
when restoring older copies.
Backups older than 30 days are rejected. Inspect the restored data and restart using that
data root only after validation; the live directory is never overwritten by restore.

These commands and controlled tests establish the local boundary. Real hard-stop/provider
cleanup, hosted isolation, production backup/key recovery and deployment rollback still
need deployment-specific validation. See [the F6 record](full-feature-development.md#f6-local-implementation).

For focused F11 browser checks, build first and serve the compiled application in one terminal:

```powershell
cd frontend
npm run build
npm run preview -- --port 5175
```

In a second terminal under `frontend/`:

```powershell
$env:PLAYWRIGHT_BASE_URL = 'http://127.0.0.1:5175'
npm run smoketest -- tests/f11.spec.ts tests/projects.spec.ts tests/account.spec.ts tests/adaptation.spec.ts
```

Use compiled assets for mocked smoke tests: the `/api/**` mocks also match Vite's
unbundled `/src/api/client.ts` path. The default test origin remains port 5173 when
`PLAYWRIGHT_BASE_URL` is unset. These checks use controlled API responses and do not
establish live provider/private-access evidence.
