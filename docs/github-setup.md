# GitHub connection development setup

The local connection supports sign-in, expiring sessions, owner-scoped sample runs,
repository sync/search, installation state, signed lifecycle webhooks and provider grant
revocation. F2 also implements explicitly enabled private snapshot retrieval. Real GitHub
account validation remains pending; leave private retrieval disabled until that check.
General analysis of connected projects and hosted operation remain later packages.

## Register a development GitHub App

Use a separate development registration and one canonical browser origin:

| Setting | Vite development value |
|---|---|
| Homepage | `http://127.0.0.1:5173` |
| User authorization callback | `http://127.0.0.1:5173/api/auth/github/callback` |
| Post-install setup URL | `http://127.0.0.1:5173/api/github/setup` |
| Repository Contents / Metadata | Read-only |
| User access token expiration | Enabled |

Install on selected repositories. Sign-in and installation are separate: use
**Repositories → Manage repository access**, grant access on GitHub, return and sync.
The setup callback verifies its installation ID against the signed-in user's accessible
installations; a query parameter never creates a grant. If setup returns before sign-in,
sign in and return to Repositories to sync. No personal access token is needed.

For lifecycle processing, configure a reachable HTTPS webhook URL ending in
`/api/webhooks/github` (an explicitly configured development relay is suitable), with
a generated webhook secret. Subscribe to installation, installation-repository and
repository lifecycle events. GitHub App authorization-revoked events are delivered by
default. The backend verifies the raw-body SHA-256 HMAC before accepting deliveries.
Without a webhook relay, keep private retrieval disabled; sign-in/indexing still work.

