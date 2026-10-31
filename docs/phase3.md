# Phase 3: pinned snapshots and the first source trace

Implemented task 3 in section 11 of the approved breakdown. The CLI is `graft-source-trace`.
This milestone produces source evidence for a bounded retry slice. Feature selection, crawler
retry adaptation, final red-to-green verification, and the UI remain later work.

## Run

From the repository root, with Python 3.12, the locked backend environment, Git, network access,
and the existing `MODEL_API_KEY` configuration:

```powershell
uv run --project backend --locked graft-source-trace
```

Defaults are public Scrapy at its latest default-branch commit and the reviewed
`crawler-demo` registration. `--source-url`, `--source-sha`, `--target`, and `--goal` are explicit
CLI arguments. Source URLs must be public HTTPS GitHub repository URLs without credentials;
`--source-sha` is optional: omitting it resolves the default branch's HEAD once when the
trace starts, then fetches and records that exact commit. Explicit revisions must be full
commit SHAs. To reproduce the original reviewed snapshot, pass
`--source-sha 5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac`.
The tracer is intentionally focused on Scrapy retry code,
settings, and source tests. Other stacks and arbitrary local target paths are unsupported.

The crawler is a subdirectory of this monorepo, so target provenance records both the actual
monorepo commit SHA and the `examples/crawler-target` prefix. Only the three registered fixture
files enter the target inventory and worktree. Dirty working files are excluded: the current
committed target can therefore differ from local fixes verified by `graft-crawler-baseline`.
Commit target changes when you intend them to become the next pinned input.

## Repository boundary

The source is fetched by exact SHA into a bare managed repository without checking out or
executing its files. Local targets are cloned with independent object storage, without local
hard links or alternates. Worktrees link only to that managed clone. Sparse checkout preserves
the full base index while materializing approved target files. A successful trace requires a
clean worktree, matching file hashes, unchanged source object integrity, and unchanged original
target HEAD/status/diff. There is no automatic commit, merge, push, or retention cleanup.

Git runs with argument vectors and a minimal environment. System/global Git configuration,
credential helpers, hooks, fsmonitor, submodule recursion, and optional original-index writes
are disabled. Templates are empty. The managed clone has no repository-defined filter commands.
Only HTTPS retrieval and the registered local clone transport are enabled. API keys are never
passed to Git or target processes.

Inventories come from Git tree/blob objects. They retain UTF-8 text hashes, byte sizes,
languages, exclusions, and reached limits. Symlinks, submodules, secret-like paths, binaries,
dependency/build directories, oversized files, and nonportable paths are excluded. Root and
ancestor links/junctions are rejected before resolving approved filesystem roots.

The baseline Docker runner still exports only reviewed files and remains the only path that
imports/tests target code. A managed worktree itself is not an execution sandbox.

## Evidence and model boundary

The Meta adapter owns transport, sanitized metadata, and provider validation. The smoke
workflow and its token tool live in `smoke.py`; source tool dispatch lives in `tracer.py`.
The source tracer has only `read_range`, `python_evidence`, and `search_symbols`. It has no shell,
file-write, dependency-install, or target-execution tool. Repository text is untrusted data.

Every reference must match the pinned repository/commit and an inventoried text file. Cited
lines must exist and have been read. Named symbols must match Python AST definitions,
assignments, or imports, with citations contained in their ranges. Dependency classifications
are checked against Python's standard-library module set and cited imports/manifests. Supported
cards must retain direct runtime source coupling, including Scrapy itself; the intended small
HTTPX adaptation does not import that framework.

The controller derives coverage from reads and inventory. Partial/unsupported cards cannot pass
the retry trace gate. Static provenance checks do not establish semantic correctness, complete
transitive dependencies, a runtime call graph, or executed upstream test results. Source tests
are not executed in this milestone. Source license identification is model-derived and checked
for citation provenance; the complete inventoried notice is carried into the bundle.

Limits are 4,000 inventory entries, 50 MiB analyzed text, 2 MiB per input file, 200 lines and
24,000 UTF-8 bytes per read window, and 500,000 cumulative read bytes. The first trace permits
one feature, 15 evidence files, and 16 model turns. Symbol search results cap at 80 with visible
truncation. Search is scoped to the declared retry focus files. Coverage retains unexamined
paths and does not claim a complete feature catalog.

