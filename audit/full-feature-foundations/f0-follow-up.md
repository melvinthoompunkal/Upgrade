# F0 foundation follow-up

October 28, 2026, America/New_York. This is a focused implementation review and
regression receipt, not an independent security audit or a full-product release.

Base commit: `bedf27e1daf824228144e0ede272dc1687ca0758`. The follow-up is an uncommitted
working-tree change. Original audit reports and the first foundation receipt are
unchanged. Verification results and implementation hashes are recorded separately
for this follow-up in [f0-follow-up-checks.json](f0-follow-up-checks.json).

## Scope and repairs

The first package in [the development plan](../../docs/full-feature-development.md)
is **F0: reconcile and repair foundations**. Its six original Stage 4 fixes were
already implemented. This pass reviewed their tests and closed additional paths
that still bypassed the same guarantees.

| Boundary | Reproduction | Implemented result |
|---|---|---|
| Optional human report | `KeyError`, `TypeError` or `RuntimeError` during Markdown rendering prevented JSON publication | Optional formatting failure produces a sanitized warning; the original outcome, intact evidence and required JSON remain available |
| Source trace publication | A later sidecar write lost previously written outputs from the manifest; failure writing the final read log escaped before `report.json` | Each artifact is published atomically and registered immediately; final JSON retains usage, recorded feature counts, original errors and verified intact outputs |
| Pinned baseline publication | A denied `baseline-binding.json` write escaped the finalizer before the report was saved | Failed binding publication yields a sanitized failed JSON receipt with captured suites and an explicit unavailable binding |
| Physical run roots | Source tracing and working-file baseline could create run directories below a junction before rejecting redirected content | Existing artifact ancestors are checked before creating run directories or accessing a provider/runner; the outside directory remains empty |
| Custom API storage | The worker locked its artifact-root parent while the CLI locked the workspace, permitting concurrent execution | Both use the workspace's `data/execution.lock`; the worker waits before claiming queued work even with custom report storage |
| Busy CLI status | The working-file baseline translated `execution_busy` into `runner_failed` | All five CLI entry points refuse work and return the same sanitized busy code while the shared lock is held |

Source-trace failure receipts are also inspected through the actual FastAPI routes:
the result remains failed, its analyzed count agrees with JSON, and injected private
exception text is absent. A successful mocked trace independently rehashes every
published artifact. No API schema or frontend transport shape changed.

## Original finding coverage

| Finding | Fresh regression coverage |
|---|---|
| S4-A01 | Disconnected helper scope rejected; explicit fetch-file integration accepted |
| S4-A02 | Eight typed behavior mismatches rejected despite matching rationale prose |
| S4-A03 | Changed raw bytes rejected before JSON decoding; missing/extra/type-corrupt metadata rejected and retained as sanitized CLI/API evidence failures |
| S4-A04 | Optional Markdown write/render failures, source sidecar failures, binding publication failure and an unwritable JSON destination |
| S4-A05 | Artifact root/intermediate junction rejection and workflow-level rejection before run creation/provider access |
| S4-A06 | Zero counts before analysis in JSON/Markdown/API; actual source count retained after a later publication failure |

New source failure cases were also executed against the original committed source
controller loaded in memory, without replacing workspace files. They failed there;
the evidence-read-log cases raised the injected I/O error before saving JSON.
The original binding, custom-lock, renderer and busy-status reproductions likewise
failed before their respective fixes.

## Verification

| Final check | Result |
|---|---|
| Complete backend suite, including all six original audit regressions | **358 passed**, zero failures/errors/skips, 301.41 seconds |
| Ruff lint, backend/scripts/sandbox/examples | Passed |
| Ruff formatting | Passed, 82 files |
| Git whitespace check | Passed |
| Local links in six updated/new documents | Passed; no missing targets |

[JUnit results](f0-follow-up-tests.xml) retain test identities, outcomes and timestamps;
the machine hostname was omitted. The suite reports one existing Starlette/httpx
deprecation warning. [The checks manifest](f0-follow-up-checks.json) binds the receipt
to the tested source/dependency bytes and the retained JUnit digest.

Tests use local Git fixtures, controlled GitHub/Meta responses and mocked Docker operations. Normal
Windows permissions were needed because sandbox-created pytest directories were
not readable by subsequent fixture operations. Verification fixtures/output stay
under ignored `data/`; the initially created `.tmp/` directory was removed.

Recheck without paid inference or Docker execution, choosing a fresh temporary path:

```powershell
$taskRunId = [guid]::NewGuid().ToString('N')
backend/.venv/Scripts/python.exe -m pytest backend/tests -q --basetemp "data/test-runs/f0-$taskRunId"
backend/.venv/Scripts/ruff.exe check --config backend/pyproject.toml backend scripts sandbox examples
backend/.venv/Scripts/ruff.exe format --check --config backend/pyproject.toml backend scripts sandbox examples
git diff --check
```

This pass makes no new paid model requests, fetches no external repositories,
executes no repository code and leaves `.env` unchanged. Earlier live model/Docker
receipts retain their original dates and scope.

## Limits

F0's local foundation boundary is the scope of this receipt. Concurrent hostile
replacement of checked filesystem paths still requires an OS handle-based design.
A hard-killed worker can leave Docker resources requiring cleanup. Hosted leases,
fairness, retention/consent, private GitHub retrieval, general feature discovery and
verified production adaptation remain separate packages; this pass does not certify
connected analysis or verified adaptation as complete.