See GitHub's [authorization flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app),
[installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
and [webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

## Configure the backend

Install the locked environment and generate an encryption key locally:

```powershell
uv sync --project backend --locked
backend/.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the following in the ignored `backend/.env`, preserving existing Meta settings:

```dotenv
AUTH_MODE=github
APP_BASE_URL=http://127.0.0.1:5173
GITHUB_CLIENT_ID=<from App registration>
GITHUB_CLIENT_SECRET=<from App registration>
GITHUB_APP_SLUG=<App slug>
TOKEN_ENCRYPTION_KEYS=<generated Fernet key>
GITHUB_PRIVATE_FETCH_ENABLED=false
```

F2 consumes these additional optional settings. Generate the RSA private key in the
App settings and store it outside source control and artifact directories:

```dotenv
GITHUB_APP_ID=<numeric App ID>
GITHUB_PRIVATE_KEY_PATH=<absolute path to App RSA PEM file>
GITHUB_WEBHOOK_SECRET=<generated webhook secret>
```

When validating real private access, explicitly set `GITHUB_PRIVATE_FETCH_ENABLED=true`.
The backend requires GitHub mode and all three settings above for this switch. Merely
registering the App does not enable private retrieval or general project analysis.
There are no frontend secrets or token input fields. Never paste credentials into chat,
remotes, screenshots or reports.

Start with authorization-query access logging disabled:

```powershell
uv run --project backend --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Start Vite as described in [frontend.md](frontend.md). Use `127.0.0.1` consistently;
the browser origin and `APP_BASE_URL` must match. Vite proxies `/api`. For the compiled
app served by FastAPI, configure the exact `http://127.0.0.1:8000` origin/callback
instead. Any relay/proxy must also redact callback queries and credential headers.

## Connection and private snapshot behavior

- Sessions use hashed opaque identifiers in HttpOnly, SameSite=Lax, host-only cookies,
  Secure on HTTPS. Local loopback HTTP is supported. Sessions expire after seven days
  absolutely or 24 hours idle. Mutations require exact Origin and session CSRF.
- Ten-minute, browser-bound PKCE transactions are single use. Numeric GitHub identity
  owns data across login renames. Credentials use authenticated, owner-bound encryption;
  refresh, token-pair replacement and disconnect are transactional.
- **Sign out** invalidates the current application session. **Disconnect GitHub** attempts
  to revoke that user's provider authorization, then clears local credentials/cache and
  cancels queued work. It never uninstalls an organization's shared App. If GitHub cannot
  confirm revocation, the UI says to remove authorization in GitHub Settings → Applications.
- Repository sync performs at most four page requests per action. Continue resumes the
  saved cursor. The index caps at 10,000 repositories/1,000 installations and labels
  partial/limited states. Search, owner and visibility filters run on the server; pages
  contain at most 25 rows. Cached rows expire after 15 minutes. Browsing invokes no Meta.
- **Check installations** shows suspension, missing Contents permission and repository
  selection. No accessible installations may also mean organization approval is pending;
  Upgrade does not infer approval state that GitHub has not returned.
- With private retrieval enabled, each repository exposes **Pin a repository snapshot**.
  Supply a full lowercase commit SHA, source/target role and optional project-root prefix.
  `POST /api/repository-snapshots` fetches that immutable revision; it does not resolve
  branch names. The descriptor records SHA/root, inventory hash/counts and limits.
- Retrieval checks both fresh user-token repository access and the actual App installation
  before minting a one-repository, Contents-read installation token. A per-fetch loopback
  askpass channel keeps the token out of files, URLs, argv, remotes and environment values.
  The helper and channel are destroyed and token revocation is attempted in `finally`.
  If token revocation is unavailable, the discarded token expires within its provider lifetime.
- Git fetches only data into bare managed storage, with redirects, hooks, submodules and
  ambient credentials disabled. No source code, setup command or test is executed. Retrieval
  preflight rejects provider-reported repositories over 100 MiB; fetched storage is checked
  against 200 MiB, inventories cap at 4,000 entries/50 MiB text. The storage check is after
  fetch, not a hard operating-system disk quota during transfer.
- Successful private snapshots live in `data/private-repositories/`, separate from public
  run artifacts. Only an owner-scoped descriptor route is exposed. Every descriptor read
  rechecks current user/App access. Snapshot files are not downloadable through run routes
  and are not sent to Meta. Analysis capability remains **browse/index**.
- Signed webhook deliveries deduplicate by delivery ID plus body hash in a durable SQLite
  inbox. Invalidation and deduplication commit together; raw payloads are not retained.
  Installation/repository changes clear affected owners' indexes, increment access
  generations, cancel queued work and signal active F6 jobs. Resync is required before old descriptors reopen.
  Authorization revocation disconnects the matching numeric user. Reordered deliveries
  never restore cached grants. Fresh provider checks handle missed webhooks on each
  private operation, job admission, worker start and result publication.
- Fetches already in progress settle within the Git timeout and cannot publish through a
  changed generation. Failed fetches remove only their owned temporary code. Worker stage
  boundaries check known access changes, and publication is fenced transactionally.
  F6 adds durable cancellation, async provider interruption, owned runner cleanup and
  local lease fences. Hosted isolation and real provider/hard-stop validation remain pending.
  Private snapshots cannot currently be submitted to model workflows.
- Rate limits return sanitized 429 responses with Retry-After; there is no immediate
  rate-limit retry loop. Read-only 502/503/504 requests allow one retry; mutations do not.

## Migrate, rotate and verify

SQLite migrates transactionally to schema v8, preserving prior runs/events under their
existing owners. Legacy local records remain `local-workspace` and are never claimed by
a GitHub account. V4 adds access generations, delivery deduplication, snapshot descriptors
and run authorization bindings. Queued GitHub jobs without a generation binding fail
closed before execution; abandoned running jobs remain interrupted without paid replay.

Back up both `data/web/runs.sqlite3` and `data/web/usage.sqlite3` using the F6 offline
backup command, together with the managed code/artifact directories. Keep encryption keys
separately. For key rotation, prepend the new
Fernet key to `TOKEN_ENCRYPTION_KEYS`; successful reads reencrypt older records. Retain old
keys until rotation completes or require reconnecting. App key rotation replaces the
configured PEM file; new operations sign fresh short-lived JWTs. F6 supplies local
retention/deletion, integrity-checked backup/restore and exact-snapshot consent records.
Private model analysis stays disabled. Hosted lifecycle certification remains pending.
See [local operations](frontend.md#local-operations-and-recovery).

In GitHub mode, paid Meta diagnostics require an explicit numeric ID grant, for example
`OPERATOR_GITHUB_IDS=[12345]` in the backend environment. This does not change repository
permissions. Provider/deployment configuration has no browser mutation API. Default
daily REST/model allowances, disk reservations and retention settings are listed in
[`backend/.env.example`](../backend/.env.example).

The [current F2 verification record](full-feature-development.md#f2-local-private-access-lifecycle)
uses controlled HTTP responses, disposable local Git repositories and isolated browsers.
It does not establish real-account success. The remaining live exit check is: sign in with
a development account, fetch one granted private SHA, reject an ungranted/foreign ID,
rotate credentials, revoke/remove repository access, and confirm descriptors/work cannot
reopen or publish under the old grant. Keep local mode until ready to perform that check.
