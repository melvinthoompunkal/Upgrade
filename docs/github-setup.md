# GitHub connection development setup

The implemented connection is a development preview: sign-in, sessions, owner-scoped
sample runs, and a paginated index of the repositories both the user and App can access.
Private code retrieval, general project analysis, webhooks, hosted operation, generated
patches and PR creation are not enabled. Registering the App does not enable those features.

## Register a development GitHub App

Create a GitHub App in your GitHub developer settings. Use a separate registration for
development. For the Vite setup, use exactly these URLs:

| Setting | Value |
|---|---|
| Homepage | `http://127.0.0.1:5173` |
| User authorization callback | `http://127.0.0.1:5173/api/auth/github/callback` |
| Repository Contents permission | Read-only |
| Repository Metadata permission | Read-only |
| User access token expiration | Enabled |

This preview does not consume installation setup callbacks or webhooks. Disable webhook
delivery for this development registration. Install the App on selected repositories.
Sign-in and installation are separate: after sign-in, use **Repositories → Manage
repository access**, grant access on GitHub, then return and select **Sync repositories**.
No ordinary user needs a personal access token.

The authorization flow uses PKCE S256 and `offline_access` to request expiring tokens.
GitHub describes the [user/App access intersection and authorization flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
and [expiring user tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens).

## Configure the backend

Install the locked backend dependencies. Generate a credential-encryption key locally:

```powershell
uv sync --project backend --locked
backend/.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put that key and the App's client credentials in the ignored `backend/.env` file:

```dotenv
AUTH_MODE=github
APP_BASE_URL=http://127.0.0.1:5173
GITHUB_CLIENT_ID=<from your App registration>
GITHUB_CLIENT_SECRET=<from your App registration>
GITHUB_APP_SLUG=<your App slug>
TOKEN_ENCRYPTION_KEYS=<the generated key>
```

These are the names actually read by the backend. The plan's proposed singular
`TOKEN_ENCRYPTION_KEY` is superseded by the rotation-capable `TOKEN_ENCRYPTION_KEYS`.
App IDs, private keys and webhook secrets are not consumed by this preview. Preserve
the existing Meta settings. Never put credentials in frontend variables, Git remotes,
screenshots, issues or chat.

Start the backend with access logging disabled so authorization codes never appear
in Uvicorn request logs, then start Vite as documented in [frontend.md](frontend.md):

```powershell
uv run --project backend --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Use `127.0.0.1` consistently. Vite proxies `/api` to the backend; the browser's origin
and `APP_BASE_URL` must match exactly. For the compiled app served directly by FastAPI,
set `APP_BASE_URL=http://127.0.0.1:8000` and register that exact callback separately.
Any additional proxy must redact callback query strings and credential headers too.
Do not expose this development preview as a hosted multi-user service.

## Implemented behavior and limits

- Application sessions use opaque HttpOnly, SameSite=Lax, host-only cookies. HTTPS
  origins use Secure cookies; loopback HTTP is allowed for development. Session tokens
  are hashed in SQLite, expire after seven days absolutely or 24 hours idle, and rotate
  after sign-in. Mutations require the exact Origin and a per-session CSRF token.
- Ten-minute auth transactions are single use and bound to a separate browser cookie.
  Identity uses the numeric GitHub user ID, so renamed logins keep their ownership.
- GitHub access/refresh tokens are encrypted with authenticated, user-bound Fernet
  envelopes. Refresh is serialized and token pairs are replaced atomically. A failed
  refresh requires reconnecting. GitHub tokens never enter API DTOs or the browser.
- **Sign out** invalidates the current browser session. **Disconnect GitHub** removes
  local credentials and repository cache and cancels queued work. It does not uninstall
  a shared organization App. Provider-side grant revocation and in-flight cancellation
  are still pending; revoke the authorization in GitHub settings when necessary.
- Repository sync performs at most four provider page requests per action. **Continue
  sync** resumes its saved cursor. It caps discovery at 10,000 rows/1,000 installations
  and labels partial/limited results. The server searches all indexed rows and returns
  pages of 25. Browsing does not refresh GitHub or invoke Meta. Expired cache entries
  are hidden after 15 minutes; explicit sync refreshes access metadata.
- Indexed repositories are **browse/index** only. They are not added to the sample
  analysis target selector. Installation-token fetching, signed webhooks, immediate
  provider revocation processing, private-code consent, retention and hosted isolation
  remain release requirements in [the full feature plan](full-feature-development.md).

## Migrate, rotate and recover

On first startup, the SQLite v1 database migrates transactionally to schema v3.
Existing rows, events, report paths and hashes are preserved under `local-workspace`.
They never become visible to a GitHub user. Local report discovery runs only in local
mode. Switch `AUTH_MODE=local` on a loopback server to inspect those local reports;
there is no automatic ownership claim/import endpoint.

Back up `data/web/runs.sqlite3` using SQLite's backup API and retain the corresponding
artifact tree before a deployment/migration. Keep encryption keys in a separate secure
backup. Hosted backup/restore and deletion policies are not certified by this preview.

For rotation, prepend a new Fernet key to the comma-separated keyring and restart.
New credentials use the first key; successful credential reads reencrypt older records.
Keep old keys until every retained record has been rotated, or require reconnecting
affected users. Losing all applicable keys requires reconnecting; there is no plaintext
fallback. Fernet's [key rotation documentation](https://cryptography.io/en/latest/fernet/#cryptography.fernet.MultiFernet)
describes the underlying authenticated-encryption mechanism.

Controlled provider tests cover this flow. A real account sign-in and granted private
repository check are still required; no real GitHub App credentials were available
for the implementation receipt.
