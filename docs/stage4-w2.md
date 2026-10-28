# Stage 4 W2: typed selection artifacts

W2 defines the immutable data boundary for target profiling and feature selection.
It builds on the [W1 pinned baseline](stage4-w1.md). The subsequent
[Stage 4A workflow](stage4.md) now implements profiling, model assessment,
gate evaluation, and the selection command using these contracts.

## Contracts

| Contract | Owner and purpose |
|---|---|
| `TargetProfileProposal` | Model claims about stack, architecture, capabilities, gaps, style, and integration points |
| `TargetProfile` | Controller adds pinned SHA, registered tests, bounded coverage, and provenance |
| `ProfileProvenance` | Run ID, target repository ID, hashed baseline/read artifacts, and observed-gap capture/case links |
| `AssessmentBatch` | Model supplies unique candidate IDs, five fixed criteria, and proposed adaptations |
| `GateEvidence` | Controller records `pass`, `fail`, or `unknown` and its supporting explanation/references |
| `GateResult` | Approved boolean gate shape; only explicit `pass` becomes true |
| `CandidateScore` | Controller result with exact integer total, all ten gates, and consistent eligibility |
| `FitResult` | All scored candidates, ordered eligible `top3`, selection or explicit null, and complete rejection membership |
| `BaselineBinding` | Typed selection view of W1's complete pinned input/image/capture relationship |

The implementation is in [selection_contracts.py](../backend/app/selection_contracts.py)
and [baseline_contracts.py](../backend/app/baseline_contracts.py). It reuses `Contract`,
`FrozenList`, `Ref`, `Coverage`, and `Dependency`. Collections become tuples in Python
and arrays in JSON; nested contracts are frozen. Fields are required, nullable values
must be explicit, unknown properties are rejected, and ratings are strict integers 0–5.
Reasons are bounded to 2,000 characters.

Use only `TargetProfileProposal` and `AssessmentBatch` as inference output schemas.
They exclude authoritative SHA fields, capture IDs, test status, coverage, gates,
totals, ranking, eligibility, and selection. Citation SHAs are model claims that the
controller must validate. All new artifact schemas pass the existing `strict_schema`
preflight; this is a local compatibility check, not a fresh provider round trip.

The final profile rejects references from another repository or SHA. Capability and
gap IDs are unique. An `observed_behavior` gap requires exactly one controller link
with a capture ID and named case outcomes. Goal inferences do not claim capture proof.

Assessments retain the proposed paths, new paths, changed-line estimate, existing
dependency use, additions, and service requirements. Empty proposed paths are allowed
for an infeasible adaptation. Over-budget proposals can be recorded for rejection;
their estimates do not certify a future patch.

## Derived result invariants

`FitCriteria.weighted_total` uses `7*gap + 5*isolation + 3*dependency + 3*stack + 2*testability`.
`meets_thresholds` requires total ≥75 and gap/isolation/stack/testability each ≥3.
`CandidateScore` validates both the supplied total and eligibility against those
rules and every gate. W4 should reuse these helpers when constructing results.

`FitResult` rejects inconsistent ordering, unknown selection IDs, duplicate candidates,
and missing/duplicate rejections. Eligible candidates sort by descending total,
isolation, testability, dependency rating, then ascending feature ID. `top3` contains
at most three eligible IDs. An empty eligible set requires an empty `top3` and explicit
null selection; a one-candidate run remains valid.

These are constructor checks. W4 still has to validate the exact input catalog,
establish gate evidence, construct rejection reasons, and create the result in Python.
Model proposals cannot supply a precomputed result.

## W1 compatibility

W1 continues to write its existing `baseline-binding.json` format with file-hash maps.
For a successful controller-owned W1 run, convert that record with:

```python
from app.baseline_contracts import BaselineBinding

# w1_record belongs to the successful record_pinned_baseline invocation in this run.
binding = BaselineBinding.from_w1(w1_record)
```

The conversion preserves run/SHA/prefix, image and receipt hashes, definition hashes,
and every capture/artifact hash. Its immutable view uses `FileDigest` arrays instead
of mutable maps. Do not overwrite W1's receipt with this normalized representation.
Incomplete bindings, changed inputs, missing suites/log hashes, invalid paths, and
unknown fields are rejected.

This converter validates data shape and relationships. It does not read files,
verify content hashes, prove passing tests, or authorize historical receipt imports.
W1 owns its existing execution/hash checks. W3/W5 must retain run ownership, match
the exact target and prefix, inspect the successful report, and verify artifacts
again when reading them.

## Remaining controller checks

W3 must parse the pinned manifest as data, validate citations against actual inventory,
symbols and successful read windows, attach controller coverage, and verify observed
gap cases against the same snapshot's diagnostic. A named case in a contract alone
does not prove the behavior. W4 must validate source and target citations in separate
sessions, require exactly the supplied candidates, assess all ten gates, and reject
already-present or unsupported behaviors. W5 owns the run lifecycle and artifact registry.

No target code, runner execution behavior, Meta transport, dependencies, or CLI entry
points are changed by W2. Backend checks use mocked provider/runner data and disposable
Git repositories; target code is never executed on the host.

## Development checks

```powershell
uv run --project backend --locked pytest backend/tests
uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts sandbox examples
uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts sandbox examples
```
