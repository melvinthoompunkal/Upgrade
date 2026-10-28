# Full-feature foundation implementation receipt

October 28, 2026, America/New_York. This is an implementation and verification receipt,
not an independent security audit or certification of the complete feature plan.

Base commit: `9d0ea7297d0844070d7706ca1cb7a2e92c09fb08`. Changes remain uncommitted.
[checks.json](checks.json) identifies the exact working-tree implementation with 104
source/dependency-file hashes and their canonical manifest digest. Original audit
reports and their dated reproductions were preserved unchanged.

## Delivered scope

The first pass from [the full feature plan](../../docs/full-feature-development.md) repairs
the six Stage 4 findings and implements the identity/owner boundary. It also adds a
development GitHub connection/repository-index preview, public source/target snapshot
preflight, global history search, compact status polling and a bounded event window.

| Finding | Repair | Regression evidence |
|---|---|---|
| S4-A01 | The cited `Crawler.fetch` file must appear in actual proposed production scope | Disconnected helper rejected; helper plus fetch file passes |
| S4-A02 | A strict versioned `retry-v1` contract binds method, status, attempt bound, final error, parsing, transport behavior and excluded retry extensions | Eight mismatched typed behaviors cannot win despite matching rationale prose; real valid contract passed all ten gates |
| S4-A03 | Capture digest verified before decoding; strict nested metadata; shape errors translate to `invalid_evidence` | Missing/extra/type corruption retained as sanitized workflow/API failures; hash-matching malformed metadata rejected; bad bytes rejected before JSON parsing |
| S4-A04 | JSON publication independent of optional Markdown; sanitized outer CLI failure boundary | Markdown-only denial retains JSON and original failure; unwritable root yields terminal JSON and exit 1 |
| S4-A05 | Physical root, ancestors and destination checked before artifact writes | Root and intermediate junction redirects rejected before any outside file is written |
| S4-A06 | Human report derives analyzed/eligible counts from the recorded values | Pre-inference failure shows zero in JSON, Markdown and API projection |

Selection finalization now uses a dedicated publication boundary. Shared OS locks
exclude CLI/API execution before the worker claims a job; interrupted inference is
not replayed. Explicit workflow context binds the API's settings and artifact root.
Lock acquisition does not append indefinitely to the lock file.

Ownership uses immutable numeric GitHub identity, opaque hashed sessions, absolute/idle
expiry, session rotation, exact-origin CSRF, browser-bound single-use PKCE transactions,
and authenticated user-bound credential encryption with a rotation keyring. Credential
refresh replaces pairs atomically; failure requires reconnecting. Existing local runs
and imported historical receipts stay under `local-workspace`. Every run list/detail,
status, event, reference, artifact preview and download is owner checked. Idempotency
keys and active limits are owner scoped. Schema migration is transactional.

Repository sync persists explicit continuation, bounds provider pages, indexes the
user/App intersection, hides stale cache entries, and supports server search/filtering
with 25-item pages. Disconnect clears credentials/cache and cancels queued work;
in-flight sync cannot repopulate that cache after disconnect. This preview does not
fetch private code or register arbitrary targets for inference/execution.

## Fresh verification

Environment: Windows, Python 3.12.7, Node 24.10.0, headless Chrome desktop/mobile,
Docker Desktop Linux containers and the existing prepared runner image.

| Check | Result |
|---|---|
| Complete backend suite before the final prompt clarification | **342 passed**, 271.43 seconds; zero failures/errors/skips |
| Profiler after the `target-profile-v2` clarification | **20 passed**; validation rules unchanged |
| Complete selection protocol after that clarification | **16 passed**, including retained failures and API inspection |
| Final deterministic/browser/API suite | **34 passed**, zero failures/skips, desktop and mobile |
| Browser recheck of the newly completed real selection | **2 passed**, desktop/mobile; actual excerpt and download digest checks |
| Frontend TypeScript and production build | Passed |
| Ruff lint and format | Passed for backend, scripts, sandbox and examples |
| Git whitespace check | Passed |
| Fresh Docker-only pinned baseline | Passed: 6 isolation and 13 ordinary cases; retry diagnostic has the expected 4 assertion failures and 3 passes |
| Public urllib3/HTTPX preflight | Both immutable snapshots fetched/inventoried; manifests inspected; no model calls or repository execution |
| Retained artifacts | Rehashed by [verify_receipts.py](verify_receipts.py); see [checks.json](checks.json) |

JUnit: [backend](backend-tests.xml), [profiler](profile-v2-tests.xml),
[selection protocol](workflow-v2-tests.xml), [browser suite](browser-tests.xml),
[fresh recorded-result browser recheck](browser-live-tests.xml).

