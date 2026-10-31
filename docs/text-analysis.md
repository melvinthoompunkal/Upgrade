# Repository text analysis preview

Use `AUTH_MODE=local` and public GitHub URLs. In source inspection choose
**All languages — text analysis preview**, optionally narrow the project root, then
inspect and discover features. Target comparison uses the same text profile and may
cross languages. No manifest, compiler, interpreter, dependency installation, language
server or parser is required. **Structure only** retains its reviewed Python definition
report and admission rules.

The preview is opt-in. FE0–FE4 have controlled implementation checks. FE5 requires
fixed-pin live samples and semantic review before making text analysis the default;
the code does not claim this gate passed. Optional parser enhancements (FE6) are disabled.
Hosted/private analysis, a new execution runtime and automatic ports are outside this release.

## Identity and compatibility

New text pins persist `repository-text-inventory-v2` and `repository-text-v1`.
Inventory v2 serializes file roles, classification bases, blob IDs and line counts,
and identifies test-helper filenames such as `render.test-helpers.ts` as test evidence.
Existing text v1 pins retain their original classification and digest, including the
historical omission of subclass metadata from that digest. New inspections use v2;
the same commit under a different inventory policy cannot reuse an incompatible catalog.
Missing policy/profile fields in historical descriptors select the legacy Python policy.
Reopening reconstructs the original policy and validates its digest without fetching.
A broader pin of the same commit has a new selection and catalog; old records and spend
are preserved. Catalog identities bind inventory, scope, model, prompts, retrieval,
evidence schema and disabled parser versions. Continuations with incompatible identities
require a new catalog before inference. Existing serialized tables need no physical migration.

`text-ref-v2` includes selection, repository, commit, root and role. Paths retain their
original repository spelling, including the selected root. The server validates inventory
membership, actual read windows and line bounds, then binds blob and excerpt SHA-256 hashes.
An optional symbol name is an unvalidated display claim. No model can confer parser validation.
Legacy `Ref`, `FeatureCard`, retry and adaptation validators retain their strict meanings.

## Retrieval and coverage

The selected project root is the hard boundary. Catalog scope only selects seed areas;
follow-up retrieval may examine related admitted files inside the root. Directory hints
come from manifests and source areas. Tree traversal visits directory metadata separately
from content; excluded vendor directories do not consume thousands of recursive file slots.
Unknown extensions and extensionless shebang sources can be admitted as strict UTF-8.
Unknown roles remain ambiguous evidence. Binary/non-UTF-8 blobs, symlinks, submodules,
notebooks without exact cell mappings, sensitive paths and generated locks have receipts.

Discovery distributes source windows across modules and runs documentation and implementation
passes separately. Continuation saves per-file offsets and advances overlapping windows;
oversized lines remain explicitly unread while later bodies remain reachable. Traces can
list paths, perform literal search and read exact ranges. Search supports file-offset and
line continuation; hits are logged as read evidence. Models receive no shell or execution tool.

Coverage records enumeration completeness, admitted/deferred/excluded paths, sampled modules,
read ranges, partially read files and remaining work. Counts of seed files and retrieved
files differ. Partial coverage never proves absence. Supported cards require operative
production entry/declaration and implementation references. Documentation/test/import-only
evidence cannot establish implementation. Exact citations do not establish semantic accuracy.

The initial limits remain 4,000 tree entries, 50 MiB admitted text, 2 MiB per file and
200 MiB snapshot storage. Traversal additionally stops after 256 directory reads and
offers a smaller project root. Evidence sessions expose at most 64 distinct files,
500,000 read bytes, 200 lines / 24,000 bytes per window. Each search scans at most
100 files / 4 MB / 2 seconds, returns at most 20 hits / 24,000 bytes, and shares a
20-query / 16 MB search allowance. Oversized or unexamined lines remain disclosed.

Jobs retain 32 seed files and eight deep traces by default, with at most 32 model attempts,
400,000 returned input tokens, 40,000 returned output tokens and 1,200 seconds. Retrieval
and synthesis share these limits, including the reserved final structured turn. Continuation
is a new explicit job. Actual returned usage, model IDs, tool denials and checkpoints are saved.

