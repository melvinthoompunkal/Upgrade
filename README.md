# Graft Meta

Adapt one small feature from a source repository into a target project, with Muse Spark planning and code-backed red-to-green evidence. The approved design is in [tech_breakdown.md](tech_breakdown.md).

The first three coding milestones in section 11 are implemented: the Meta backend foundation,
the HTTPX crawler and offline Docker baseline, and pinned repositories with a live Meta source
trace. Stage 4A adds target profiling and deterministic selection of one bounded retry
candidate. A local React frontend and a persistent browser API now expose the existing
analysis workflows. Retry adaptation and final red-to-green verification remain later work.

The [browser workspace guide](docs/frontend.md) covers startup, workflow coverage,
verification, and remaining backend milestones. The original
[frontend and integration plan](docs/frontend-integration-plan.md) retains the broader
design and delivery criteria; it is not a completion receipt for every package.

To open the frontend, start the backend below, then run `npm ci` and `npm run dev` in
`frontend/`. Open <http://127.0.0.1:5173>. Requires Node 20.19+ or 22.12+.

The [full feature plan](docs/full-feature-development.md) now has a foundation
implementation pass: the six Stage 4 audit regressions are repaired, CLI/API execution
is mutually excluded, history search is server-side, and an optional GitHub connection
preview adds account ownership, sessions, encrypted credentials and repository indexing.
Local mode remains the default. See [GitHub setup](docs/github-setup.md) and
[the implementation receipt](audit/full-feature-foundations/report.md) for exact limits.
General discovery, private Git retrieval and verified adaptations remain pending.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). From the repository root:

```powershell
uv sync --project backend --locked
if (-not (Test-Path -LiteralPath 'backend/.env')) {
    Copy-Item -LiteralPath 'backend/.env.example' -Destination 'backend/.env'
}
```

Enter your Meta key in `backend/.env` locally, or provide `MODEL_API_KEY` through your environment. Environment variables take precedence. The `.env` file, virtual environments, caches, research clones, and runtime artifacts are ignored by Git. The example file contains no credentials.

The adapter targets `https://api.meta.ai/v1` and defaults to `muse-spark-1.3` in Standard mode. `META_MODEL_ID` may explicitly select documented Spark 1.2/1.1 if account access requires it. There is no automatic model/provider fallback. The SDK retries transient failures at most twice per request and applies a configurable timeout.

## Run the backend

