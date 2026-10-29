# Phase 2: crawler baseline and offline runner

This implements task 2 in section 11 of `tech_breakdown.md`. The fixture is independently
written and uses HTTPX, HTMLParser, and a synchronous injected client. Its public API is
`Crawler(client).fetch(url) -> Page` and `crawl(start_url, max_pages=10) -> list[Page]`.
The caller owns the client. Fetch raises HTTPX status/transport exceptions; crawl follows
same-origin links in breadth-first order, visits each URL once, and enforces a page limit.
The baseline intentionally makes one GET attempt.

The seven acceptance cases specify recovery after one/two 503 responses, three-attempt
exhaustion, immediate success, permanent 404, parsing the recovered body, and unchanged
successful traversal. These are hand-authored Phase 2 tests of the proposed demo behavior.
Phase 3 now supplies a pinned source trace and proposed source-derived acceptance behaviors.
Contract finalization and model-driven implementation remain later gates.

## Execution boundary

The explicit `crawler-demo` workflow exports exactly its `pyproject.toml`,
`src/crawler.py`, and `tests/test_crawler.py`. It checks path containment, rejects links and
junctions, bounds file reads, and copies acceptance tests to a separate directory. `.git`,
`.env`, and other project files never enter the snapshot. This is a content-hashed fixture
export. The separate Phase 3 CLI now supplies Git SHA pinning and managed worktrees.

The runner builds only this directory's allowlisted Docker context. The Python 3.12 base is
pinned by registry digest; all 13 Python dependencies are exact versions with SHA-256 hashes.
`data/runner-image.json` records the built immutable image ID and build-input hashes.
Execution inspects that image ID, refuses changed build inputs, and uses `--pull=never`.
Rebuild the image after changing the Dockerfile, lockfile, pytest configuration, or baked checks.

Each suite gets a fresh container with:

- Network disabled; no exposed ports or Docker socket.
- UID/GID 65532, all capabilities dropped, and no-new-privileges enabled.
- Read-only root, target snapshot, and acceptance inputs. Only `/tmp` and a fresh results
  directory are writable. The host home, source repository, and host `.git` are not mounted.
- 2 CPUs, 1 GiB memory with no extra swap, 64 PIDs, 64 MiB temporary storage, and an 8 MiB
  per-file write limit. Captured stdout/stderr are bounded to 2 MiB each.
- 10 seconds per test and 60 seconds per container invocation. Timeout/output-limit paths
  terminate the container; generated containers are removed after captures.
- Fixed pytest paths/configuration, disabled plugin autoload/conftests for the crawler, and an explicitly
  loaded timeout plugin. No target-provided command, Dockerfile, install hook, or environment
  file is used. No host test-execution fallback exists.

Docker operations run in the controller; target imports and tests run only inside the container.
The image contains no provider client or API keys, and host environment values are not forwarded.
Isolation checks verify effective UID/capabilities/no-new-privileges, loopback-only networking,
read-only inputs/root, and absence of known credential variables and host paths.

F3 also provides reviewed public Python profiles. `project_baseline` uses original
`tests/`, `conftest.py` and reviewed `pyproject.toml` options; it preserves qualified case
identities. `project_unittest` uses the controller-owned `unittest_runner.py` and native
`load_tests`, including doctests. Mixed collection, unprepared dependencies/configuration
and incomplete executable inputs are rejected by preflight. Neither adapter installs a
project or runs setup/build hooks. The SHA/root, export hashes, adapter/image and captures
are bound in `project-baseline-binding.json`. Rebuild the image after updating the new
unittest runner as well. See [F3 limits](../docs/full-feature-development.md#6-supporting-real-target-and-source-projects).

These are bounded local profiles, not a certificate of safety for adversarial multi-user
execution. Additional dependencies need a separately reviewed preparation path; no
arbitrary host target-path option exists.

## Evidence and failure semantics

`graft-crawler-baseline` runs isolation, ordinary baseline, and acceptance sequentially. It stops
before acceptance if either prerequisite fails. Success means the first two suites passed,
the seven known acceptance cases yielded the four specified assertion failures and three
passes, and the original/exported input hashes still agree.

Every suite records the exact Docker creation command, immutable image ID, exit code, elapsed
time, stdout/stderr, JUnit case details, and artifact hashes. The report also hashes controller
definitions. Assertions preserve the observed HTTP status before checking recovery, so an
expected HTTPX exception becomes meaningful behavioral evidence rather than an uncaught error.
Missing/malformed JUnit, collection errors, skips/xfails, mismatched exits, and unrelated failures
cannot satisfy the red gate. Timeouts and output-limit failures are explicit unsuccessful captures.

`scripts/check_runner.py` runs disposable collection-error, skipped-test, per-test timeout,
container timeout, and oversized-output probes through the same registered baseline command.
Their code is generated into ignored `data/` and executes only in Docker.

## Audit follow-up

The October 27 Phase 1 audit remains a historical record. Its M1 setup overwrite is fixed in
the root README by creating `.env` only when absent. The existing `.env` is not rewritten.

Phase 3 resolves the remaining Phase 1 findings: probe ownership, failed-call telemetry and
retry accounting, strict schema compatibility, and mutable collections. It also resolves all
Phase 2 findings: stream I/O errors now fail the gate with owned diagnostics and required logs;
crawler traversal uses canonical URL identities; and root/ancestor links are rejected.

`scripts/check_capture_faults.py` reproduces open/write/read errors for both streams through
the real Docker baseline. Each must fail the overall check with a specific I/O diagnostic.

See [Phase 3 details](../docs/phase3.md) for pinned repositories, safe reads/AST evidence,
managed worktrees, and the live Meta source trace. Feature selection, implementation, and
final green verification follow afterward. The Docker baseline itself makes no model calls.

## References

- [Docker runtime restrictions and immutable image references](https://docs.docker.com/engine/containers/run/)
- [HTTPX MockTransport for deterministic offline responses](https://www.python-httpx.org/advanced/transports/)
- [pytest JUnit reports](https://docs.pytest.org/en/stable/how-to/output.html)
- [pytest-timeout 2.4.0 behavior](https://pypi.org/project/pytest-timeout/2.4.0/)