## Comparison and saved results

Semantic assessments preserve already-present, potential-gap, unknown and no-target outcomes.
Comparison seeds at most four target files and sends each distinct source range once,
alongside a compact capability description. Follow-up tools can retrieve more evidence
inside the same session and job allowances. The final structured turn reserves only
the request it actually sends, without unused retrieval tool schemas.
The model's response schema selects each role's citation type from its pinned profile,
including expansion integration points. The saved reader still accepts historical mixed
contracts, while text model output must carry project/root/role provenance throughout.
Specialized response-format names obey the API naming limit. Invalid names fail locally
before provider admission; valid JSON Schema alone does not establish a valid request envelope.
Useful potential gaps retain integration locations, benefits, constraints, uncertainty and
role-bound citations. Cross-language ideas carry a conceptual-transfer label. Generic fit
remains unassessed; no dependency/stack score is invented. Adaptation rejects generic cards
before planning, including multilingual repositories containing Python files.

Saved catalog browsing, filters, pagination, evidence, run exports and feature JSON downloads
perform no inference. v1 and v2 payloads dispatch by saved schema. Text exports include exact
provenance, policy identities and coverage; tests remain `not_run`.

## Evaluation

The independent Git corpus lives in `backend/tests/test_text_analysis.py`; the browser suite
is `frontend/tests/text-analysis.spec.ts`. Controlled provider responses test controller
guarantees separately from semantic quality. The actual saved-data browser seed can be served
with the model key and all writes disabled using `scripts/text_validation_server.py`.

Preregister fixed SHA/root/expected source evidence in
`docs/feature-extraction-evaluation.json`. Review expected ranges against acquired bytes
before starting inference. Inspect with:

```powershell
uv run --project backend --locked python scripts/validate_text_analysis.py --plan docs/feature-extraction-evaluation.json
```

Add `--live` for explicit paid jobs, or `--case graft-frontend` to run one declared case.
The runner uses normal job leases, the shared local provider ledger and storage reservations.
It refuses to run over existing queued/running work, disables cache reuse and never replays
failed jobs. Receipts under `data/feature-extraction/live/` retain the plan, source hashes,
pin/policy identities, expected excerpt hashes, model receipts, coverage and actual spend.
Acquisition failure is retained before any model call for that case.

Release still requires at least 20 supported cards reviewed for 90% semantic precision,
80% recovery of at least 10 preregistered scoped capabilities, exact citation validation,
evidence honesty and live comparison outcomes. Fewer samples leave the gate incomplete.
Do not enable a general default or claim whole-repository recall from the preview.

The four preregistered comparison outcomes are in
`docs/feature-extraction-comparison.json`. Run them explicitly with:

```powershell
uv run --project backend --locked python scripts/validate_text_comparison.py --plan docs/feature-extraction-comparison.json --live
```

Each comparison owns at most eight requests, 400,000 input tokens, 12,000 output tokens
and 300 seconds under the existing shared account quotas. Receipts retain actual usage,
returned model IDs, exported citation checks and whether declared budgets were respected.
Paid live validation requires operator authorization; offline tests use controlled responses.
An optional `existing_target_project` in a case reuses only an exact matching public
commit/root/role/profile pin; it never falls back to a different target. Remove those local
IDs when evaluating in a fresh workspace. Finished artifact reservations settle between cases.
`--max-requests` can lower a case's declared attempt allowance from eight (range 1–8).
For a small bounded evaluation, `JOB_ARTIFACT_BYTES` may lower the per-job artifact cap;
the receipt records this cap and the account/global limits. Exceeding it still stops the job.

OpenClaw uses the declared research pin. A smaller project root reduces analysis, not the
current whole-snapshot fetch. If the 200 MiB acquisition limit fails, retain the failure and
evaluate a separate pinned partial clone with explicit blob acquisition and object hashes
under the same storage/access limits. Measure that acquisition strategy before adopting it;
neither the global quota nor private/hosted admission should be raised to hide the failure.
