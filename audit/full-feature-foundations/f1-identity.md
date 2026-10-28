# F1 identity and owner boundaries

October 28, 2026, America/New_York. This records the next package after F0 in
[the development plan](../../docs/full-feature-development.md). It is a local
implementation and verification receipt, not a hosted security certification.

Base commit: `bedf27e1daf824228144e0ede272dc1687ca0758`. F0 and this F1 pass remain
uncommitted working-tree changes. The original foundation and F0 receipts are
preserved; their source manifests identify their earlier implementation states.
This receipt records the subsequent F1 implementation separately.
The exact working-tree source, test/dependency files and build outputs are bound in
[f1-identity-checks.json](f1-identity-checks.json).

## Reviewed and implemented

The existing F1 schema, numeric GitHub identity, hashed sessions, PKCE transactions,
encrypted keyring, CSRF, owner-scoped APIs and legacy ownership were already present.
The follow-up closes these additional boundaries:

| Boundary | Prior reproduction | Result |
|---|---|---|
| Failed refresh versus reconnect | Failure cleanup ran after releasing the refresh transaction and could delete a newly committed authorization | Failure cleanup remains under the refresh lock; reconnect commits afterward and survives |
| Disconnect versus cancellation | An injected queue-update failure left credentials removed while jobs remained queued | Connection state, credential/cache removal and queued cancellation commit or roll back together |
| Disconnect versus admission | A request with a previously loaded connected principal could enqueue after disconnect | GitHub-mode admission rechecks connection inside the enqueue transaction |
| Encrypted credential records | Missing/mistyped fields, infinite expiry, empty tokens or extra data could escape validation or reach the provider | Strict payload validation rejects them, requires reconnect and settles queued work without exposing input |
| Auth state consumption | A lost key or malformed verifier could raise a server error and roll back single-use consumption | Browser-bound state is consumed before decoding; unusable records yield a sanitized 400 and cannot replay |
| Auth error responses | Early 401/403 responses omitted private-response headers; a non-ASCII CSRF value raised `TypeError` | Rejections consistently use no-store/no-referrer/nosniff; byte comparison safely rejects malformed CSRF |
| Browser account switch | The new identity appeared beside the previous account's finished result; the old form draft was copied to the next owner | Private context clears before new history arrives; the routed workspace remounts under the new owner and reads only that owner's draft |

Before fixes, 16 backend reproductions and both desktop browser reproductions failed.
Tests use controlled provider responses and fault injection; none require a real GitHub
account, model inference or Docker execution.

## Ownership, sessions and migration

- Populated owner-only routes are exercised with real stored artifacts and hash-checked
  excerpts. Detail, compact status, events, reference, artifact list, text preview and
  download succeed for the owner. Another account receives the same 404 as an unknown
  run and cannot find the result through global history search. Submission keys remain
  independent for each owner.
- Existing replay, browser mismatch, denial, PKCE binding, renamed numeric identity,
  session rotation, CSRF and refresh/key-rotation cases remain covered. New cases add
  HTTP/HTTPS cookie flags, absolute/idle expiry, provider failure and invalid identity.
- A real SQLite v1 fixture with duplicate report paths fails migration transactionally:
  rows, events and version remain intact. After repairing the duplicate fixture path,
  migration succeeds and both history entries remain readable only under local ownership.
  A future schema is rejected without downgrade.
- No new schema version or token-format migration is required. Schema v3 stays in use.
  Existing valid encrypted records retain their fields; key rotation remains supported.

## Verification

| Final check | Result |
|---|---|
| Complete backend suite, retaining F0 regressions | **384 passed**, zero failures/errors/skips, 341.20 seconds |
| Deterministic desktop/mobile browser suites | **36 passed**, zero failures/skips, 97.65 seconds |
| TypeScript and production Vite build | Passed |
| Ruff lint and formatting | Passed; 83 Python files formatted |
| Prettier on changed browser files | Passed |
| Git whitespace check and local links in five updated/new documents | Passed |

[Backend JUnit](f1-identity-tests.xml), [browser JUnit](f1-browser-tests.xml), and
[content hashes](f1-identity-checks.json) retain the results. The backend reports one
existing Starlette/httpx deprecation warning; its machine hostname is omitted from
the retained JUnit. Browser project names remain explicit.

Backend cases use
temporary local SQLite/Git fixtures and controlled GitHub/Meta/Docker boundaries.
Browser cases use isolated headless Chrome desktop/mobile profiles and mocked API
responses. The temporary Vite server was stopped after these checks. Windows fixture
and helper-process restrictions required normal local permissions, as in F0.

The existing `.env` and local-mode preference are unchanged. No live GitHub sign-in,
private repository fetch, paid model call or Docker execution occurred in this pass.

## Next package and release limits

F1's local identity/owner checks are the scope of this pass. F2 still needs the
controlled private Git credential broker, installation tokens, signed webhooks,
provider revocation and current-access/in-flight handling. Real account integration
remains deferred; private code and hosted multi-user operation are not enabled.
Hosted worker/credential lifecycle and recovery validation remain release work.
