# Stage 4 W1: exact pinned-target baseline binding

This implements the first work package of the Stage 4 plan: execute the exact committed
target used for repository analysis, and preserve the input-to-capture relationship.
Target Profiler, Fit Matcher, feature selection, and production patching are later work.

## Run

With the existing locked backend environment and prepared Linux Docker image:

```powershell
uv sync --project backend --locked
uv run --project backend --locked graft-pinned-baseline
```

If the runner is not prepared, first run `scripts/build_runner.py` using the existing
[runner instructions](../sandbox/README.md). The command needs no model key, makes no
Meta requests, and accepts no arbitrary target path or test command.

The command registers only `crawler-demo`, pins its committed HEAD into independent Git
storage, and exports `pyproject.toml`, `src/crawler.py`, and `tests/test_crawler.py` from
Git blobs. It records the real repository SHA and `examples/crawler-target` prefix.
Uncommitted files are excluded and disclosed; they are not automatically committed.
Exports preserve the pinned bytes, including their line endings.

## Results

Isolation must pass its six registered cases and the crawler baseline must pass its thirteen
registered cases. JUnit case identities are checked, not just the number of cases or exit code.
Missing logs, changed evidence, unsupported cases, errors, skips, and timeouts cannot pass.

The frozen retry fixture is a separate diagnostic:

| `retry_diagnostic_status` | Meaning |
|---|---|
| `expected_red` | Four exact missing-retry assertion failures and three passing controls |
| `already_present` | All seven known retry cases pass; the old missing-retry gap cannot be claimed |
| `invalid` | Executed cases do not demonstrate either of those known outcomes |
| `not_run` | Earlier execution or evidence validation prevented this diagnostic |

The command exits 0 only when the ordinary baseline and evidence/preservation checks pass
and the diagnostic has a recognized outcome. Both `expected_red` and `already_present`
can yield that successful baseline check. Neither means a feature was selected or adapted.
Failures exit 1 and retain available sanitized evidence after pinning succeeds. Failures
during initial repository preparation print a sanitized error to stderr.

The existing `graft-crawler-baseline` retains its local-working-file behavior and requires
the known missing-retry result. Its older receipts are not substituted for a pinned run.

## Artifacts and checks

Each invocation creates `data/artifacts/pinned-baseline-<id>/` with:

- `report.json`: result, original target disclosure/preservation, suite captures, and the
  content hash of the baseline binding.
- `baseline-binding.json`: run ID, target repo/SHA/prefix, exported target and acceptance
  hashes, prepared image ID, image receipt hash, controller/runner definition hashes,
  and hashed capture references.
- `snapshot/` and `acceptance/`: the bounded exported inputs.
- `test-captures/<suite>/`: capture JSON, stdout, stderr, and JUnit XML.
- `repositories/`: the independent pinned target clone and inventory.

The controller checks inputs before and after every suite and revalidates completed captures
before publishing the final binding. It rejects unexpected export files and linked paths.
Capture image/suite identity, content hashes, JSON metadata, JUnit cases, and exit classification
must agree. The managed repository and original target are checked again before publication.
Reports use atomic replacement; a partially written JSON file is not published as complete.
Reusing an output directory is rejected, preserving existing receipts.

The binding describes a controller-owned run; it is not a signature or a general validator
for arbitrary imported historical reports. Typed selection contracts and artifact consumption
are part of the next work package. As with existing workflows, the registered input and
runner are the supported execution scope.

Target code runs only through the existing restricted offline Docker runner. No worktree
edits, source retrieval, source tests, dependency installation, commits, or model calls are
performed by this command.

## Controller integration

A later selection controller can reuse its already pinned target and original fingerprint:

```python
from app.pinned_baseline import record_pinned_baseline

report, report_path = record_pinned_baseline(
    target, original_state, owned_run_directory, image_receipt_path
)
```

`target` must be the existing reviewed `PinnedRepository`, and `original_state` must be the
state returned alongside it by `RepositoryManager.local_target`. The run directory already
exists but contains no previous baseline output. The function returns a failure report for
handled execution/evidence errors; callers must inspect `status` before using any capture.
No caller-supplied historical capture is accepted by this API.

## Development checks

```powershell
uv run --project backend --locked pytest backend/tests
uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts sandbox examples
uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts sandbox examples
```

Backend regression tests use disposable Git repositories and mocked Docker captures. They
do not import or execute the target on the host. They cover dirty/committed input separation,
SHA/prefix/inventory mismatches, byte-preserving exports, mutation before/between suites,
missing or inconsistent captures, earlier-capture tampering, registered case identities,
already-present retries, unrelated failures, safe failure receipts, and output reuse.