Windows sandbox temporary-directory ACL and Vite helper-spawn restrictions required
normal local execution permissions for checks, as in earlier receipts. The existing
SQLite database was backed up before testing its real migration. Browser verification
used a local API instance with its worker disabled, so queued jobs could not execute.

### Fresh Meta runs, including the failure

Automatic approval review initially blocked external code-evidence transfer; no model
request started from that rejected invocation. The user then explicitly approved
sending the bounded public Scrapy/bundled crawler evidence and sample goal to Meta.

1. `fit-select-13c9d57aa43d47fab61a1448531e00e6`: source trace succeeded, but the
   target profile cited a related duplicate-visit test instead of the exact registered
   test. The controller rejected it as `invalid_evidence`. Its 18 calls, usage, 24
   intact artifacts and preservation results remain retained. No selection was claimed.
2. The controller's exact required capability/test mapping was added to the profiler
   prompt (`target-profile-v2`) and its diagnostic was made specific. No validator or
   acceptance gate was weakened. Focused profiler/protocol tests passed.
3. `fit-select-6a66d401a31d413bbd9193fcee246c52`: a separate fresh workflow completed
   with **selected**, one analyzed/eligible feature, **91/100**, all ten gates passing,
   and **29 rehashed artifacts**. It made 17 model calls with 135,440 input and 24,148
   output tokens. Source, original target and managed worktree were preserved.

Each workflow retained one shared budget across its stages. They are separate runs,
not hidden retries or a resumed paid call. Raw receipts remain under ignored
`data/artifacts/`; [checks.json](checks.json) records their paths/digests and both
outcomes. The browser recheck selected the new successful run from the actual API.

The accepted typed contract is GET/503, three total attempts, final `raise_for_status`,
parsing after success, propagated transport errors, no backoff and no Retry-After.
This verifies selection readiness, **not an implemented or green production patch**.

### Independent public examples

The user asked Upgrade to choose suitable public examples. The preflight retained:

| Role | Repository | Resolved commit | Text inventory |
|---|---|---|---|
| Source | [urllib3](https://github.com/urllib3/urllib3) | `5feb993421a48a508bc70c78e745337cc6415265` | 144 files, 1,478,408 bytes; 82 Python files |
| Target | [HTTPX](https://github.com/encode/httpx) | `b5addb64f0161ff6bfe94c124ef76f6a1fba5254` | 90 files, 974,681 bytes; 60 Python files |

[public-projects.json](public-projects.json) records the original branches, notices,
manifest/inventory hashes and `python-static-v1` support assessment. Both are currently
**browse/index** only. Fetching them does not demonstrate general discovery, a prepared
test runner, semantic compatibility, a reusable source-use policy or safe adaptation.

## Remaining release work

- GitHub live sign-in/installation/private-grant verification is deferred: credentials
  are absent and the user chose to continue in local mode. Their `.env` was unchanged.
  [github-setup.md](../../docs/github-setup.md) documents actual configuration.
- F2 still needs a private Git credential broker, installation tokens, signed webhooks,
  provider-side revocation/current-access checks and in-flight cancellation. Local
  disconnect is not a shared organization App uninstall or remote token revocation.
- F3 needs supported project-specific analyzers and reviewed offline execution profiles.
  Fetch size before expensive retrieval, dependency preparation, monorepo roots in
  onboarding and independent executable baseline coverage still need implementation.
- F4/F5 feature discovery, persistent catalogs, all/relevant membership, relevance,
  immutable pagination, lazy feature details and 10,000-row performance checks are pending.
  Global run-history search is implemented; it is not a feature catalog.
- F6 hosted leases, fairness, consent, retention/deletion, observability, recovery and
  hostile-code worker isolation remain pending. Hard-kill orphan Docker cleanup is not
  solved by a process lock. Physical path checks do not defend against a concurrent
  hostile local actor replacing checked filesystem paths.
- F7/F8 real planner, independently frozen meaningful acceptance red, constrained
  production patches, actual scope/dependency checks, unchanged acceptance/regression
  green, capped repairs, diff review/download and explicit idempotent PR publishing
  are not implemented.
- F9 full connected-analysis and verified-adaptation release receipts remain required.

The original sample analysis remains usable. Neither full connected analysis nor
verified adaptation is marked complete by this receipt.

Recheck retained evidence without inference:

```powershell
backend/.venv/Scripts/python.exe audit/full-feature-foundations/verify_receipts.py --selection fit-select-13c9d57aa43d47fab61a1448531e00e6 --selection fit-select-6a66d401a31d413bbd9193fcee246c52
```
