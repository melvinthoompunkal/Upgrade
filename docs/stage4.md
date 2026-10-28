# Stage 4A: target profiling and feature selection

The selection command analyzes **one** independently traced Scrapy retry slice against
the registered Python/HTTPX crawler. It produces an evidence-backed `selected` or
`no_safe_fit` result. It does not implement the selected feature. The optional 4B
comparison of depth and duplicate-filter slices remains pending.

```powershell
uv sync --project backend --locked
uv run --project backend --locked graft-fit-select --target crawler-demo --goal "Improve resilience to transient HTTP failures"
```

Requires the configured Meta key, network access for public source retrieval and Meta,
and the prepared Docker image receipt at `data/runner-image.json`. See the
[baseline setup](../sandbox/README.md). `--image-receipt` can select another reviewed
receipt. Source URL/SHA defaults match `graft-source-trace`; unrecognized source revisions
can be traced but cannot pass the registered retry acceptance/source-use policy.
Only registered target IDs are accepted; arbitrary host target paths and historical
receipt imports are unsupported.

The controller sends bounded public source excerpts, the three registered target files,
the goal, and test case summaries to the configured Meta endpoint. It excludes `.env`
and credential files. Source/target code is never imported or executed on the host.

The command clones committed target HEAD into independent storage, creates a clean sparse
managed worktree, and exports the exact approved Git blobs. Uncommitted target changes
are excluded and disclosed. It parses TOML and Python AST as data, requires the reviewed
Python requirement and HTTPX dependency, and runs only the existing fixed Docker suites.
Unknown declarations/imports require an explicit runner extension.

Isolation and ordinary baseline must pass all registered cases. The separate frozen retry
diagnostic must establish the known 503 assertion failures, or pass all seven cases as
`already_present`. A green diagnostic removes the old retry gap. It does not trigger
another candidate or manufacture red evidence. Other failures remain failures.

The read-only source tracer and Target Profiler use medium reasoning. The Fit Matcher
uses high reasoning, with at most one clarification round of eight bounded reads.
All model stages share one `MetaClient`, budget, attempt ledger, and usage allowance:
32 HTTP attempts, 400,000 returned input tokens, 40,000 returned output tokens, and
1,200 seconds checked at request boundaries. Analysis is sequential. These are returned
usage limits, not a hard billing guarantee for a request already in flight.

Python validates repository/SHA/path/range/symbol citations against actual read windows,
binds observed retry gaps to their same-snapshot capture/case outcomes, and computes:

```text
total = 7*gap + 5*isolation + 3*dependency + 3*stack + 2*testability
```

Eligibility requires total >=75, gap/isolation/stack/testability each >=3, and all ten
controller gates: source/target provenance, supported execution, passing baseline,
demonstrable gap, isolated proposed scope, existing dependencies, recognized source
notices, registered acceptance readiness, and sufficient slice coverage. Missing evidence
does not pass. Eligible candidates sort by descending total, isolation, testability,
dependency rating, then ascending ID. `top3` is never padded with rejected candidates.

Scope gates assess a proposal within five production files, one new production file,
and 400 changed production lines. Dependencies require explicit evidence of existing
HTTPX/stdlib use; an empty dependency list is insufficient. Source coupling stays on the
source card. Only the exact registered Scrapy BSD-3-Clause notice is recognized by the
versioned source-use policy, and its complete text is exported. Changes fail closed.

`selected` and `no_safe_fit` exit 0; `failed` exits 1 with a specific error code.
`no_safe_fit` means analysis completed without an eligible feature. It does not mean the
goal was implemented. High ratings never override a failed gate.

Each command prints a fresh `data/artifacts/fit-select-<id>/report.json` location. The
adjacent `report.md` explains actual counts, profile, rating breakdown, failed gates,
selection/rejections, and scope. The JSON report records model calls, attempts, usage,
versions, source/target identities, prefix, controller hashes, preservation checks,
available artifact digests, and explicitly unavailable outputs.

Artifacts include source trace/catalog and per-feature bundles; complete notices;
target profile and read windows; W1 baseline binding/report with complete captures;
matcher assessments; gate evidence; and deterministic fit results. Derived JSON artifacts
wrap `data` in an envelope containing run/source/target binding and a canonical content
hash. `baseline-binding.json` preserves the W1 format and is converted to immutable typed
data internally. Artifacts are atomically published, then hashed; the final manifest
advertises only intact files. `report.json` is the final manifest and does not hash itself.
The target profile references its frozen read log; a final read log also retains reads
and denials from failed matcher clarification.

Coverage describes bounded static reads plus captured target tests. An examined path may
contain unread lines. AST metadata is not a read window; static citations do not establish
general semantic correctness. Source tests are cited but are not executed. Page limits
are distinct from depth limits; equivalent duplicate suppression already exists.

The original target and managed worktree must remain unchanged. Both
`actual_patch_scope_checked` and `post_implementation_verified` stay false. Patch generation,
frozen expanded acceptance contracts, red-to-green implementation, repair loops, API jobs,
and UI remain later milestones. Existing baseline/source-trace commands remain available.

Development checks:

```powershell
uv run --project backend --locked pytest backend/tests --basetemp=data/test-runs/stage4-<unique-id>
uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts sandbox examples
uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts sandbox examples
```

Backend tests use mocked Meta/Docker with disposable real Git repositories. Live
completion additionally requires a fresh command run and inspection of its artifact
hashes, snapshot bindings, captured case identities, exact scores, and preservation checks.

Fresh verification on October 27, 2026: **259 backend tests passed**, with Ruff lint and
format checks passing. A live Muse Spark/Docker run selected `http-retry-bounded` at
**93/100**, with all ten gates passing. Its six isolation and thirteen ordinary baseline
cases passed; the retry diagnostic retained four expected assertions and three passing
controls. Independent inspection verified 29 published artifacts and 50 citations. The
original target and actual managed worktree remained unchanged. The live run used 15 HTTP
attempts, 109,638 returned input tokens, and 23,176 returned output tokens within one shared
budget. These results verify selection; retry implementation remains subsequent work.