```powershell
uv run --project backend --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open [health](http://127.0.0.1:8000/health) or [API docs](http://127.0.0.1:8000/docs). `model_configured` reports whether a key is present; it does **not** claim the key works or the model has been verified. The server can start without a key.

## Verify live Meta access

```powershell
uv run --project backend --locked graft-meta-smoke
```

This command makes three small model requests: validate a `RunPlan` using a synthetic inventory, request the harmless `read_probe` tool, and validate acknowledgement of a fresh token supplied only by that tool. No repositories, user files, or shell commands are accessed. It uses `tool_choice="auto"`, which Meta supports; if the model does not call the tool, the check fails explicitly.

A successful command prints JSON with `status: "passed"`, the validated plan, and each call's returned model ID, response ID, token usage, stage, and elapsed time. A missing key, API error, malformed/truncated output, unsupported tool, or incorrect token exits with status 1. Provider error bodies and credentials are excluded from diagnostic output. This is a connectivity/contract check, not the final feature implementation demo.

**Live verification:** passed on October 27, 2026 using `muse-spark-1.3`. Three authenticated requests validated a structured plan and the read-only tool round trip. Returned response IDs, token usage, and timings are recorded locally in `data/artifacts/meta-smoke.json` (ignored by Git). The plan used the declared synthetic inventory; this verifies the API foundation, not repository analysis or feature implementation. Rerun the check when credentials, model access, or adapter behavior changes.

## Verify the crawler baseline (Phase 2)

Requires Docker Desktop running Linux containers. Build the reviewed runner once (network access
is needed to obtain its pinned base image and hash-locked wheels), then run the offline check:

```powershell
uv run --project backend --locked python scripts/build_runner.py
uv run --project backend --locked graft-crawler-baseline
```

Expected result: **6 isolation checks pass, 13 baseline tests pass, and 4 of 7 retry acceptance
cases fail by assertion**. The other 3 acceptance cases pass. The CLI exits 0 only when this
exact missing-feature result is recorded and the input hashes remain unchanged. This establishes
the red baseline; retry adaptation has not been implemented yet.

Each run prints its unique `data/artifacts/crawler-baseline-<id>/report.json` path. The directory
contains exported input files, frozen acceptance tests, and per-suite `capture.json`, stdout,
stderr, and JUnit XML. The report records image identity and hashes of inputs, runner definitions,
and captures. No API key or model request is needed.

To exercise runner error handling in Docker:

```powershell
uv run --project backend --locked python scripts/check_runner.py
```

See [the runner boundary and Phase 2 scope](sandbox/README.md) for restrictions and audit follow-up.

## Trace pinned Scrapy source (Phase 3)

```powershell
uv run --project backend --locked graft-source-trace
```

This retrieves Scrapy at `5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac`, clones the registered
`crawler-demo` target's committed HEAD into independent managed Git storage, and creates a
clean sparse worktree on a new `graft/source-trace-<id>` branch. Uncommitted target changes are
excluded and disclosed. No branch is created in your original repository.

The live Muse Spark tracer reads bounded source windows and AST evidence through registered
tools. The controller validates cited SHAs, inventory membership, line bounds, symbols,
dependency imports, and license references. A successful command prints the path to
`data/artifacts/source-trace-<id>/report.json`. Adjacent artifacts include the feature card,
trace bundle with hashed excerpts and complete source notices, inventories, coverage, and a
read log. Reports retain model IDs, response IDs, usage, every HTTP attempt, tool events, and
controller hashes, including on model failure.

This command needs network and the configured Meta key; it makes real inference requests.
Tracing uses medium reasoning and `META_TRACE_MAX_COMPLETION_TOKENS` (default 8,000), separately
from the smaller smoke output allowance. It never imports source/target code, runs source tests,
or applies a patch. The separate Docker baseline remains the target execution check.
See [Phase 3 details](docs/phase3.md) for limits, artifacts, verification, and audit resolutions.

## Test the pinned target (Stage 4, W1)

```powershell
uv run --project backend --locked graft-pinned-baseline
```

This command clones the registered crawler's committed HEAD, exports its exact Git bytes,
and runs the existing offline Docker suites. It records the target SHA and monorepo prefix,
input hashes, image identity, and hash-verified captures in a unique run directory. Dirty
local changes are excluded and disclosed. The original target is checked for preservation.

The ordinary baseline must pass. The separate retry diagnostic records either the expected
missing-retry assertions or `already_present` when all frozen retry cases pass. Changes to
inputs, capture evidence, or original target fail the run. The existing `graft-crawler-baseline`
command continues to test local fixture files. See [Stage 4 W1 details](docs/stage4-w1.md).
The selection workflow below reuses this binding; patching remains subsequent work.

Stage 4 W2 now provides immutable profile, assessment, gate, score/result, and
selection-facing baseline-binding contracts. Model proposals exclude controller-owned
results; final artifacts validate snapshot identity, observed-gap links, score thresholds,
and selection consistency. See [Stage 4 W2 contracts and handoff](docs/stage4-w2.md).

## Select a feature (Stage 4A)

```powershell
uv run --project backend --locked graft-fit-select --target crawler-demo --goal "Improve resilience to transient HTTP failures"
```

This runs fresh same-snapshot Docker captures, a live Meta source trace and target profile,
and a high-reasoning fit assessment. Python validates evidence, computes scores and all
gates, and returns `selected` or `no_safe_fit` (exit 0), or a specific `failed` result
(exit 1). It analyzes **one** source feature; optional broader comparison is pending.
The command prints its unique report path with hashed artifacts and readable results.
It preserves the original target and managed worktree, and produces no patch or
post-implementation verification. See [Stage 4 usage, policy, and boundaries](docs/stage4.md).

## Development checks

```powershell
uv run --project backend --locked pytest backend/tests
uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts sandbox examples
uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts sandbox examples
```

Backend tests make no external model requests, need no API key or Docker, and never execute the
crawler on the host. They cover provider contracts and credential-safe errors, file containment,
image-receipt validation, result classification, pinned Git repositories, clean managed worktrees,
AST/citation validation, budget accounting, and reproduced audit defects. The crawler tests run through the Docker
commands above. If your environment restricts the system temporary folder, add
`--basetemp=data/pytest-<unique-id>` to the backend pytest command.

## Integration references

- [Meta quickstart](https://dev.meta.ai/docs/quickstart)
- [Chat Completions parameters](https://dev.meta.ai/docs/protocols/chat-completions)
- [Structured output and strict schema constraints](https://dev.meta.ai/docs/structured-output)
- [Tool calling and supported tool choice](https://dev.meta.ai/docs/tool-calling)

The current foundation is implemented independently. It does not copy code from the Graft research clone. Preserve upstream licenses/notices when reusing source in subsequent milestones.
