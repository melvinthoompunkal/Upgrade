# Graft + Meta orchestration: MVP breakdown for approval

Research date: October 27, 2026. Planning only; no application implementation or scaffolding has been started.

**Recommendation:** build a local developer tool that takes one source repository, one target Git snapshot, and a goal; explains three candidate features; adapts the highest-ranked eligible feature into an isolated target branch; and produces a patch with recorded red-to-green tests. Reuse Graft's FastAPI and React/Vite/Tailwind foundations. Use Muse Spark through Meta Model API for planning and every agent's model calls.

The demo should use **Scrapy → a small Python/HTTPX crawler**, with bounded HTTP retries as the likely winning feature. The target crawler is a proposed fixture to create after approval; you have not supplied an existing crawler repository. Support Python targets with a known pytest runner first. General language and build-system support is outside this MVP.

## 1. Graft audit

I cloned and inspected [Graft](https://github.com/melvinthoompunkal/Graft) at commit [`a386620842c34f1f731bf6ffc91bdb1b101f31ba`](https://github.com/melvinthoompunkal/Graft/tree/a386620842c34f1f731bf6ffc91bdb1b101f31ba), including its README, primary backend, primary frontend, and separate demo backend. The clone is in `.research/Graft/`. These findings are from static code inspection; I did not run Graft or make paid inference calls.

All Graft links below point to that audited commit.

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
| Frontend | [`graft-frontend/package.json`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/package.json) pins React 18.3.1, Vite 6.0.7, and Tailwind 3.4.16. [`src/App.jsx`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/src/App.jsx) handles ingestion, manual tracing, downloads, and page navigation. | Reuse the installed stack, styling primitives, and fetch/error conventions. Replace the multipage workflow with one run view. |
| Trace display | [`src/components/CallChainTree.jsx`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/src/components/CallChainTree.jsx) renders file/function/range/role entries. [`TraceWorkspace.jsx`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-frontend/src/components/TraceWorkspace.jsx) displays trace details. | The call-chain component can be reused substantially as-is; simplify the surrounding workspace. |
| Separate demo product | [`graft-demo/backend/routers/demo.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-demo/backend/routers/demo.py) serves pre-analyzed JSON and a live analyze route. [`services/analyze_service.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-demo/backend/services/analyze_service.py) performs live Claude-based extraction and returns an empty `traces` object. | Cut the library/gallery and parallel app. Cached JSON cannot substantiate a live Meta-powered implementation. |
| Tests and isolation | The primary backend has no test runner or target-edit workflow in its routes/services. [`graft-demo/backend/test.py`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/graft-demo/backend/test.py) is a manual HTTP probe. The root [`Dockerfile`](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/Dockerfile) packages the web app. | Build test execution and execution isolation. Packaging the API in Docker does not isolate arbitrary target tests from the API. |

**Reusable as-is or with mechanical relocation:** `utils/errors.py`'s error type, Pydantic conventions, the call-chain renderer, frontend styling/build setup, and simple health/static-serving patterns. Keep Graft's [MIT notice](https://github.com/melvinthoompunkal/Graft/blob/a386620842c34f1f731bf6ffc91bdb1b101f31ba/LICENSE.md) with reused code.

**Rebuild on Meta:** all calls in `claude_service.py`, README-only extraction, candidate and synthesis prompts, and any retained logic from the demo's `analyze_service.py`. Add the planner, Target Profiler, Fit Matcher, Implementer, and Verifier/Fixer. A model-name substitution alone does not supply these missing behaviors.

**Cut:** repo-map navigation, bundle/library management pages, the second demo application, broad manifest rewriting, multilingual transplant support, and claims of complete dependency closure or guaranteed token/rate-limit avoidance. Graft currently analyzes one source; the source-to-target implementation workflow is new work.

## 2. Recommended tech stack

| Area | MVP choice | Reason and boundary |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic 2, Uvicorn | Matches Graft and the proposed target. Reuse compatible dependencies and lock the actual validated versions. |
| Frontend | React 18 + existing Vite/Tailwind setup | One form and one run view. No router or UI-library migration. |
| Models | Meta Model API, `muse-spark-1.3`, Standard mode | One model across the five functions and planner reduces integration risk and makes Meta central to the result. |
| API client | Python `AsyncOpenAI` configured for Meta's endpoint | SDK transport only; all inference goes to Meta. Use Chat Completions for the MVP's explicit message history, function tools, and JSON Schema outputs. |
| Orchestration | Small Python state machine and typed function calls | No LangGraph, CrewAI, AutoGen, MCP server, or autonomous agent-manager framework needed. |
| Repository handling | Git CLI subprocesses, isolated clones, target worktree | SHA-pinned evidence, reversible branch output, and a readable patch. Avoid another Git abstraction layer in new code. |
| Code indexing | Python `ast`, manifests, bounded text search/read tools | Enough for a Python-first demo. No vector database, embeddings, graph database, or language server. |
| Execution | Separate disposable Linux Docker containers, using Docker Desktop/WSL2 locally | Keep model/API credentials outside test processes. Use a prepared Python/HTTPX/pytest image. |
| Tests | Target's existing pytest suite plus frozen acceptance tests | Focused regression evidence and a recorded red-to-green change. Port source test behavior to the target API. |
| Storage | SQLite via standard-library `sqlite3`; artifacts on local disk | One user and one active run do not justify Postgres. JSON files alone make ordered events/status updates harder to manage. |
| Jobs | One lifespan-managed worker, bounded `asyncio.Queue`, model semaphore of 2 | One active run and one waiting run. Return 202 on enqueue and 429 when full. No Redis or Celery. |
| Progress | Poll ordered run events every second | Simpler than WebSockets/SSE for a local demo; events survive page refresh. |
| Delivery | Target worktree branch, `changes.patch`, `report.md`, test logs, provenance JSON | A PR is optional. Verified terminal output and a patch are the required deliverables. |

Meta's current documentation lists `muse-spark-1.3`, the base URL `https://api.meta.ai/v1`, bearer authentication through `MODEL_API_KEY`, and SDK compatibility. It also documents function tools and schema-constrained JSON. These are verified integration capabilities, not a claim that your account/key has already been tested. [Meta overview](https://dev.meta.ai/docs/overview), [models](https://dev.meta.ai/docs/models), [tool calling](https://dev.meta.ai/docs/tool-calling), [structured output](https://dev.meta.ai/docs/structured-output).

The first post-approval integration check must perform a real authenticated request, validate a small schema, and round-trip one harmless tool. Keep the model configurable, but pin the validated ID for the submission. If 1.3 is unavailable to the account, explicitly select an accessible documented Muse Spark version and rerun the check. Never silently substitute another provider. Muse Code may help during development; embedding its CLI inside this application is unnecessary.

**Clone/worktree approach:** a network-enabled retrieval step shallow-clones the public source and target and records their exact commit SHAs. For a registered local target, clone its committed HEAD into a managed cache with independent Git storage; do not create linked worktrees against the developer's original `.git`. Create a `graft/<run-id>` worktree from the managed target clone. Uncommitted local changes are excluded and disclosed. No push or merge is part of Run. [Git worktrees](https://git-scm.com/docs/git-worktree).

**Execution boundary:** a worktree separates edits; it does not restrict processes. Only the runner executes target code. It receives an exported snapshot without `.git`, source repo, host home, Docker socket, or API keys. Run as non-root with a read-only root filesystem, dropped capabilities, no-new-privileges, writable temporary/result directories, 2 CPUs, 1 GB memory, a PID cap, and timeouts. Use `--network none`; acceptance tests use in-process HTTP fixtures. The controller retains Git and model access outside this container. [Docker runtime options](https://docs.docker.com/engine/containers/run/), [network isolation](https://docs.docker.com/engine/network/drivers/none/).

For the demo, bake approved target dependencies into a pinned runner image in advance. Do not run a repository's Dockerfile or install arbitrary dependencies on the host. Targets needing unknown packages, services, or network tests return `unsupported_target`; a general-purpose dependency installer is cut.

**Persistence:** use `runs`, `events`, `artifacts`, and `model_calls` tables. Store large traces, patches, stdout/stderr, and JUnit XML on disk, referenced by path and SHA-256. Persist each state transition before announcing it. On restart, mark a previously running job `interrupted`; start a fresh run from pinned inputs instead of replaying a possibly applied patch. Cleanup skips active runs and preserves exported demo evidence.

## 3. MVP feature list

### Must-have — the demo fails without these

- One public source GitHub URL, one public target URL or registered local Git target, and one-line goal.
- Commit-pinned inventories, stack detection, and a known target runner; report unsupported inputs before editing.
- A bounded source feature catalog with code/test citations and explicit analysis coverage. Analyze at most eight goal-relevant features per run; disclose unexamined areas.
- Target architecture, existing capabilities, gaps, style evidence, and verified baseline test status.
- A scored top three, rejected candidates with reasons, and selection of exactly the highest-ranked eligible feature.
- A detailed trace bundle and target adaptation contract for that one feature.
- Independent acceptance tests recorded failing against the original target for the expected behavior.
- A small adapted production patch in the target worktree: at most five production files, one new production file, 400 changed production lines, and zero new runtime dependencies.
- Focused acceptance tests and the existing target suite passing after implementation; at most three repair rounds.
- Real Meta planner/agent calls recorded with provider, returned model ID, response ID, usage, stage, and elapsed time. Display short decisions and tool events, without exposing private reasoning.
- One run view showing progress, evidence, ranking, diff, and test outcome; the same event log available in the terminal.
- Exportable patch, provenance, source notices where needed, and test/report artifacts. An honest failure outcome when verification cannot succeed.

### Should-have — only after the complete workflow works

- SSE instead of polling, cancellation, and finer code-window navigation.
- Source-analysis cache keyed by source SHA, prompt/schema version, and model ID; label cached stages.
- One additional Python demo target to check that integration paths are discovered rather than hard-coded.
- Optional user-triggered PR creation after reviewing the verified local result.
- Side-by-side source behavior and adapted target implementation in the run view.

### Cut — explicitly outside scope

- More than one source, more than one target, or implementation of feature #2/#3 in the same run.
- Generic cross-language ports, framework migrations, dependency upgrades, large refactors, database migrations, or new external services.
- Account systems, teams, hosted arbitrary-code execution, private-repo credential flows, deployment automation, auto-merge, and production rollouts.
- Dashboards as the main product, feature galleries, basic RAG/chat wrappers, Streamlit, graph visualization polish, and analytics pages.
- Persona agents, agent debates, recursive delegation, unlimited repair, self-hosted models, model training, and broad benchmark infrastructure.
- Claiming success from an LLM review, fabricated logs, skipped tests, or a cached green result.

## 4. Agent breakdown and contracts

These are functions with constrained tools, not simulated job titles. They share no mutable conversation history. Each consumes typed artifacts and produces a validated artifact.

The following are **contract specifications**, not implementation code. `?` means optional; `null` is explicit absence. Every object rejects unknown fields in its eventual Pydantic/JSON Schema definition. Paths are repository-relative, normalized, and checked against their pinned snapshot. Artifact IDs resolve through the controller and are not arbitrary host paths.

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
  agent: string, model_id: string, response_id: string,
  created_at: string, content_sha256: string, data: T
}
```

The controller supplies envelope metadata and hashes after validation. The model supplies `data`. Citation existence, valid line bounds, and an AST symbol match are necessary evidence checks; they do not prove semantic correctness. Tests supply behavioral evidence.

### Tracer

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
  coverage: Coverage
}
```

**Access:** read target inventory, manifests, source/tests, and runner-captured baseline results. It may request the registered baseline runner through the controller; it cannot submit arbitrary commands or edit files. The runner does not execute on the API host.

**Model:** `muse-spark-1.3`, medium reasoning. Architecture/style inference and gap assessment need code context, while dependency/version extraction is deterministic where possible.

**Verification:** check manifests and cited symbols against actual files. Missing search hits yield a provisional gap, not a proven absence. Baseline test failure or an unsupported runner blocks implementation. A feature already present is rejected or explicitly narrowed to a demonstrable missing behavior.

### Fit Matcher

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
  eligible: boolean, total: number 0..100
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

### Implementer

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

This function runs **before implementation** to define and record the red test, then after implementation to verify and, if necessary, repair production code.

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
TestCapture = {
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
  captures: TestCapture[], repairs_used: integer 0..3,
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

Muse Spark also makes the initial planning call. Its constrained `RunPlan` is:

```text
{
  source_focus_paths: string[], target_focus_paths: string[],
  feature_focus: string[],
  analysis_tasks: ("trace_source" | "profile_target")[],
  rationale: string
}
```

The controller validates these paths/tasks and requires exactly one `trace_source` and one `profile_target` task. It dispatches Tracer and Target Profiler independently, with at most two model calls in flight. After their outputs validate, it dispatches Matcher, prepares acceptance tests, dispatches Implementer, and runs verification/repair. The model plans analysis and makes feature/adaptation decisions; deterministic code enforces sequence, permissions, budgets, and success conditions. No additional persona or recursive delegation is needed. `acceptance_sha256` is null only for the original baseline capture, before acceptance tests exist.

Initial run limits: eight catalog features, 15 evidence files per feature, 4,000 inventory entries, 50 MB analyzed text, bounded line-window reads, 32 model requests including retries, 400,000 cumulative input tokens, 40,000 output tokens, and 20 minutes wall time. Individual model outputs cap at 8,000 tokens; total limits still apply. Stop with a visible budget/coverage result rather than silently omitting evidence. These are starting engineering limits to tune during rehearsal, not measured performance claims.

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

**Illustrative scores for the proposed crawler fixture, not actual agent output:**

| Candidate | Gap | Isolation | Dependency | Stack | Tests | Total | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Bounded retry of transient HTTP failures | 5 | 4 | 5 | 4 | 5 | **92** | #1: unreliable fetches are central to this demo goal; adapt the policy at `fetch` |
| Crawl depth limit | 4 | 5 | 5 | 4 | 5 | **90** | #2: compact frontier control, less directly related to fetch reliability |
| In-memory duplicate-request suppression | 4 | 4 | 5 | 4 | 5 | **85** | #3: useful, but needs request identity/frontier decisions |
| Full scheduler/engine integration | — | — | — | — | — | — | Reject: coupled framework integration, exceeds isolation/dependency limits |
| AutoThrottle transplant | — | — | — | — | — | — | Reject the full transplant: depends on downloader-slot/latency integration absent from the fixture |

For the 92-point example, the explanation must connect source `RetryMiddleware.process_response`/`get_retry_request` and source retry tests to the target's one-attempt `fetch` path and a reproduced transient-503 failure. Dependency cost is 5 only if the resulting patch uses HTTPX already present in the target. Stack is 4 because Scrapy middleware/request objects differ from HTTPX. The actual profiler and matcher must confirm these assumptions after the fixture exists; the demo must not hard-code this ranking.

## 6. End-to-end data flow

1. **Input:** submit source URL, target URL or registered local target ID, and goal. Local registration is a developer-side action under a configured workspace; the browser cannot browse arbitrary host paths.
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

Proposed persisted states:

```text
queued → preparing → baseline → planning → analyzing → matching
       → acceptance_red → implementing → verifying ↔ fixing
       → verified | failed | no_safe_fit | unsupported_target | interrupted
```

Every event includes `{sequence,run_id,timestamp,stage,kind,message,artifact_ids}`. Polling resumes from sequence. `verified` is set only by the controller after all required executed tests pass, test hashes match, scope gates pass, and at least one acceptance case has valid red evidence. A green run with no valid red is incomplete.

## 7. Concrete crawler demo plan

### Compatibility check before choosing the pair

OpenClaw's inspected [`package.json`](https://github.com/openclaw/openclaw/blob/main/package.json) describes a multi-channel AI gateway and declares Node/pnpm/TypeScript tooling. Its [`tsconfig.json`](https://github.com/openclaw/openclaw/blob/main/tsconfig.json) configures TypeScript with NodeNext and Node types. It is a TypeScript/Node source, not a Python crawler framework.

Your actual target crawler's stack is unknown because no target repo was provided. Therefore an OpenClaw→your-target compatibility judgment is not yet possible. Against the proposed Python target, it is a language/runtime mismatch and is rejected by this MVP's language gate. Even with a TypeScript target, feature-level dependency/isolation checks would still be required.

**Recommended demo pair:**

- **Source:** [`scrapy/scrapy`](https://github.com/scrapy/scrapy), inspected clone commit [`5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac`](https://github.com/scrapy/scrapy/tree/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac), stored locally in `.research/Scrapy/`.
- **Target:** proposed `examples/crawler-target/`, a small Git repository created after approval: Python 3.12, HTTPX, pytest, a command-line entry point, HTML link traversal, a page cap, and an injected HTTPX client for tests. Baseline fetches make one attempt and already have passing fetch/parser/frontier tests. It deliberately lacks bounded retry, depth limiting, and duplicate-request suppression so three real candidates can be assessed. This is a declared demo fixture, not a claim about an existing user project.
- **Goal:** “Make my crawler more reliable when a site returns temporary HTTP errors, while keeping it lightweight.”

Scrapy's audited [`pyproject.toml`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/pyproject.toml) establishes Python ≥3.11 and substantial framework dependencies including Twisted. The shared language makes a small behavioral adaptation plausible; it does not make a direct middleware transplant compatible. The target must keep HTTPX and acquire none of Scrapy's runtime stack.

### Real source evidence to show

| Feature | Inspected implementation | Inspected source tests / qualification |
|---|---|---|
| Retry policy | [`scrapy/downloadermiddlewares/retry.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/downloadermiddlewares/retry.py): `RetryMiddleware.process_response`, `_retry`, `get_retry_request`. [`scrapy/settings/default_settings.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/settings/default_settings.py) sets `RETRY_TIMES=2` and retryable status codes. | [`tests/test_downloadermiddleware_retry.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/tests/test_downloadermiddleware_retry.py): `TestRetry.test_503`, `test_404`, and `TestMaxRetryTimes.test_with_settings_zero`. |
| Depth limit | [`scrapy/spidermiddlewares/depth.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/spidermiddlewares/depth.py): `DepthMiddleware.get_processed_request`. | [`tests/test_spidermiddleware_depth.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/tests/test_spidermiddleware_depth.py): `test_process_spider_output`. |
| Duplicate suppression | [`scrapy/dupefilters.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/dupefilters.py): `RFPDupeFilter.request_seen`; [`scrapy/utils/request.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/utils/request.py) handles fingerprinting. | [`tests/test_dupefilters.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/tests/test_dupefilters.py): `DupeFilterTestMixin.test_filter`. URL canonicalization relies on **w3lib**; do not attribute its implementation to Scrapy or quietly import its dependency graph. |
| Rejected full integrations | [`scrapy/core/scheduler.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/core/scheduler.py), [`scrapy/core/engine.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/core/engine.py), [`scrapy/extensions/throttle.py`](https://github.com/scrapy/scrapy/blob/5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac/scrapy/extensions/throttle.py). | Show framework coupling as the rejection reason; do not suggest these are absent from the source. |

For the winning retry slice, adapt status selection and bounded attempt counting into the target's existing GET fetch method. Keep the target's HTTPX exception/result conventions. Exclude Scrapy request cloning, priority scheduling, crawler stats, arbitrary callbacks, exception taxonomy, persistent retry queues, exponential backoff, and Retry-After handling. The inspected source retry module does not justify presenting those last two as already implemented behavior.

### Proposed 3–4 minute script

1. **0:00–0:25 — establish the problem.** Show the small target's existing suite passing and a fixture that returns `503, 503, 200`. Its current fetch fails after the first response. Explain the goal in one sentence.
2. **0:25–0:50 — start a real run.** Enter the Scrapy URL, target ID, and goal. Show commit SHAs and a live Meta planner event. Source can be pre-cloned for latency, but model calls and target tests are real and current.
3. **0:50–1:30 — show judgment.** Open the top-three result and one rejection. Explain why retry wins and why bringing over the full scheduler or Twisted stack fails the isolation gate. Open the actual retry implementation/test citations.
4. **1:30–2:00 — show red evidence.** The verifier's frozen test calls the target's current fetch API with an HTTPX `MockTransport` fixture. Capture an HTTPX status exception as an observed fetch outcome, then assert successful recovery and the expected response body. The assertion fails because the target stops at the initial 503. Show the command, assertion, exit code, and test artifact hash.
5. **2:00–3:00 — show adaptation and green.** Show the generated diff at the target fetch path. Run the same acceptance tests: transient errors recover, permanent 404 is attempted once, and persistent 503 stops after three total attempts. The existing suite also passes. Display actual result counts captured at runtime.
6. **3:00–3:30 — inspect the deliverable.** Show patch/report paths, unchanged dependency manifest, source provenance, and the recorded Meta model-call IDs. Conclude with the working crawler behavior and local branch ready for review.

HTTPX documents `MockTransport` for predetermined responses without external requests. This lets the crawler's real fetch logic run offline in the restricted container. [HTTPX transports](https://www.python-httpx.org/advanced/transports/).

Prepare cases for recovery after one/two transient responses, exhaustion, immediate success, permanent 404, body parsing after recovery, and unchanged traversal behavior. Counts and timings in the video must be actual captures; none exist yet. If live inference exceeds the presentation window, use a clearly identified continuous recording of a real run, with the app available for inspection. A saved run is labeled recorded, never live.

The official event page confirms **November 4, 2026 at 9:00 PM ET**. On that date New York is on EST; the deadline is November 5 at 02:00 UTC. The fetched official public event pages did not expose the detailed judging/restricted-project rules, and the linked waiver returned only a loading shell. Accordingly, the approximately 25% tooling weight and dashboard/RAG/Streamlit exclusions are retained as **your supplied constraints**, not independently verified official findings. On October 28, check the accepted-participant rules for those details, permitted pre-existing Graft reuse, and the exact submission/video requirements. [Official Meta event page](https://dev.meta.ai/events/global-hackathon), [organizer event page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon), [linked waiver](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver).

## 8. Proposed folder/repo structure

Use a new project directory/repository to keep the hackathon additions identifiable. Copy only the audited reusable Graft parts after approval and retain their notice. This tree is a proposal; these application folders have not been created.

```text
graft-meta/
  README.md
  LICENSE
  THIRD_PARTY_NOTICES.md
  tech_breakdown.md
  backend/
    pyproject.toml
    <validated dependency lock>
    app/
      main.py
      api/runs.py
      contracts.py
      config.py
      orchestration/
        controller.py
        planner.py
        worker.py
      agents/
        tracer.py
        target_profiler.py
        fit_matcher.py
        implementer.py
        verifier_fixer.py
      models/meta_client.py
      tools/
        repositories.py
        inventory.py
        python_index.py
        evidence.py
        patching.py
        runner.py
      storage.py
    tests/
      test_scope_and_paths.py
      test_ranking.py
      test_verification_gates.py
  frontend/
    package.json
    package-lock.json
    vite.config.js
    tailwind.config.js
    src/
      App.jsx
      api.js
      components/
        RunForm.jsx
        RunLog.jsx
        FitResults.jsx
        TraceEvidence.jsx
        PatchAndTests.jsx
  sandbox/
    Dockerfile.python-demo
    runner-policy.json
  examples/
    crawler-target/             # separate local Git repo / pinned base fixture
  scripts/
    run_demo.py                 # thin client for the same backend workflow
  data/                        # ignored runtime data, never committed
    runs.sqlite3
    repositories/
    worktrees/<run-id>/
    artifacts/<run-id>/
      source-catalog.json
      target-profile.json
      fit-result.json
      trace-bundle.json
      acceptance/
      test-captures/
      changes.patch
      provenance.json
      report.md
```

Keep files such as `storage.py` and `contracts.py` consolidated initially. Split them only when actual complexity warrants it. The acceptance artifact directory sits outside the implementation worktree so production patch tools cannot edit it.

## 9. Day-by-day build plan: October 28–November 4

Budget approximately 43 focused hours. The first end-to-end terminal run is due **October 29**, before UI work. Scope expansion stops if that milestone slips.

| Date, Eastern Time | Work in priority order | Required end-of-day result / cut |
|---|---|---|
| **Wed Oct 28 — 7h** | Check participant rules and pre-existing-code disclosure. Verify the real Meta schema/tool call. Create the small crawler baseline and pinned offline runner. Add repository snapshots, safe read/patch tools, and the first minimal run contracts. | Real Muse Spark response captured; target baseline passes inside the sandbox. If Docker/API access fails, resolve it today; do not build UI around a mock. |
| **Thu Oct 29 — 7h** | Connect narrow source tracing, target profiling, fit comparison, acceptance-red preparation, one adapted patch, and verification through the CLI. Use the real source and dynamic evidence; focus on three small candidates. | First real **source → target → red → patch → green** run using all five functions and the planner. It can have rough logs and a narrow source scan. If incomplete, freeze all optional work. |
| **Fri Oct 30 — 6h** | Expand discovery to the bounded eight-feature catalog, improve citations/dependency evidence, finalize weights/gates, add bad-fit rejections, freeze tests/hashes, and implement the three-round repair cap. | Complete MVP decision/verification contract. Meaningful checks cover score arithmetic, invalid paths, altered tests, and capped repair. A forced failing patch exercises one repair round. |
| **Sat Oct 31 — 6h** | Add SQLite records, queued worker, interruption handling, run/event endpoints, and artifact exports. Reuse the minimal React form/log and trace renderer. | A browser can start and observe the same working workflow and refresh without losing evidence. Cut SSE, cancellation, and PR integration first. |
| **Sun Nov 1 — 5h** | Show top three/rejections, source evidence, patch, and real test captures. Exercise unsupported stack, missing tests, bad baseline, no-fit, and budget-exhaustion outcomes. | Every terminal state is understandable. Keep the screen focused on the run; no extra pages. New York clocks change today; keep schedules tied to `America/New_York`. |
| **Mon Nov 2 — 5h** | Rehearse several clean-base runs, remove hard-coded target paths, check credential/path boundaries, and try a second tiny target only if the main run is stable. Draft README, provenance, and video script. | Reproducible candidate and real captured demo evidence. Drop the second target immediately if it threatens reliability. |
| **Tue Nov 3 — 4h** | Freeze dependencies/source SHA/fixture base. Record the demo, package evidence and source notices, confirm portal requirements, and prepare the submission. | Submission-ready artifact and backup recording. No new product features. |
| **Wed Nov 4 — 3h** | Run one final clean verification, inspect video/repository links, complete the entry, and verify receipt. Fix only blocking defects. | Submit by **6:00 PM ET**, leaving three hours before the official **9:00 PM ET** deadline. |

If participant rules prohibit reusing the existing implementation, retain the concept and verified research, disclose the prior work, and implement the narrow pipeline independently during the event. Cut copied UI and convenience features to preserve the verified terminal outcome. Do not claim Graft itself was newly built during this hackathon.

## 10. Risks and failure points

| Risk | Mitigation or cut |
|---|---|
| Meta credentials, quota, or model access fails | Verify a live call on Oct 28. Record returned IDs/usage, limit concurrent calls to two, honor Retry-After on API rate limits, and bound transport retries. An unavailable API is a visible failure; no alternate-provider substitution. |
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

## 11. First three coding tasks after approval

1. **Prove the Meta adapter and contract boundary — roughly 1–2 hours.** Create the minimal backend package and settings; add an async Meta client for `muse-spark-1.3`; define `Ref`, `RepoSnapshot`, and one small plan schema. Perform a real schema response and a read-only dummy-tool round trip. Done when a terminal command prints validated output, returned model/response IDs, and usage without exposing the key. No agents or UI yet.
2. **Build the crawler baseline and offline runner — roughly 2–3 hours.** Create the declared Python/HTTPX crawler fixture with the existing public fetch API and passing ordinary tests. Prepare a pinned dependency image and registered pytest commands; capture exit code/stdout/stderr/JUnit. Use `MockTransport` to demonstrate the one-attempt transient-503 limitation. Done when ordinary baseline tests pass inside the restricted container and the added retry acceptance test fails by assertion for the missing behavior.
3. **Add pinned snapshots and the first source trace — roughly 2–3 hours.** Implement public-source and registered-target snapshotting, inventories, safe range reads, Python AST evidence, and creation of a managed target worktree. Connect the Meta Tracer to Scrapy's retry implementation/settings/tests and produce a validated `FeatureCard` plus trace bundle. Done when every cited path/range/symbol resolves at the recorded SHA, the source/original target remain unchanged, and an invalid/out-of-root path is rejected.

**Approval boundary:** approve or revise this breakdown before application coding begins. The next work starts with those three tasks.
