# Upgrade: MVP technical design

Original research: October 27, 2026. Documentation reconciled October 29, 2026,
America/New_York.

Use [project status, audits and the development plan](docs/full-feature-development.md)
for completed milestones, resolved findings, historical verification and the remaining
package checklist. This document retains architecture, contracts, scoring and the
original MVP design. The active milestone is local semantic discovery and source-to-target
comparison, as recorded in [the current scope](docs/full-feature-development.md#active-milestone-local-semantic-discovery-and-expansion).
The account/private-access roadmap remains a deferred extension.

Current product direction, October 30, 2026: break a repository into meaningful
capabilities, compare them with the user's project, and explain useful expansions with
source/target code evidence. Use the existing local public-URL workflow and F4/F5
services. GitHub App setup, private-account integration, automatic patching and hosted
execution are not prerequisites for this milestone. Analysis support for another
language is a separate task from implementing an execution/adaptation runner for it.

The [feature extraction roadmap](docs/feature-extraction-roadmap.md) now specifies the
planned shared source-text discovery and comparison path, with exact citations and
optional parsers. It supersedes mandatory per-language parser/admission work for that
analysis milestone. The application still uses the Python analysis profile today;
execution and verified adaptation keep their separate reviewed profiles.

The implemented demo pair is Scrapy → `examples/crawler-target` (`crawler-demo`).
Selection is delivered; it still produces a proposal without patching the crawler.
F3 adds public Python structural analysis with optional reviewed native offline tests;
the crawler selection is now an explicit example. F4/F5 add saved general Python catalogs
and relevance. F7/F8 add verified adaptation and patch review for the supported synchronous
function/MIT profile. F9 supplies repeatable local regression/build/browser/scale receipts;
live semantic, real-account and hosted release gates remain pending in the roadmap.
Setup and actual browser workflows are in [README](README.md) and
[the browser guide](docs/frontend.md).

### Available terminal actions and their future UI entries

Run these entry points with `uv run --project backend --locked <command>`.
The browser submits these shared workflows through `/api/runs`; CLI entry points
remain available and share the workspace execution lock.

| Command | Current input and result | Browser location |
|---|---|---|
| `graft-meta-smoke` | Local Meta configuration; synthetic structured-plan and harmless tool round-trip check, with call metadata | System checks; explicit user-triggered check |
| `graft-crawler-baseline` | Current local fixture files; isolation and ordinary baseline plus expected missing-retry diagnostic | System checks; label working-file inputs |
| `graft-pinned-baseline` | Registered target committed HEAD and reviewed image receipt; exact exported input/capture binding, retry missing/already-present classification | System checks; label committed inputs |
| `graft-source-trace` | Public source URL/full SHA, registered target ID and goal; one read-only retry feature trace and bundle | New run; trace-only mode |
| `graft-fit-select` | Public source URL/full SHA, registered target ID, goal and reviewed image receipt; fresh baseline, trace, profile, assessment, selection and exports | New run primary action; run workspace |

The source defaults are Scrapy at `5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac`.
Only the recognized pinned source slice/notice can pass the current selection policy;
accepting a different public source input does not establish adaptation support.
Arbitrary host target paths are not browser inputs. F3 public URL/full-SHA/root inspection
creates server-owned source/target project IDs for the separate general workflows. Target
registration, runner/image settings, credentials and policy remain server-side concerns.

### Data and status rules for frontend work

- The current selection outcomes are `selected`, `no_safe_fit` (both exit 0), and
  `failed` (exit 1 with `error_code`/`detail`). `unsupported_target` is an error code
  in a failed selection receipt, not a separate current selection status.
- `selected` means a proposed feature passed analysis policy. Both
  `actual_patch_scope_checked` and `post_implementation_verified` remain false.
  The separate F7 workflow does expose an actual adaptation verdict, repair count and
  patch digest; F8 provides its saved review and exact download. Use each workflow's receipt.
- Read actual `analyzed_feature_count`, `eligible_feature_count`, candidate membership,
  ratings, gates and rejection reasons. A run can fail before analyzing any feature.
  Do not pad the current one-card result into three candidates or hard-code its score.
- Ordinary baseline failures and expected retry-diagnostic assertions are different.
  `already_present` describes an all-green retry diagnostic; it removes that gap.
  Source tests are cited, not executed. Coverage describes bounded reads, not a
  verified runtime call graph or exhaustive semantic analysis.
- Selection JSON artifacts commonly wrap their payload in `data` with snapshot/run
  binding and a content hash. `baseline-binding.json` retains the W1 format;
  `report.json` is the final manifest. Use the actual serializers/contracts, not the
  conceptual shapes later in this document, to build transport adapters.
- `/health` and the local `/api` browser bridge are implemented; FastAPI also generates
  documentation/schema routes. `model_configured` means a key is present, not that a
  fresh connectivity test passed. Its current `phase: "connected_analysis_foundations"`
  is a delivery label; `/api/capabilities` and actual run events drive capability
  detection and progress.
- The implemented UI views are Overview, Features, Evidence, Tests, Changes and Exports.
  Analysis-only Changes explains that no code changed; F7 adaptations show F8's bounded
  diff and integrity-checked exports. Build controls depend on backend capabilities.
  Preserve the implemented shared-service
  boundary as the API expands; do not duplicate workflows in frontend logic.

## 1. Graft audit

I cloned and inspected [Graft](https://github.com/melvinthoompunkal/Graft) at commit [`a386620842c34f1f731bf6ffc91bdb1b101f31ba`](https://github.com/melvinthoompunkal/Graft/tree/a386620842c34f1f731bf6ffc91bdb1b101f31ba), including its README, primary backend, primary frontend, and separate demo backend. The clone is in `.research/Graft/`. These findings are from static code inspection; I did not run Graft or make paid inference calls.

All Graft links below point to that audited commit.
This section describes upstream Graft, not features already integrated into Upgrade.
The delivered backend was implemented independently; Graft's UI remains in `.research`.

| Capability | What the files actually establish | Reuse decision |
|---|---|---|
| FastAPI application | [`graft-backend/main.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/main.py) registers the repo router, health route, exception handlers, CORS, a cleanup task, and optional compiled frontend serving. | Keep application structure, health handling, and static serving. Restrict the new app to localhost and its frontend origin. |
| Repo ingestion | [`services/github_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/services/github_service.py) parses GitHub URLs, fetches metadata, shallow-clones using GitPython, inventories files, reads the README, and counts languages by file extension. | Reuse inventory and clone logic as a starting point. Add SHA pinning, resource limits, safe path handling, and a separate target snapshot. Language counts alone are insufficient for profiling. |
| Feature discovery | [`services/claude_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/services/claude_service.py), `extract_features_from_readme`, requests `{slug,name,description}` from the README. | Replace inference with Meta and corroborate every supported feature in code. README claims are discovery hints, not proof. |
| Candidate selection | The same file's `identify_candidate_files` filters and ranks the file tree, asks for at most 15 files, and rejects returned paths absent from inventory. | Preserve the inventory-membership check. Add source tests, manifest evidence, path containment, and bounded follow-up reads. |
| Structural tracing | [`services/tracer_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/services/tracer_service.py) extracts definitions with Tree-sitter and a regex fallback, builds a symbol skeleton, creates a NetworkX graph, and combines this with an AI trace. | Preserve the evidence-oriented trace shape. For Python MVP use the standard-library AST for definitions/imports; it avoids needing Graft's multilingual parser setup. |
| Trace limitations | `build_call_graph` creates edges when another symbol's name appears in a definition body. The skeleton covers candidate files. Model-provided trace nodes/ranges are not comprehensively checked against actual definitions and file bounds. | Treat edges as heuristic references. Validate every exported path, symbol, and range; do not describe this graph as a verified runtime call graph. |
| Chunked sub-agents | [`services/claude_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/services/claude_service.py) uses Sonnet under an estimated 20,000-token payload; above that it runs roughly 15,000-token Haiku chunks with concurrency 2 and synthesizes with Sonnet. Oversized individual files are truncated. | Reuse bounded fan-out/fan-in as a pattern. Replace both model clients/prompts; use explicit code windows and report incomplete coverage instead of silently treating truncated input as complete. |
| Bundles | [`services/bundle_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/services/bundle_service.py) slices traced line ranges, prepends import lines, filters supported dependency manifests, writes a README and `.env.example`, and ZIPs the result. | Reuse provenance/packaging ideas. Replace executable code-slice bundles with a trace bundle containing evidence, behavior, dependencies, and test references. The existing ZIP is not evidence of a complete runnable transplant. |
| API and contracts | [`routers/repo.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/routers/repo.py), [`models/requests.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/models/requests.py), and [`models/responses.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/models/responses.py) implement ingest, trace, bundle, and session read/delete. | Keep Pydantic. Introduce run-based endpoints and contracts for two repositories, ranking, patches, and test results. |
| Persistence | [`store/session_store.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/store/session_store.py) is a locked in-memory dictionary. [`utils/cleanup.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-backend/utils/cleanup.py) removes directories/bundles using a two-hour modification-time cutoff. | Replace with SQLite run state and explicit retention. Never clean an active worktree or demo artifact based solely on directory mtime. |
| Frontend | [`graft-frontend/package.json`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/package.json) pins React 18.3.1, Vite 6.0.7, and Tailwind 3.4.16. [`src/App.jsx`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/src/App.jsx) handles ingestion, manual tracing, downloads, and page navigation. | Use the React/Vite/Tailwind approach as a reference; choose compatible versions when creating Upgrade's frontend. Build new design tokens and run-based navigation. |
| Trace display | [`src/components/CallChainTree.jsx`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/src/components/CallChainTree.jsx) renders file/function/range/role entries. [`TraceWorkspace.jsx`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/src/components/TraceWorkspace.jsx) displays trace details. | Reuse the inspection pattern with Upgrade citation adapters and new styling; do not imply a verified call graph. |
| Separate demo product | [`graft-demo/backend/routers/demo.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-demo/backend/routers/demo.py) serves pre-analyzed JSON and a live analyze route. [`services/analyze_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-demo/backend/services/analyze_service.py) performs live Claude-based extraction and returns an empty `traces` object. | Cut the library/gallery and parallel app. Cached JSON cannot substantiate a live Meta-powered implementation. |
| Tests and isolation | The primary backend has no test runner or target-edit workflow in its routes/services. [`graft-demo/backend/test.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-demo/backend/test.py) is a manual HTTP probe. The root [`Dockerfile`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/Dockerfile) packages the web app. | Build test execution and execution isolation. Packaging the API in Docker does not isolate arbitrary target tests from the API. |

**Reference patterns:** Pydantic boundaries, file/symbol/range display, small presentation components, and frontend/backend build integration. Upgrade already has its own public errors, contracts, runner and Meta transport. Do not replace these with Graft's older implementations. If upstream component code is copied later, retain Graft's [MIT notice](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/LICENSE.md), adapt its data contract, and apply Upgrade's distinct design.

**Delivered on Meta:** the connectivity/schema/tool probe, bounded source tracer, Target Profiler and Fit Matcher. Graft's Claude services were not integrated. Repository-aware planning, the Implementer and Verifier/Fixer remain pending; the smoke planner uses synthetic inputs.

**Cut:** repo-map navigation, bundle/library management pages, the second demo application, broad manifest rewriting, multilingual transplant support, and claims of complete dependency closure or guaranteed token/rate-limit avoidance. Graft currently analyzes one source; the source-to-target implementation workflow is new work.

## 2. Technology choices and delivery status

| Area | Current delivery | Remaining work / boundary |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic 2, Uvicorn; owner-scoped catalogs/relevance/adaptation/review and opt-in publishing routes; `backend/uv.lock` | Live integration and hosted certification remain pending |
| Frontend | React/Vite/TypeScript workspace, generated transport types, scalable catalog/relevance browsing, adaptation review/exports and opt-in draft PR publishing; `frontend/package-lock.json` | Publishing defaults off; live GitHub and semantic release evidence remains pending |
| Models | Meta Chat Completions; configured default `muse-spark-1.3`, Standard mode | Tracer/profiler use medium reasoning; Matcher/planner/test preparer/implementation use high; live general workflow validation pending |
| API client | `AsyncOpenAI` transport; SDK automatic retries disabled; explicit application retries/attempt accounting | No provider/model fallback; current default at most two HTTP retries per request |
| Orchestration | Shared typed web/CLI inputs and workflow context, selection/catalog/relevance controllers and frozen acceptance/production repair state machine, one run-owned budget | Reviewed profiles only; live and hosted gates remain pending |
| Repository handling | Git CLI, independent pins, public Python inventories/discovery/native tests, constrained verified production patches and preservation checks | Public reviewed profile limits apply; private analysis stays disabled |
| Code indexing | Python `ast`, parsed TOML, bounded text/symbol tools | Static evidence with disclosed limits; no exhaustive dependency closure/runtime call graph |
| Execution | Prepared, pinned Linux Docker image; fixed offline suites and resource restrictions | Only reviewed Python/HTTPX fixture/dependencies; no host execution fallback |
| Tests | Existing target suite and frozen hand-authored retry diagnostic; backend contract/policy/workflow tests | Final adaptation contract, production patch, green acceptance/regression and repair loop pending |
| Storage | Local JSON/artifacts, managed Git repositories, transactional SQLite migrations, owner-scoped runs/events and server history paging/search | Catalog storage, retention/deletion and hosted recovery remain pending |
| Jobs | Single local API worker, durable SQLite queue, per-owner admission/idempotency and shared CLI/API execution lock | Hosted leases/fairness, hard-stop cleanup and broader lifecycle controls remain pending |
| Progress | Durable ordered events, compact status polling, bounded browser event window and refresh recovery | Catalog/adaptation stages depend on their future controllers |
| Delivery | Trace/profile/fit bundles, reports, notices, logs and provenance hashes | `changes.patch` and a verified adaptation branch are future outputs; PR creation remains optional |

The implemented adapter reads `MODEL_API_KEY` locally and targets `https://api.meta.ai/v1`.
Authenticated schema/tool smoke checks and the recorded full selection have succeeded
with `muse-spark-1.3`. Settings also allow explicit Spark 1.2/1.1 selection; alternate
configuration is not evidence of a fresh successful check. No provider fallback occurs.
See [configuration](backend/app/config.py), [transport](backend/app/models/meta_client.py)
and [recorded verification](docs/full-feature-development.md#live-sample-runs). Original documentation references:
[Meta tool calling](https://dev.meta.ai/docs/tool-calling),
[structured output](https://dev.meta.ai/docs/structured-output).

`graft-meta-smoke` already performs a real structured response and harmless read-only
tool round trip. Rerun it when configuration/access changes. Its `RunPlan` uses a
synthetic inventory; the real selection command has no repository-aware planner yet.

**Implemented clone/worktree approach:** retrieve the public source at its full SHA;
clone the registered local target's committed HEAD into independent managed storage.
Create the `graft/<run-id>` worktree against that clone, not the original `.git`.
Dirty local changes are excluded and disclosed. Public target retrieval, production
patching, merging and pushing are not part of current selection.
[Git worktrees](https://git-scm.com/docs/git-worktree).

**Execution boundary:** a worktree separates edits; it does not restrict processes. Only the runner executes target code. It receives an exported snapshot without `.git`, source repo, host home, Docker socket, or API keys. Run as non-root with a read-only root filesystem, dropped capabilities, no-new-privileges, writable temporary/result directories, 2 CPUs, 1 GB memory, a PID cap, and timeouts. Use `--network none`; acceptance tests use in-process HTTP fixtures. The controller retains Git and model access outside this container. [Docker runtime options](https://docs.docker.com/engine/containers/run/), [network isolation](https://docs.docker.com/engine/network/drivers/none/).

For the demo, bake approved target dependencies into a pinned runner image in advance. Do not run a repository's Dockerfile or install arbitrary dependencies on the host. Targets needing unknown packages, services, or network tests return `unsupported_target`; a general-purpose dependency installer is cut.

**Implemented local persistence:** SQLite stores owner-scoped runs, queue state and
sequenced events; artifact bytes and model call/attempt records remain on disk. A single
API-owned worker uses the common CLI/API execution lock and marks abandoned work
interrupted without replaying paid calls. F6 adds local leased jobs, cancellation,
container recovery, quotas and retention. Hosted isolation and real hard-stop certification
remain [release gates](docs/full-feature-development.md#13-development-sequence-and-deliverables).

## 3. MVP feature list

### Required product features and actual completion

| Feature | Current status |
|---|---|
| Source, target and goal inputs | Delivered; F3 adds public Python source/optional-target URL/full SHA/root inspection and owner-scoped project IDs |
| Pinned inventories and supported target baseline | Delivered, including exact target Git bytes and captured same-snapshot tests |
| Bounded catalog with citations/coverage | Retry example plus F4 persistent public Python discovery; live general semantic exit checks pending |
| Target architecture/capabilities/gaps/style | Delivered with same-run baseline and observed-gap provenance |
| Scoring, gates, selection and rejections | Delivered; `top3` has at most three eligible IDs and currently at most one; original audit findings are repaired |
| Trace bundle and adaptation proposal | Delivered with integration/scope/dependency evidence and typed retry-v1 behavior; no applied patch |
| Missing-behavior red evidence | Retry diagnostic plus F7 independently frozen function acceptance with meaningful assertion red |
| Production patch within five files / one new file / 400 changed lines / zero new runtime dependencies | F7 enforces actual patches for the reviewed synchronous Python function/MIT profile; broader profiles remain unsupported |
| Same frozen acceptance and regression green; at most three repair rounds | F7 controller implemented with real offline Docker evidence and controlled planning; live semantic release validation pending |
| Meta call metadata and budgets | Delivered for smoke, tracing, profiling, matching, catalogs, relevance and F7 pinned-code planning |
| Browser input/progress/evidence/ranking/tests/diff | Input, progress, evidence, ranking, tests and exports delivered; F8 lazy production diff review and F13 opt-in draft PR publishing, disabled by default pending live validation |
| Reports, notices, logs and provenance exports | Delivered on disk and through owner-scoped downloads, including F7 exact patches and F8 change review |
| GitHub connection | F2 local lifecycle adds installation state, opt-in private pinned snapshots, credential broker, signed webhooks, provider revocation and current-access checks; real-account gate remains pending |

### Should-have — only after the complete workflow works

- SSE instead of polling, cancellation, and finer code-window navigation.
- Source-analysis cache keyed by source SHA, prompt/schema version, and model ID; label cached stages.
- One additional Python demo target to check that integration paths are discovered rather than hard-coded.
- Optional user-triggered PR creation after reviewing the verified local result.
- Side-by-side source behavior and adapted target implementation in the run view.

### Cut — explicitly outside scope / what shouldnt be thought of

- More than one source, more than one target, or implementation of feature #2/#3 in the same run.
- Generic cross-language ports, framework migrations, dependency upgrades, large refactors, database migrations, or new external services.
- Account systems, teams, hosted arbitrary-code execution, private-repo credential flows, deployment automation, auto-merge, and production rollouts.
- Dashboards as the main product, feature galleries, basic RAG/chat wrappers, Streamlit, graph visualization polish, and analytics pages.
- Persona agents, agent debates, recursive delegation, unlimited repair, self-hosted models, model training, and broad benchmark infrastructure.
- Claiming success from an LLM review, fabricated logs, skipped tests, or a cached green result.

## 4. Agent breakdown and contracts

These are functions with constrained tools, not simulated job titles. They share no mutable conversation history. Each consumes typed artifacts and produces a validated artifact.

The shapes below are **readable contract sketches**, not HTTP response definitions.
Implemented Pydantic contracts reject unknown keys, use immutable collections, and
require explicit nulls for nullable model fields. Repository paths/citations are checked
against pinned evidence. The Implementer/Verifier and persisted API specifications are
future work. Frontend adapters must follow the current serializers and these sources:

| Contract group | Current canonical definition |
|---|---|
| `Contract`, `FrozenList`, `Ref`, `RepoSnapshot`, `RunPlan`, model-call metadata | [contracts.py](backend/app/contracts.py) |
| `Inventory`, `Coverage`, `Dependency`, `FeatureCard`, `SourceTrace`, AST/read inputs | [evidence_contracts.py](backend/app/evidence_contracts.py) |
| Profile proposals/final profile/provenance, adaptation proposal, assessments, gates and fit results | [selection_contracts.py](backend/app/selection_contracts.py) |
| Typed selection-facing baseline binding and W1 conversion | [baseline_contracts.py](backend/app/baseline_contracts.py) |
| Current captured suite/case data | [runner_artifacts.py](backend/app/tools/runner_artifacts.py) |
| Selection artifact envelope/manifest publication | [selection_artifacts.py](backend/app/selection_artifacts.py), [fit_select.py](backend/app/fit_select.py) |

### Shared data shapes

```text
RepoSnapshot = {
  repo_id: string, origin: string, commit_sha: string,
  inventory_artifact_id: string, license_refs: Ref[]
}
Ref = {
  repo_id: string, commit_sha: string, path: string,
  start_line: integer >= 1, end_line: integer >= start_line,
  symbol: string | null
}
Inventory = {
  repo_id: string,
  files: {path: string, sha256: string, bytes: integer, language: string}[],
  excluded_paths: string[], limits_reached: string[]
}
Coverage = {
  examined_paths: string[], excluded_paths: string[],
  unexamined_areas: string[], limits_reached: string[]
}
Dependency = {name: string, kind: "stdlib" | "runtime" | "test", refs: Ref[]}
FeatureCard = {
  feature_id: string, name: string, behavior: string,
  entry_points: Ref[], implementation: Ref[], dependencies: Dependency[],
  source_tests: Ref[], acceptance_behaviors: string[],
  transferable_slice: string, excluded_behaviors: string[],
  license_spdx: string | null, license_refs: Ref[],
  evidence_status: "supported" | "partial" | "unsupported"
}
ArtifactEnvelope<T> = {
  schema_version: "1", run_id: string, artifact_id: string,
  source_sha: string, target_sha: string, target_repo_id: string,
  target_path_prefix: string, content_sha256: string,
  model_id: string | null, response_id: string | null, data: T
}
```

The envelope sketch describes derived Stage 4 selection artifacts after input binding;
standalone trace artifacts, the W1 binding and the final manifest have their own formats.
The controller supplies provenance and hashes. Some `data` payloads are model proposals;
others are validated/controller-derived artifacts, including totals and selection.
Citation existence, read windows and AST overlap do not prove semantic correctness.
Captured tests supply bounded behavioral evidence.

### Tracer

**Delivered scope:** `SourceTracer` traces exactly one bounded Scrapy retry feature.
`SourceTrace` can represent up to eight cards; focus selection, prompts, validation and
export support for a broader catalog are still optional 4B work. The `limits` sketch
below expresses intended catalog bounds, not a currently configurable discovery API.

**Input**

```text
{
  source: RepoSnapshot, goal: string,
  limits: {max_features: 8, max_files_per_feature: 15}
}
```

**Output**

```text
{source_sha: string, features: FeatureCard[], coverage: Coverage}
```

**Access:** read source README, manifests, license, inventory, and bounded source code/test ranges; search symbols/imports. No source execution, target writes, shell, or dependency installation. A trace bundle stores the chosen card, referenced excerpts with hashes, source test references, and notices.

**Model:** `muse-spark-1.3`, medium reasoning for discovery/tracing; use bounded chunk analysis with concurrency at most two when needed. Its structured tool/JSON interfaces suit evidence extraction. Keep individual files readable by ranges instead of relying on a giant context window.

**Verification:** every supported card needs an entry point and implementation evidence. Match dependencies to manifests/imports and distinguish standard-library modules from packages. Record absent source tests as an empty array. Partial cards cannot win selection. Model synthesis cannot erase missing evidence. Full catalog coverage is never implied when the cap is reached.

### Target Profiler

**Delivered:** the model generates `TargetProfileProposal`; the controller validates
references and adds tests, coverage and `ProfileProvenance` to build `TargetProfile`.

**Input**

```text
{target: RepoSnapshot, goal: string, runner_id: string}
```

**Output**

```text
{
  target_sha: string,
  stack: {language: string, python_requirement: string,
          frameworks: string[], dependencies: Dependency[], refs: Ref[]},
  architecture: {entry_points: Ref[],
                 modules: {path: string, role: string, refs: Ref[]}[]},
  capabilities: {id: string, behavior: string, refs: Ref[]}[],
  gaps: {id: string, description: string,
         basis: "goal_inference" | "observed_behavior", refs: Ref[]}[],
  style: {naming: string, error_handling: string,
          configuration: string, refs: Ref[]},
  integration_points: Ref[],
  tests: {runner_id: string, test_paths: string[],
          baseline_capture_id: string, baseline_status: "passed" | "failed"},
  coverage: Coverage,
  provenance: {
    run_id: string, target_repo_id: string,
    baseline_binding: {artifact_id: string, sha256: string},
    evidence_reads: {artifact_id: string, sha256: string},
    observed_gaps: {gap_id: string, capture_id: string,
                    cases: {name: string, outcome: "passed" | "failed"}[]}[]
  }
}
```

**Access:** bounded target inventory/manifest/source/test evidence and controller-supplied
baseline results. In the delivered workflow the controller runs the fixed suites before
profiling; the profiler has no runner command or write tool. Target code executes only
inside the restricted runner container.

**Model:** `muse-spark-1.3`, medium reasoning. Architecture/style inference and gap assessment need code context, while dependency/version extraction is deterministic where possible.

**Verification:** check manifests and cited symbols against actual files. Missing search hits yield a provisional gap, not a proven absence. Baseline test failure or an unsupported runner blocks implementation. A feature already present is rejected or explicitly narrowed to a demonstrable missing behavior.

### Fit Matcher

**Delivered:** the model returns `AssessmentBatch`, with exactly one assessment per supplied
card. Each includes five evidence-backed criteria and an `AdaptationPlan` describing gap
IDs, integration references, production/new paths, estimated lines, existing dependencies,
proposed additions and service needs. Python creates `CandidateScore` and `FitResult`.
The final output below is controller-owned, not the model's response schema.

**Input**

```text
{
  goal: string, source_catalog_artifact_id: string,
  target_profile_artifact_id: string, rubric_version: "fit-v1"
}
```

**Output**

```text
Criterion = {rating: integer 0..5, reason: string, evidence: Ref[]}
CandidateScore = {
  feature_id: string,
  criteria: {gap_fit: Criterion, isolation: Criterion,
             dependency_cost: Criterion, stack_compatibility: Criterion,
             testability: Criterion},
  gates: {name: string, passed: boolean, reason: string}[],
  eligible: boolean, total: integer 0..100
}
FitResult = {
  rubric_version: "fit-v1", candidates: CandidateScore[],
  top3: string[], selected_feature_id: string | null,
  rejected: {feature_id: string, reasons: string[]}[],
  selection_reason: string
}
```

**Access:** read validated feature cards, target profile, evidence excerpts, and scope policy. May request one bounded evidence clarification. No writes or test commands.

**Model:** `muse-spark-1.3`, high reasoning, because comparing tradeoffs and rejecting superficially attractive features is a core product decision.

**Verification:** Python computes totals, applies gates, sorts candidates, and derives `top3`/selection; never trust arithmetic or ordering generated by the model. Reasons must cite source and target evidence. If fewer than three eligible features exist, return fewer with an explanation. Never invent options to fill the UI. If none qualifies, finish with `no_safe_fit` and no patch.

**Current boundary:** the repaired S4-A01/A02 gates require the integration file in proposed
scope and a typed retry-v1 behavior contract. Gate success still assesses a proposal,
not a verified patch. One clarification round permits at most eight bounded reads.

### Implementer

**Planned; not implemented.** The following input, output and tools are the later-stage
design. No `propose_patch` tool or production patch CLI exists today.

**Input**

```text
{
  selected_feature_id: string, trace_bundle_artifact_id: string,
  target_profile_artifact_id: string, target_base_sha: string,
  acceptance_contract_artifact_id: string, edit_allowlist: string[],
  limits: {max_production_files: 5, max_new_production_files: 1,
           max_changed_production_lines: 400, max_new_runtime_dependencies: 0}
}
```

**Output**

```text
{
  feature_id: string, target_base_sha: string, patch_artifact_id: string,
  touched_paths: string[],
  adaptation: {source_refs: Ref[], target_paths: string[], explanation: string}[],
  runtime_dependency_additions: string[], unresolved_issues: string[]
}
```

**Access:** read the selected source bundle and relevant target code/style examples; request additional target ranges by path. Submit a patch through the controller's `propose_patch` tool. It cannot edit source, original target, frozen tests, manifests/locks, runner definitions, or controller code. No unrestricted shell.

**Model:** `muse-spark-1.3`, high reasoning, for translating the traced behavior into the target's existing API, error handling, and configuration conventions.

**Verification:** controller checks base SHA, path containment, patch applicability, allowlist, file/line budget, and dependency delta before applying. Adaptation notes must connect source behavior to target changes. Compile/import checks and behavioral tests decide whether the patch works. Matching the source's class names is not required; importing Scrapy into the target would fail the proposed dependency gate.

### Verifier/Fixer

**Planned; not implemented.** The existing runner and hand-authored retry diagnostic
provide baseline/red evidence, but there is no model acceptance-preparation or repair
controller. The planned function runs before implementation to finalize/freeze acceptance,
then after implementation to verify and, if necessary, repair production code.

Current runner captures have this actual data shape (JSON serialization uses arrays):

```text
TestCase = {name, outcome: "passed" | "failed" | "error" | "skipped", message, detail}
TestCapture = {
  suite: string, status: "passed" | "failed" | "error" | "timeout",
  exit_code: integer, elapsed_ms: integer, command: string[], image_id: string,
  cases: TestCase[], artifact_hashes: {relative_artifact_path: sha256}, diagnostic: string
}
```

The baseline binding separately ties these captures to target inputs and acceptance hashes.
The richer `VerificationCapture` below is a future specification, not today's `capture.json`.

**Input: a tagged union**

```text
PrepareInput = {
  phase: "prepare", feature_id: string, trace_bundle_artifact_id: string,
  target_profile_artifact_id: string, target_base_sha: string, runner_id: string
}
CheckInput = {
  phase: "check", acceptance_contract_artifact_id: string,
  candidate_snapshot_artifact_id: string, runner_id: string, repair_round: integer 0..3
}
FixInput = {
  phase: "fix", feature_id: string, repair_round: integer 1..3,
  failure: {command_id: string, exit_code: integer,
            failing_test_ids: string[], stdout_tail: string, stderr_tail: string},
  relevant_files: {path: string, content: string, sha256: string}[],
  acceptance_contract_artifact_id: string, edit_allowlist: string[]
}
```

**Output contracts**

```text
AcceptanceContract = {
  feature_id: string, target_base_sha: string, runner_id: string,
  cases: {test_id: string, behavior: string, source_refs: Ref[],
          source_test_refs: Ref[], expected_baseline_failure: string}[],
  protected_files: {path: string, sha256: string}[],
  red_capture_id: string
}
VerificationCapture = {
  capture_id: string, stage: "baseline" | "red" | "green" | "regression",
  command_id: string, argv: string[], snapshot_sha256: string,
  image_digest: string, acceptance_sha256: string | null,
  exit_code: integer | null, timed_out: boolean,
  counts: {passed: integer, failed: integer, skipped: integer},
  stdout_artifact_id: string, stderr_artifact_id: string,
  junit_artifact_id: string, duration_ms: integer
}
VerificationReport = {
  status: "verified" | "failed" | "infra_error" | "no_valid_red",
  captures: VerificationCapture[], repairs_used: integer 0..3,
  protected_tests_unchanged: boolean, explanation: string
}
PrepareOutput = {phase: "prepare", contract: AcceptanceContract}
CheckOutput = {phase: "check", report: VerificationReport}
FixOutput = {phase: "fix", patch_artifact_id: string, explanation: string}
```

**Access:** prepare mode may add acceptance tests in a separate protected artifact directory. Check mode invokes registered sandbox commands and reads their captures. Fix mode sees only the failure packet, frozen acceptance contract, and relevant target files; it gets no full source repository or previous agent conversations. An additional file read must be justified by a failing import/stack frame and stays bounded. It can propose production-only patches through the same gate as Implementer.

**Model:** `muse-spark-1.3`, high reasoning for acceptance design and failure diagnosis. Process execution, exit codes, test hashes, counts, and final pass/fail are controller-generated facts, not model judgments.

**Verification:** first run the existing suite on the untouched base. Then create tests against the target's existing public API and run them on that base. Require an assertion failure caused by the missing behavior; an import error, timeout, or missing service is not valid red evidence. Freeze test hashes before Implementer runs. Both agents are prohibited from deleting tests, changing assertions, adding skips/xfails, or modifying the runner/configuration. Run the same frozen acceptance tests and the original suite on the final snapshot.

There is one initial implementation followed by **at most three fix → rerun rounds**, four candidate validations maximum. Schema/API retries do not permit extra repair rounds. After round three fails, export failure evidence and the unverified patch. Do not implement the second-ranked feature as an automatic fallback.

### Planner and controller

**Current:** `RunPlan` and schema/tool validation exist in the synthetic Meta smoke check.
`graft-fit-select` directly sequences the baseline, tracer, profiler and matcher; it does
not make a repository-aware planning call, dispatch parallel analyses, patch, or repair.

**Planned:** connect the constrained `RunPlan` to actual pinned repository inventories:

```text
{
  source_focus_paths: string[], target_focus_paths: string[],
  feature_focus: string[],
  analysis_tasks: ("trace_source" | "profile_target")[],
  rationale: string
}
```

The eventual controller must validate paths/tasks and require exactly one `trace_source`
and one `profile_target` task. Concurrent analysis, if added, must use at most two model
requests in flight under one budget. After matching it must prepare/freeze acceptance,
dispatch Implementer, and verify/repair. Models propose analysis/adaptation decisions;
code enforces sequence, permissions, limits and final success. No recursive delegation
is needed. Nullable `acceptance_sha256` refers to the proposed verification capture above,
not the delivered runner capture fields.

**Current selection budget:** 32 HTTP attempts including retries, 400,000 returned input
tokens, 40,000 returned output tokens and 1,200 seconds checked at request boundaries.
Stages share one client/budget and execute sequentially. Output caps are at most 8,000
tokens per request and the remaining allowance. These are not hard preflight billing or
whole-process wall-time guarantees. Failed attempts remain in the ledger; unavailable
provider usage is null. Later planning/patch/repair stages must not reset the allowance.

**Catalog design bounds:** up to eight cards and 15 evidence files per feature, with
bounded inventories/read windows. The present tracer still analyzes one retry card.
Measure combined usage before expanding scope; report limits rather than hiding missing
evidence. See current repository/evidence constants and Stage 4 documentation for enforced
size/read limits.

## 5. Exact fit-scoring rubric

Rate each criterion from 0–5. For intermediate ratings, 1 means major obstacles, 2 substantial adaptation, 3 moderate adaptation, and 4 minor adaptation; 5 means direct evidence of a strong fit. Unknown evidence scores 0 until investigated.

| Criterion | Weight | 0 | 3 | 5 |
|---|---:|---|---|---|
| Gap fit | 35% | Unrelated goal or capability already present | Useful improvement, but secondary to the goal | Directly addresses an observed target failure or central missing behavior |
| Isolation | 25% | Requires architecture replacement or broad changes | Fits limits but touches several coupled components | One narrow integration point; clear input/output and behavior boundary |
| Dependency cost | 15% | Adds a service or substantial runtime stack | Can be adapted with current dependencies, but needs material glue | Existing target APIs/stdlib are sufficient, with minimal glue |
| Stack compatibility | 15% | Unsupported language/runtime or fundamentally incompatible execution model | Same language, significant framework adaptation | Same language and compatible APIs/execution conventions |
| Testability | 10% | No deterministic acceptance check | A local deterministic test is possible with fixture work | Small offline tests and clear source test cases support a valid red-to-green result |

`score = 35×gap/5 + 25×isolation/5 + 15×dependency/5 + 15×stack/5 + 10×testability/5`.

**Hard gates before ranking:** valid code citations; supported Python target and passing baseline; explicit missing target behavior; small isolated slice within edit limits; no new runtime dependency/service; compatible source-use permissions/notices established; deterministic acceptance plan; and no partial source evidence for the winning slice. Require gap ≥3, isolation ≥3, stack ≥3, testability ≥3, and total ≥75. A high weighted score cannot override a failed gate.

Sort eligible candidates by total, then isolation, testability, dependency cost, and finally stable feature ID. `selected_feature_id` must equal the first result. Emit all rejected candidates with gate/score explanations.

**Actual recorded Stage 4 selection**, not a default ranking for future runs:

| Candidate | Gap | Isolation | Dependency | Stack | Tests | Total | Recorded outcome |
|---|---:|---:|---:|---:|---:|---:|---|
| `http-retry-bounded` | 5 | 4 | 5 | 5 | 4 | **93** | Selected; one analyzed/eligible card, all ten gates passed in that run |

The exact arithmetic is `7*5 + 5*4 + 3*5 + 3*5 + 2*4 = 93`. Ratings are model
assessments, not measured probabilities. The adaptation remains a proposal inside
`Crawler.fetch`, using existing HTTPX, with no implemented patch. Proposal gates include
the recorded audit repairs. The old illustrative three-feature ranking has been
removed so it cannot be mistaken for real comparative output.

| Additional research slice | Current target facts | Delivery boundary |
|---|---|---|
| Depth bound | `max_pages` exists; no explicit depth parameter/tracking in the inspected crawler | Independent source trace/comparison pending; no registered depth acceptance suite or score |
| Equivalent duplicate-visit suppression | Already implemented with the crawler's visited identities | An equivalent feature must be rejected as already present; richer fingerprinting is a different proposal |
| Full scheduler/engine or AutoThrottle | Framework coupling exceeds the intended lightweight adaptation | Research rejection examples, not extra assessed candidates in the current run |

The frontend renders only candidates actually supplied to the Matcher. It must not turn
these research possibilities into scored feature cards or imply source tests were run.

## 6. End-to-end data flow

### Delivered terminal selection workflow

1. Read source URL/SHA, registered target ID, goal and image receipt from CLI arguments.
2. Initialize strict schemas, local settings and one Meta client/shared budget.
3. Pin the committed target, create its clean managed worktree, and validate parsed stack facts.
4. Export exact target Git bytes; run isolation, ordinary baseline and frozen retry diagnostic
   through fixed Docker suites; validate their same-run binding.
5. Retrieve the pinned source and run the bounded read-only source tracer.
6. Run the Target Profiler with target evidence and captured baseline/diagnostic results.
7. Run the Matcher, validate assessments and compute gates, scores, ordering and selection
   in Python; allow at most one bounded clarification round.
8. Check source/original-target/worktree preservation and artifact integrity; publish available
   reports, model attempts/usage, read logs, notices, profiles and fit results.
9. End with `selected`, `no_safe_fit`, or a specific `failed` receipt. No target production
   file is patched. Recorded audit repairs cover finalization and corrupt captures.

The CLI and browser API share this lifecycle. A local SQLite queue, worker, ordered
events and saved browser results are implemented; hosted lifecycle remains F6.

### Planned browser and complete adaptation workflow

The run API/queue and shared selection services below exist. Repository planning,
general discovery, patching and verification remain proposed; consult the current package
checklist before enabling additional workflows.

1. **Input:** submit source URL/full SHA, registered local target ID, and goal. Public target URLs require additional target retrieval/runner registration. Local registration is a developer-side action; the browser cannot browse arbitrary host paths.
2. **Create run:** `POST /api/runs` validates input and enqueues work, returning `{run_id,status:"queued"}`. Other proposed routes are `GET /api/runs/{id}`, `GET /api/runs/{id}/events?after=<sequence>`, and `GET /api/runs/{id}/artifacts/{artifact_id}`. Artifacts are looked up by registered IDs, never client-supplied filesystem paths.
3. **Retrieve and pin:** clone source/target, record SHAs and licenses, build inventories, and prepare the managed target worktree. Disable submodule recursion and Git hooks/filter execution. Skip symlinks, binaries, secrets, dependency/build directories, and oversized files; report exclusions.
4. **Preflight:** check supported Python/runtime/dependencies and run the unchanged target suite in the sandbox. Stop on an unsupported environment or failing baseline.
5. **Plan:** Muse Spark receives the goal, repository summaries/manifests, and inventories. Its validated plan focuses subsequent analysis.
6. **Analyze:** Tracer builds code-backed feature cards while Target Profiler builds the target map. Persist artifacts and visible coverage.
7. **Match:** Muse Spark assesses rubric criteria and rejections. The controller recomputes totals/gates and records top three plus the single winner.
8. **Prepare selected transfer:** package the winning trace excerpts, dependencies, source test references, and adaptation boundaries. Lock the selected feature ID and production edit budget.
9. **Establish red:** Verifier prepares target-native acceptance tests from the source behavior and goal. Run them against the untouched base, validate the failure reason, and freeze test artifacts/hashes. If they already pass, return a disputed-gap result; do not manufacture a failure.
10. **Implement:** Muse Spark proposes an adapted patch. The controller validates and applies it only to the managed target worktree.
11. **Verify/fix:** run frozen focused tests and the existing target suite. Send only failure details and relevant files to Fixer when needed. Reapply the same patch restrictions; stop after three repair rounds.
12. **Export:** produce `changes.patch`, source-to-target mapping, source SHA/notices, model-call metadata, baseline/red/green/regression captures, and final report. The selected implementation branch remains local for inspection. Failure reports label patches unverified.

Planned logical stages/outcomes; the API projection separates run status, stage and outcome:

```text
queued → preparing → baseline → planning → analyzing → matching
       → selected | no_safe_fit                         # analysis-only mode
       → acceptance_red → implementing → verifying ↔ fixing
       → verified | failed | no_safe_fit | unsupported_target | interrupted
```

Planned events include `{sequence,run_id,timestamp,stage,kind,message,artifact_ids}` and
polling resumes from sequence. `verified` may be set only after all required executed
tests pass, protected hashes match, actual scope/dependency checks pass, and valid red
evidence exists. The current `failed`/`error_code` representation must be mapped explicitly
when introducing richer API outcomes. See the [frontend plan](docs/frontend-integration-plan.md)
for durable queue ownership, idempotency, refresh recovery and artifact serving.

## 7. Concrete crawler demo plan

### Historical compatibility check and implemented pair

OpenClaw's inspected [`package.json`](https://github.com/openclaw/openclaw/blob/main/package.json) describes a multi-channel AI gateway and declares Node/pnpm/TypeScript tooling. Its [`tsconfig.json`](https://github.com/openclaw/openclaw/blob/main/tsconfig.json) configures TypeScript with NodeNext and Node types. It is a TypeScript/Node source, not a Python crawler framework.

That historical comparison is outside the delivered selection run. The registered target
is now the implemented Python/HTTPX crawler below; OpenClaw is not an analyzed candidate.
Other languages/build systems remain unsupported by the prepared runner.

**Implemented demo pair:**

- **Source:** [`scrapy/scrapy`](https://github.com/scrapy/scrapy), inspected clone commit [`5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac`](https://github.com/scrapy/scrapy/tree/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac), stored locally in `.research/Scrapy/`.
- **Target:** [`examples/crawler-target/`](examples/crawler-target/src/crawler.py), a registered fixture inside this monorepo with Python 3.12, HTTPX and pytest. Public APIs are `Crawler(client).fetch(url)` and `crawl(start_url, max_pages=10)`. It parses links, crawls breadth-first within the canonical origin, suppresses repeated visits, and enforces a page cap. Fetch makes one attempt; bounded retries and explicit depth tracking are absent. There is no standalone crawler command-line entry point or separate target Git repository. The selection controller pins the monorepo commit with prefix `examples/crawler-target`.
- **Goal:** “Make my crawler more reliable when a site returns temporary HTTP errors, while keeping it lightweight.”

Scrapy's audited [`pyproject.toml`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/pyproject.toml) establishes Python ≥3.11 and substantial framework dependencies including Twisted. The shared language makes a small behavioral adaptation plausible; it does not make a direct middleware transplant compatible. The target must keep HTTPX and acquire none of Scrapy's runtime stack.

### Real source evidence to show

| Feature | Inspected implementation | Inspected source tests / qualification |
|---|---|---|
| Retry policy | [`scrapy/downloadermiddlewares/retry.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/downloadermiddlewares/retry.py): `RetryMiddleware.process_response`, `_retry`, `get_retry_request`. [`scrapy/settings/default_settings.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/settings/default_settings.py) sets `RETRY_TIMES=2` and retryable status codes. | [`tests/test_downloadermiddleware_retry.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/tests/test_downloadermiddleware_retry.py): `TestRetry.test_503`, `test_404`, and `TestMaxRetryTimes.test_with_settings_zero`. |
| Depth limit | [`scrapy/spidermiddlewares/depth.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/spidermiddlewares/depth.py): `DepthMiddleware.get_processed_request`. | [`tests/test_spidermiddleware_depth.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/tests/test_spidermiddleware_depth.py): `test_process_spider_output`. |
| Duplicate suppression | [`scrapy/dupefilters.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/dupefilters.py): `RFPDupeFilter.request_seen`; [`scrapy/utils/request.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/utils/request.py) handles fingerprinting. | [`tests/test_dupefilters.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/tests/test_dupefilters.py): `DupeFilterTestMixin.test_filter`. URL canonicalization relies on **w3lib**; do not attribute its implementation to Scrapy or quietly import its dependency graph. |
| Rejected full integrations | [`scrapy/core/scheduler.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/core/scheduler.py), [`scrapy/core/engine.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/core/engine.py), [`scrapy/extensions/throttle.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/extensions/throttle.py). | Show framework coupling as the rejection reason; do not suggest these are absent from the source. |

**Proposed next adaptation:** retry GET/503 responses up to three total attempts inside
the target's existing fetch path, preserving final HTTPX status raising and parsing after
success. The frozen diagnostic establishes this narrow behavior, not every Scrapy retry
status or transport exception. Keep Scrapy request cloning, priority scheduling, stats,
callbacks, persistent queues, backoff and Retry-After outside this contract. Finalize
the typed behavior/acceptance binding before generating a patch.

### Proposed 3–4 minute script

This is the future full-product demonstration. Today the CLI can show pinned inputs,
real trace/profile/selection and the red diagnostic through CLI/browser. A repository planner,
multi-candidate comparison, patch generation and green verification are not yet available.

1. **0:00–0:25 — establish the problem.** Show the small target's existing suite passing and a fixture that returns `503, 503, 200`. Its current fetch fails after the first response. Explain the goal in one sentence.
2. **0:25–0:50 — start a real run.** Enter the Scrapy URL, target ID, and goal. Show commit SHAs and a live Meta planner event. Source can be pre-cloned for latency, but model calls and target tests are real and current.
3. **0:50–1:30 — show judgment.** Show the actual analyzed/eligible counts and candidate evidence. Show comparative rejections only when independently traced candidates exist. Open the retry implementation/test citations and explain the existing-dependency adaptation boundary.
4. **1:30–2:00 — show red evidence.** The verifier's frozen test calls the target's current fetch API with an HTTPX `MockTransport` fixture. Capture an HTTPX status exception as an observed fetch outcome, then assert successful recovery and the expected response body. The assertion fails because the target stops at the initial 503. Show the command, assertion, exit code, and test artifact hash.
5. **2:00–3:00 — show adaptation and green.** Show the generated diff at the target fetch path. Run the same acceptance tests: transient errors recover, permanent 404 is attempted once, and persistent 503 stops after three total attempts. The existing suite also passes. Display actual result counts captured at runtime.
6. **3:00–3:30 — inspect the deliverable.** Show patch/report paths, unchanged dependency manifest, source provenance, and the recorded Meta model-call IDs. Conclude with the working crawler behavior and local branch ready for review.

HTTPX documents `MockTransport` for predetermined responses without external requests. This lets the crawler's real fetch logic run offline in the restricted container. [HTTPX transports](https://www.python-httpx.org/advanced/transports/).

The seven hand-authored cases already cover recovery after one/two 503 responses,
exhaustion, immediate success, permanent 404, parsing after recovery and unchanged
successful traversal. Recorded baseline/red captures exist; post-patch green captures
do not. Use actual run counts/timings in the demo. A saved run must be labeled recorded.

The original October 27 event research recorded a November 4, 2026, 9:00 PM ET deadline
and left detailed participant/reuse rules unresolved. This implementation-status update
does not recheck event requirements. Verify them with the organizer before submission;
they are not frontend capability requirements. Original references:
[Meta event](https://dev.meta.ai/events/global-hackathon),
[organizer event](https://cerebralvalley.ai/e/meta-ai-developer-hackathon),
[waiver](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver).

## 8. Current folders and planned additions

Selected delivered paths in this workspace:

```text
Upgrade/
  README.md
  tech_breakdown.md
  backend/
    pyproject.toml
    uv.lock
    app/
      main.py                         # health, browser API and local worker lifecycle
      config.py
      contracts.py
      evidence_contracts.py
      baseline_contracts.py
      selection_contracts.py
      smoke.py
      baseline.py
      pinned_baseline.py
      source_trace.py
      fit_select.py
      tracer.py
      target_profiler.py
      fit_matcher.py
      evidence_tools.py
      selection_baseline.py
      selection_artifacts.py
      models/                         # Meta transport, schemas, budget
      tools/                          # Git, repositories, evidence, runner
    tests/
  docs/
    phase3.md
    stage4-w1.md
    stage4-w2.md
    stage4.md
    frontend-integration-plan.md
  sandbox/
    Dockerfile.python-demo
    requirements.lock
    pytest.ini
    test_isolation.py
    acceptance/test_retries.py
  examples/crawler-target/             # fixture in this monorepo
  scripts/
    build_runner.py
    check_runner.py
    check_capture_faults.py
  data/                               # ignored generated outputs
    runner-image.json                 # created by runner build
    artifacts/<run-id>/                # created by each workflow
  .research/                          # ignored reference clones
  audit/                              # machine receipts; ignored local history archive
  skills/                             # ignored local project skills
```

A completed selection run publishes `report.json`, `report.md`, `source-trace.json`,
`source-catalog.json`, `trace-bundles/<feature-id>.json`, `target-profile.json`,
`baseline-report.json`, `baseline-binding.json`, `matcher-assessment.json`,
`gate-evidence.json`, `fit-result.json`, evidence read logs, complete notices and captures.
Its working directory also contains managed repositories, the exact exported `snapshot/`
and protected diagnostic `acceptance/`. Not every internal directory is a downloadable
artifact. Failed runs can have only a subset; consult the final verified manifest.

`frontend/`, `backend/app/api/`, shared workflow context/worker services, SQLite history,
and optional GitHub identity/repository-index modules are implemented. General catalog
and patch/verification modules remain planned. The [frontend plan](docs/frontend-integration-plan.md)
retains the design criteria. Keep frozen acceptance artifacts outside the production
worktree; future patch tools must not be able to edit them.

## 9. Delivery sequence

The current dependency order, completed packages and remaining exit criteria live in
[the single development checklist](docs/full-feature-development.md#13-development-sequence-and-deliverables).
Estimate remaining work from the chosen adapter and hosting scope rather than the
original day-by-day schedule.

Event rules and pre-existing-code disclosure still need separate verification before
submission. Preserve research/implementation provenance; recorded runs and earlier
Graft code must retain their original dates and attribution.

## 10. Risks and failure points

This table combines delivered safeguards with requirements for remaining work. Patching,
repair and hosted recovery safeguards are future requirements. The original six
Stage 4 defects have [recorded repairs](docs/full-feature-development.md#stage-4-and-f0-closure);
remaining operational limits are tracked with the development plan.

| Risk | Mitigation or cut |
|---|---|
| Meta credentials, quota, or model access fails | Rerun the existing smoke check when configuration/access changes. Preserve response IDs/usage and bounded explicit attempts. Selection is sequential today; future concurrency must share its allowance. No alternate-provider substitution. |
| Graft traces are plausible but incomplete | Code-backed feature cards, source SHA/range/symbol validation, manifest checks, explicit missing coverage, and selected-feature source test evidence. Reject unsupported slices. |
| Source is too large | Inventory/code-window caps, at most eight goal-relevant features and 15 files per feature, and a bounded source scan. Keep the Scrapy source scope focused; do not promise exhaustive framework understanding. |
| Shared language masks framework mismatch | Score execution/API compatibility separately. Adapt a small retry policy into HTTPX; reject bringing over Scrapy middleware, scheduler, or Twisted dependencies. |
| Matcher picks something already implemented | Target evidence plus original baseline and feature-specific red test. A test already green invalidates the claimed gap. Show the result and stop without modifying a second feature. |
| Patch grows beyond one isolated feature | Enforce file/line/dependency budgets and lock feature ID. No framework migration or extra cleanup. Return a rejected patch with the reason. |
| False red-to-green evidence | Baseline must pass first. Require meaningful assertion red, unchanged test hashes/runner, identical acceptance suite on final snapshot, and a green regression suite. Skips/xfails and environmental failures do not satisfy acceptance. |
| Tests are written to the implementation | Verifier prepares/fixes the contract before Implementer writes production code. Freeze tests; include negative and exhaustion cases. Acceptance behavior comes from source references and the user goal. |
| Repair loops consume the schedule | Three production-only repair rounds, minimal failure packets, fixed per-test timeouts and run budget. Final failure is useful evidence, never a reason for unlimited retries. |
| Prompt injection or unsafe repository paths | Treat README/comments/configs as untrusted data, not instructions. No source execution. Resolve/contain paths, reject symlinks and traversal, restrict tools, and keep test execution in a credential-free container. |
| Repo/tests access host secrets or network | No host execution, no host home/.git/socket mounts, explicit environment allowlist, network disabled, resource/time limits, and a prepared dependency image. Keep the MVP local rather than exposing an arbitrary-code service. |
| Dirty target or interrupted run damages work | Clone committed HEAD into managed storage; edit a new worktree only. Report excluded uncommitted changes. Mark interrupted jobs and rerun from the pinned base. No automatic merge/push. |
| Unknown dependencies/services prevent tests | Support the prepared Python/HTTPX/pytest target profile only. Return unsupported rather than improvising a package install, browser stack, database, or network-enabled runner. |
| Source-use permissions or competition reuse are unclear | Preserve Graft's MIT notice and Scrapy's audited [BSD-3-Clause license](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/LICENSE)/notices as applicable. Gate unknown licenses. Check participant rules and disclose prior Graft code before reuse. |
| UI work crowds out the core demonstration | Keep CLI first and a single browser run view. Cut graphs, PR integration, extra targets, and streaming upgrades before touching the terminal outcome. |
| Live demo timing is unpredictable | Pin SHAs, warm the runner and clones, rehearse from a fresh worktree, and keep a labeled recording of a real run. Report observed durations; do not promise the entire pipeline finishes within the speaking slot. |

## 11. Milestone and audit reference

[Current implementation](docs/full-feature-development.md#1-current-implementation),
[audit closure](docs/full-feature-development.md#2-consolidated-audit-findings) and
[dated verification](docs/full-feature-development.md#3-verification-and-provenance)
are maintained together in the full feature development document. Add future status
changes there rather than maintaining a second milestone or audit table here.
