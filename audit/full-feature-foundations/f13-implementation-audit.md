# F13 implementation and full-feature completion audit

Date: October 30, 2026, America/New_York.

Review and cleanup note, October 30, 2026: the current implementation boundaries were
checked against configuration, project admission, runtime support, hosted admission and
publishing code. The actionable follow-up is now recorded in
[section 15 of the maintained plan](../../docs/full-feature-development.md#15-next-implementation-and-documentation-policy).
Obsolete pre-F13 reports and one-off probes were removed; current regression/runner tests
and this report's complete F13 evidence set remain. See [the evidence index](../README.md).
The verification results below describe the original F13 checkout. Documentation,
ignore-rule and browser-test output changes from this cleanup invalidate a full current-source
comparison with that original manifest; its retained artifact hashes remain independently
checkable. This review does not certify the pending live workflows.

**Result: F13 is implemented and verified locally. The full product is not yet release-complete.**
The remaining work includes real GitHub and Meta workflows, private-access validation,
deployment certification, and implementation of any additional execution profiles offered.
Publishing remains disabled by default.

This is a dated handoff report for the F13 development session and its review of the
existing F0–F12 implementation. It does not claim that earlier packages were newly
implemented during this session. The ongoing backlog and release criteria remain in
[full-feature-development.md](../../docs/full-feature-development.md).

The implementation is now recorded in commit `95509eb` (`added all of full feature dev`).
The validation manifest records implementation base commit
`78892b78d477ffaeb5705188de5643945f0e2cb4` and hashes of the final working source bytes.
The commit message does not establish completion of the outstanding release gates.

## Work completed

### Explicit draft pull request publishing

Added a separate publishing action to a finished, verified adaptation's Changes page.
The form shows the target repository, verified base, base branch, patch/notices hashes,
and a required acknowledgment that the user reviewed the production patch and source
notices. Creating or retrying a draft PR requires an explicit button action.

Opening or reloading publishing review makes no GitHub writes, model calls, or target
executions. Loading the publishing status reads the saved local state. The UI handles
loading, disabled publishing, missing repository grants, permission denial, interrupted
publication, successful creation, and stale-base outcomes. It retains the existing
patch downloads, evidence inspection, and lazy diff review.

The implementation creates a unique `upgrade/<unique-id>` branch and a **draft** PR.
It does not update or force-push an existing branch, merge a PR, rebase an old patch,
or delete remote branches or PRs. PRs are created with maintainer modification disabled.

### Exact patch, notices, and repository binding

Publishing consumes the existing F7/F8 verified artifact chain rather than trusting a
new browser-supplied patch. It checks adaptation identity, frozen binding, report target,
target SHA, project-root prefix, registered patch bytes, and notices.

For each changed file, the controller reads the immutable GitHub base blob, checks the
saved preimage hash, reconstructs the reviewed unified diff, and checks the exact
postimage hash. Reconstruction covers new files, separate hunks, CRLF content, and
unterminated original lines. Production paths retain their monorepo prefix.

Git trees are walked explicitly. Linked or special directories, symlinks, executable
files, case collisions, invalid blobs, and incomplete tree responses cannot pass as
ordinary production files. A new tree starts from the whole original repository tree
and replaces only the verified production paths. Complete source notices already
embedded in the tested patch are retained. Publication also binds the saved source and
target notice hashes; the PR body and commit message record patch/notices digests.

### Current GitHub access and base checks

Every explicit attempt verifies the owner-scoped repository grant and numeric repository
identity, the target repository name, current user push permission, current App Contents
and Pull requests write permissions, installation state, and the account access epoch.
The supported publishing destination must be public, writable, and unarchived.

Rights and the exact base SHA are checked again before branch and PR creation. A moved
base requires a fresh adaptation; publishing never silently reapplies an old verified
patch to a different base. Tokens remain in the backend. Publishing uses the existing
GitHub App user access token, which is subject to the user/App permission intersection.
No new browser-visible write token or Git credential broker was added for publishing.

### Durable idempotency and recovery

SQLite schema 10 adds an owner-scoped `publications` table. Its primary key binds one
publication to an owner/run, and a unique owner/submission-key constraint rejects reuse
of the same key for different inputs. Stored request fingerprints reject changed inputs
for an existing publication. A different browser submission key cannot create another
publication for the same run.

The operation is saved before remote writes. Its receipt retains repository/base identity,
unique branch, patch/notices digests, deterministic commit inputs, Git object identities,
actual PR URL/number, and sanitized failure state.

| Receipt state | Meaning |
|---|---|
| `prepared` | The exact publication inputs and unique branch identity are saved. |
| `committed` | The publishing commit identity is retained. |
| `branched` | The unique branch points to the publishing commit. |
| `published` | The matching PR has been identified and saved. |
| `retryable` | An attempt was interrupted; an explicit retry reconciles saved and remote state. |
| `stale` | The verified base no longer qualifies; a fresh adaptation is required. |

Retries reconcile responses lost after blob, tree, commit, branch, or PR creation,
including after reopening the database. PR lookup includes closed PRs and does not
depend on the publishing branch still existing. A changed branch is never overwritten.

Publishing reuses the canonical execution lock, protecting it against concurrent local
execution and owner-data deletion. Owner deletion removes the local publishing receipt;
it does not delete the external GitHub objects. Existing provider quotas and sanitized
GitHub errors remain in use.

### API, configuration, and support reporting

| Interface | Added behavior |
|---|---|
| `GET /api/runs/{id}/pull-request` | Owner-scoped saved publishing review and receipt; no provider requests. |
| `POST /api/runs/{id}/pull-request` | Explicit, CSRF-protected publication or reconciliation attempt. |
| POST fields | `repository_id`, `base_branch`, `patch_sha256`, `notices_sha256`, and `reviewed: true`. |
| POST header | `Idempotency-Key`, using the validated submission-key format. |
| `GITHUB_PUBLISHING_ENABLED` | New opt-in setting, default `false`. |
| Capabilities and change review | Report the configured publishing capability for the applicable workflow. |
| Support matrix | Advertises the reviewed publishing profile and whether it is enabled. |

Enabling the setting requires local GitHub authentication mode, an App ID, and an App
private-key path. Hosted publishing is rejected by configuration. Actual GitHub write
rights are still checked on each explicit attempt. Private retrieval does not need to
be enabled to validate public-repository publishing.

The original local `.env` was not edited and production publishing was not enabled.
Setup, permissions, endpoint details, and the explicit development workflow are documented
in [github-setup.md](../../docs/github-setup.md#optional-draft-pull-request-publishing-f13).

### Rollback regression found and repaired

The first broad validation found that F12's rollback helper deliberately refused any
schema beyond version 9 until the rollback had been reviewed. Adding schema 10 exposed
that guard. This was a real regression to resolve, not a test to suppress.

The reviewed rollback now accepts schemas 9/10, interrupts running work, removes worker
status, and returns the database to schema 8 while preserving publication records and
their idempotency bindings. Re-upgrade retains those records. The regression check also
verifies owner preservation and retention of newer provider usage charges through
backup/restore. The corrected path passed both its focused check and the final full suite.
See [hosting recovery instructions](../../docs/hosting.md#upgrade-rollback-and-recovery).

### Local skills and documentation

Applied the local code-quality and smoke-test skills, plus both frontend design guides:

- `skills/code_quality_skill.md`: reviewed modularity, canonical helpers, boundary
  ownership, state handling, and file growth. New publishing logic is isolated in focused
  modules; no structural findings remained in the recorded review.
- `skills/smoke_test_skill.md`: built the production frontend, exercised relevant browser
  flows, and repeated the publishing/change-review checks.
- `skills/codex_frontend.md` and `skills/claude_frontend.md`: kept the existing visual
  language, isolated the publishing component, supplied interaction/failure states,
  and checked mobile layout and keyboard use. No new frontend dependency was needed.

Updated the README, backend configuration example, frontend guide, GitHub setup guide,
hosting guide, technical breakdown, and maintained feature checklist. Removed stale
claims that publishing had no endpoint or was only planned. Added optional publishing
to the release validator's explicitly pending external gates. Generated frontend API
types from the actual OpenAPI schema.

## Implementation inventory

| Files | Responsibility |
|---|---|
| [publishing/contracts.py](../../backend/app/publishing/contracts.py) | Validated requests, saved receipts, review DTOs, and provider PR metadata. |
| [publishing/patch.py](../../backend/app/publishing/patch.py) | Receipt-chain binding and exact patch reconstruction. |
| [publishing/github.py](../../backend/app/publishing/github.py) | Fresh rights checks, immutable Git reads, exact blob/tree/commit creation, and base checks. |
| [publishing/store.py](../../backend/app/publishing/store.py) | Atomic owner/run admission, input fingerprinting, durable receipts, and duplicate rejection. |
| [publishing/workflow.py](../../backend/app/publishing/workflow.py) | Staged publication, uncertain-response recovery, branch protection, and stale-state handling. |
| [publishing/routes.py](../../backend/app/publishing/routes.py) | Owner-scoped read and explicit write endpoints. |
| [API migrations](../../backend/app/api/migrations.py), [configuration](../../backend/app/config.py), [app registration](../../backend/app/main.py) | Schema 10, opt-in validation, and router registration. |
| [GitHub client](../../backend/app/auth/github.py) | Bounded list responses and explicitly allowed missing-reference responses; normal denied-access handling remains. |
| [Change routes](../../backend/app/change_routes.py), [review contracts](../../backend/app/change_review_contracts.py), [API contracts](../../backend/app/api/contracts.py), [capabilities route](../../backend/app/api/routes.py) | Publishing capability projection. |
| [Project routes](../../backend/app/projects.py), [support matrix](../../backend/app/support_matrix.py) | Runtime support/capability reporting. |
| [Retention](../../backend/app/jobs/retention.py), [operations](../../backend/app/jobs/operations.py) | Owner deletion and reviewed rollback preservation. |
| [PublishPatch.tsx](../../frontend/src/features/runs/PublishPatch.tsx), [Changes.tsx](../../frontend/src/features/runs/Changes.tsx) | Publishing form, explicit acknowledgment/action, receipt links, and retry/stale states. |
| [Browser support matrix](../../frontend/src/features/system/SupportMatrix.tsx), [API client](../../frontend/src/api/client.ts), [generated API types](../../frontend/src/api/schema.d.ts) | UI capability reporting and typed transport. |
| [Publishing API tests](../../backend/tests/test_publishing.py), [provider fixtures](../../backend/tests/publishing_fixtures.py), [browser tests](../../frontend/tests/publishing.spec.ts) | New publishing regressions and stateful controlled-provider checks. |
| [Change review tests](../../backend/tests/test_change_review.py), [hosting tests](../../backend/tests/test_hosting.py) | Disabled-by-default behavior and rollback/restore compatibility. |
| [Release validation](../../scripts/release_validation.py) | Explicit pending live publishing gate in release evidence. |

## Verification and evidence

The final validation manifest was retained at **12:52 p.m. EDT on October 30, 2026**.
The source and artifact hashes were rechecked when preparing this report and still pass.
This report does not claim a new execution of the entire test suite during documentation.

| Check | Retained result | Evidence |
|---|---|---|
| Final backend suite | 708 passed; zero failures/errors/skips. The Docker case was deselected here and run separately. | [Backend JUnit](f13-backend-junit.xml) |
| Fresh offline Docker adaptation | 1 passed; meaningful frozen assertion red, unchanged acceptance/regression green, and exact patch review evidence. Model responses were controlled. | [Docker JUnit](f13-docker-junit.xml), [evidence archive](f13-evidence.zip) |
| Final production browser suite | 174 passed across desktop/tablet/mobile. | [Browser JUnit](f13-browser-junit.xml) |
| Repeated publishing/change review | 27 passed across the three viewports. | [Repeated browser JUnit](f13-browser-repeat-junit.xml) |
| Earlier focused API/change review | 42 passed before the final edge-case additions and metadata hardening. These results are not presented as the final-source full suite. | [Earlier focused JUnit](f13-focused-junit.xml) |
| Final publishing cases | All 33 publishing checks passed as part of the final 708 backend tests. | [Test definitions](../../backend/tests/test_publishing.py) |
| Ruff lint and format | Passed. | [Release receipt](f13-release.json) |
| TypeScript/Vite build | Passed. | [Manifest and build hashes](f13-checks.json) |
| Python and npm dependency audits | Passed for the scanned dependency environment. | [Python audit](f13-python-audit.json), [npm audit](f13-npm-audit.json) |
| Diff whitespace check | Passed. | [Release receipt](f13-release.json) |
| F10 readiness | Blocked on GitHub configuration; model configuration, origin, Python, browser dependencies, prepared runner, and current F9/Docker evidence passed. | [Readiness receipt](f13-readiness.json) |

API cases cover exact bytes/notices, current user/App rights, stale bases before and during
publication, lost provider responses, restart and duplicate keys, changed inputs, changed
branches, revoked/disconnected access, foreign owners, forged CSRF, concurrent requests,
shared execution/deletion locking, retention, and new-file/EOF/CRLF/multiple-hunk reconstruction.

Browser checks cover explicit acknowledgment, keyboard submission, no writes on opening
or reopening, retained PR links, explicit retry with the same key, stale-base revalidation,
and actionable permission denial. Production screenshots were inspected at 390 and 1440 px;
browser behavior was checked at 390, 768, and 1440 px, including horizontal overflow.

The broad gate also exercises existing catalog/relevance scale and accessibility behavior.
These are local controlled-provider and synthetic scale checks; they do not establish
live semantic discovery quality or production multi-user isolation.

The authoritative machine records are [f13-checks.json](f13-checks.json) and
[f13-release.json](f13-release.json). [f13-evidence.zip](f13-evidence.zip) retains the final
release run's logs, build, measurements, browser output, and fresh offline adaptation evidence.

## OpenClaw preflight

The supplied `openclaw/openclaw` repository was inspected read-only through the connected
GitHub account. The recorded observation is from October 30, 2026, at 12:11:56 p.m. EDT.
It is a historical access/support preflight, not a claim about future permissions.

- Public read access succeeded; the connected account's reported push permission was false.
- The root manifest uses Node/TypeScript and pnpm, outside the reviewed Python adaptation
  profile. Reading its metadata does not make it an eligible verified-adaptation target.
- The local application had no configured development GitHub App for its own workflow.
- No repository clone, target execution, model transmission, branch creation, or PR creation
  was performed against OpenClaw.

The [OpenClaw receipt](f13-openclaw-preflight.json) retains repository identity, observed
permissions, manifest blob identity, and the resulting blockers. Its connected-account
permission result is separate from the local application's unconfigured App authorization.

## What still needs to be added or completed

Local implementation and passing regression tests do not close the following gates.
The retained release receipt marks connected analysis, verified adaptation, multi-user
hosting, and optional publishing **pending**.

| Area | Existing delivery | Required next work | Completion evidence |
|---|---|---|---|
| F0–F9 local foundations | Identity, repository/catalog/relevance services, local lifecycle, verified adaptation, review/download, and repeatable validation exist within reviewed profiles. | Complete their applicable live exit checks; repair defects exposed by those runs. | Fresh real-account/provider workflows and independently checked input/count/usage/notice/artifact receipts. |
| F2/F10 connected analysis | GitHub/App lifecycle code and readiness automation exist locally. | Configure a development App and test account; validate real sign-in, granted/ungranted repositories, token rotation, revocation, and repository changes. Run browser-driven multi-feature discovery and both browsing modes with Meta. | Real App lifecycle and live catalog/relevance receipts tied to exact pins and the tested build. |
| F7/F10 verified adaptation | Supported Python patches pass local frozen-test and regression checks. | Run a fresh supported live-model source-to-target adaptation through browser review/download. Independently check baseline green, meaningful assertion red, unchanged acceptance/regression green, actual scope, notices, repairs, and target preservation. | A complete live adaptation receipt, including actual repair history and exact patch hashes. |
| F6 provider/process lifecycle | Controlled cancellation, leases, recovery, quotas, retention, and backup/restore are implemented. | Validate real provider cancellation, hard process termination, owned-container recovery, and applicable hosted load/fairness/isolation. | Actual provider and deployment/process receipts with no silent paid-work replay. |
| F11 private analysis | Private snapshot preflight and disclosure/consent records exist; private analysis is fenced. | Complete live consent/access/revocation and applicable organization-policy validation before any private-analysis workflow is enabled. | Exact-snapshot consent/access evidence, revocation/retention checks, and policy review. |
| F12 hosted analysis | Single-host Linux configuration, separate API/worker, durable state, health, maintenance, rotation, and approval infrastructure exist. | Exercise a real host with TLS/proxy/origin configuration, tenant isolation, quotas/load, cancellation/recovery, retention, backup/restore, key rotation, migration/rollback, monitoring, and independent deployment review. | Current deployment/build-bound operator approval and actual-host evidence for the documented gates. |
| Hosted target execution | Not provided by the hosted analysis profile. | Design and implement a separately reviewed untrusted-execution isolation boundary before offering target tests/adaptation on the host. | Isolation, resource, network, filesystem, cleanup, and hostile multi-user execution validation for that boundary. |
| F13 live publishing | Local public-Python publishing, exact-byte binding, and durable recovery are implemented. | Use a writable supported development repository and configured App. Validate actual rights denial, moved bases, duplicate/concurrent requests, interrupted responses, exact remote bytes/notices, and unchanged default branch. | Live development-repository PR/commit/branch receipts with independently checked hashes and permissions. |
| Additional languages/profiles, including OpenClaw's root | Not implemented for Node/TypeScript adaptation. | Add reviewed inventory/evidence, planning, dependency policy, frozen acceptance, isolated execution/regression, patch scope, and support reporting for each added runtime/profile. Do not bypass the Python support gate. | Independent repository fixtures, real isolated red-to-green checks, and applicable live semantic acceptance for the new profile. |

Other existing support boundaries remain: arbitrary dependency/build/install scripts,
async fixtures and Trio, streaming/live-network/ASGI acceptance, unsupported decorated or
inherited APIs, new entry-point classes/modules, non-JSON call values, and unreviewed
license variants. A new helper production file permitted by F7 is distinct from adding
an unsupported new acceptance entry-point module. Optional Stage 4B comparative tracing
also remains separate work; it does not replace the release workflows above.

See the [support matrix](../../backend/app/support_matrix.py),
[hosted runbook](../../docs/hosting.md), and
[maintained acceptance checklist](../../docs/full-feature-development.md#14-acceptance-and-release-verification)
for the exact offered profiles and required scenarios.

## Known limitations and operational constraints

- GitHub does not provide an atomic transaction spanning a base check and PR creation.
  The controller checks before and after creation. A concurrent move retains the actual
  PR URL with a stale receipt; the verified patch must be revalidated against a fresh base.
- Controlled GitHub responses exercise application behavior but cannot certify real App
  settings, organization restrictions, branch rules, provider outages, or permission changes.
- Public-source evidence and AST checks establish provenance within bounded reads, not
  exhaustive feature discovery or semantic completeness.
- Existing physical path checks do not prevent a hostile local actor from replacing a
  checked path concurrently. Broader hostile-local or hosted execution guarantees require
  their own reviewed boundary and applicable handle-based filesystem work.
- Publishing errors can leave already-created Git objects or a unique branch. These are
  retained for reconciliation; the workflow does not perform destructive remote cleanup.
- Deleting the local workspace removes its local publication records and does not remove
  GitHub branches/PRs. Retain needed receipts before deletion or disaster recovery.
- A dependency scan records the scanned versions and database result at that time. It is
  not an independent security audit or certification of the complete deployment.

## Recommended order of remaining work

1. Establish a supported public Python source/target pair and a writable development
   repository. Configure the local GitHub App and approve the needed selected-repository
   grants. Use an owned test repository for publishing checks.
2. Complete F2/F10 live login, access, token lifecycle, discovery, relevance, and supported
   adaptation workflows. Retain exact pinned inputs and independently reviewed evidence.
3. Validate F13 with explicit publishing actions on the development repository, including
   negative permission/stale-base cases and uncertain-response recovery. Keep production
   publishing off until the live exit checks pass.
4. Finish real cancellation/process-recovery and private-consent/access/policy gates for
   any private capability that will be offered.
5. Complete the actual-host F12 review and approval. Implement and certify a separate
   execution boundary before enabling hosted adaptation.
6. Treat Node/TypeScript and other expanded profiles as implementation work with their
   own acceptance criteria. OpenClaw is not a substitute for a supported Python target
   and writable development repository in the current release validation.

## Reproduction and evidence integrity

From the repository root, the full local validation command is:

```powershell
uv --cache-dir .uv-cache run --project backend --locked python scripts/validate_release.py --with-docker --with-audits
```

It uses controlled model/provider responses for the regression workflow and requires
normal Windows permissions for disposable Git fixtures, Chrome, and Docker. Audit mode
queries public dependency databases. This command does not certify the pending live gates.

Recheck the retained local receipt without rerunning tests or inference:

```powershell
uv --cache-dir .uv-cache run --project backend --locked python scripts/validate_release.py --verify data/release-validation/7fd6d811773e4ceeb6ce05dc299a04fc/receipt.json
```

Recheck readiness against that receipt, without live GitHub/model calls:

```powershell
uv --cache-dir .uv-cache run --project backend --locked python scripts/validate_public_python.py --f9-receipt data/release-validation/7fd6d811773e4ceeb6ce05dc299a04fc/receipt.json
```

Source edits invalidate the retained source comparison and require appropriate fresh
validation. The original run is also retained in [the evidence archive](f13-evidence.zip).
This audit document lives outside the release runner's source-manifest scope, so adding
the report does not alter the tested application bytes or require changing old receipts.

The work delivered a locally verified F13 implementation and a documented completion
assessment. It did not deliver a live GitHub PR, enable private analysis, deploy or certify
a hosted service, add Node/TypeScript adaptation, or complete the pending full-release gates.
