# Audit evidence

The current handoff is [the F13 implementation audit](full-feature-foundations/f13-implementation-audit.md).
The maintained backlog and the review of remaining work are in
[the development plan](../docs/full-feature-development.md#15-next-implementation-and-documentation-policy).

## Retained evidence

The thirteen `full-feature-foundations/f13-*` files retain the F13 audit and its complete
evidence set. The eleven artifact hashes in `f13-checks.json` still refer to files kept
beside it. Their dates, source hashes and recorded results have not been rewritten.

These are historical results for the original F13 checkout. The cleanup changes
documentation, ignore rules and a browser test's output location, so the original source
manifest no longer describes every byte of this checkout. Run `scripts/validate_release.py`
for new source-bound evidence; do not treat the old receipt as current release approval.

## Historical evidence

The October 30 cleanup removed 136 obsolete files (3,194,349 bytes): pre-F13 stage
reports, JUnit output, old evidence archives, one-off audit verifier/reproduction scripts,
copied probe projects and their logs/cache. Their recorded outcomes remain in the
development plan. Historical filenames there identify the original evidence; they are
not links to files in the current tree.

Previously tracked foundation receipts can be read from the pre-cleanup commit:

```powershell
git show 8cdcf0f:audit/full-feature-foundations/f7-checks.json
git ls-tree -r --name-only 8cdcf0f -- audit
```

Earlier ignored, local-only probes and the historical-document ZIP were never part of
that commit and are not recoverable through Git. Their consolidated findings and
implementation decisions remain in `docs/full-feature-development.md`.

## Current tests and generated output

Keep `backend/tests`, `frontend/tests`, `sandbox/test_isolation.py`,
`sandbox/acceptance`, and the crawler example tests. These exercise current product
behavior. Some sandbox/example tests are also inputs to the actual verification
workflow; deleting them would break that workflow.

New validation output belongs under ignored `data/` or Playwright's ignored result
directories. Audit scratch output is ignored; only this index and the F13 handoff/evidence
are explicitly retained. Current validation tools live under `scripts/`.