All inference uses configured Meta Muse Spark, with medium reasoning for tracing and no
provider/model fallback. `META_TRACE_MAX_COMPLETION_TOKENS` defaults to 8,000; the smoke flow
uses `META_MAX_COMPLETION_TOKENS`, default 4,096. Neither stage can exceed 8,000 output tokens.
SDK retries are disabled. The adapter counts each bounded retry in the same budget: 32 HTTP
attempts, 400,000 returned input tokens, 40,000 returned output tokens, and 20 minutes checked
at model request boundaries. Returned usage is accounted even when generated JSON, model
identity, or completion status fails validation. Unavailable usage on HTTP/transport failure
remains null. Token checks use returned usage and stop subsequent requests on exhaustion;
they are not a preflight tokenizer or a provider billing guarantee. Git and model operations
also have their own timeouts.

Strict schemas are checked before sending; defaulted or unrestricted objects are rejected.
Locked contract collections are immutable and serialize as JSON arrays.

## Artifacts

Each run creates a unique ignored `data/artifacts/source-trace-<id>/` directory:

| Artifact | Meaning |
|---|---|
| `report.json` | Success/failure, pinned snapshots, preservation checks, branch/worktree, hashes, model calls, HTTP attempts, tool events, and token totals |
| `source-trace.json` | Controller envelope with model/response provenance and canonical content SHA-256 around validated trace data |
| `feature-card.json` | Bounded retry feature, source references, dependencies, acceptance behaviors, and exclusions |
| `trace-bundle.json` | Card, hashed cited excerpts, complete notices, Python evidence, and controller-derived coverage |
| `evidence-reads.json` | Successful line-window reads and hashes, also retained on trace failure |
| `repositories/*-inventory.json` | Pinned source/target text inventories and exclusions |
| `repositories/source.git`, `target.git`, `worktree/` | Managed Git storage and isolated target worktree |

Model failures exit 1 and retain available sanitized call/attempt metadata. Truncated output
does not count as a supported trace. The CLI prints the receipt path for review.

## Audit resolutions and verification

The [consolidated audit record](full-feature-development.md#phase-1-and-phase-2)
preserves the original findings and their closure evidence. The existing setup
overwrite fix is preserved. Phase 3 resolves the remaining Phase 1 findings: smoke/client
ownership, failed-generation metadata and retry accounting, strict-schema compatibility, and
mutable plans. It also resolves all three Phase 2 findings: owned stream error propagation and
required log artifacts; canonical crawler origin/visit identity; and linked root/ancestor
rejection. The crawler still makes one fetch attempt.

```powershell
uv run --project backend --locked pytest backend/tests
uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts sandbox examples
uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts sandbox examples
uv run --project backend --locked graft-meta-smoke
uv run --project backend --locked graft-crawler-baseline
uv run --project backend --locked python scripts/check_runner.py
uv run --project backend --locked python scripts/check_capture_faults.py
```

The Docker baseline requires 6 isolation checks and 13 crawler tests green, plus exactly four
retry assertion failures and three acceptance controls green. Runner failure probes cover
collection, skips, per-test timeout, container timeout, and excessive output. Capture probes
inject open/write/read failures for each stream through the real baseline gate. They require
an error capture, a specific stream I/O diagnostic, and an unsuccessful overall run. A
controller-side probe chunk ensures the ordinarily empty stderr write path is exercised.

Backend tests use mocked Meta responses and real disposable Git repositories; repository
contents are never imported/executed on the host. A Windows identity unable to reopen pytest's
restricted temporary directories needs normal local execution permissions and a fresh
`--basetemp=data/pytest-<unique-id>` directory. Never substitute host execution for the Docker
target runner.

Primary references: [Meta structured outputs](https://dev.meta.ai/docs/structured-output),
[Meta tools](https://dev.meta.ai/docs/tool-calling), [Git clone](https://git-scm.com/docs/git-clone),
[Git worktrees](https://git-scm.com/docs/git-worktree), and
[Git sparse checkout](https://git-scm.com/docs/git-sparse-checkout).
