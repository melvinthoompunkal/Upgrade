# Hosted analysis deployment

F12 supplies a **single Linux host** control plane: Caddy terminates HTTPS; two
Uvicorn API processes and a separate, process-owned analysis worker share a durable
local filesystem. SQLite WAL, leases, owner scheduling, quota transactions, journals
and artifacts live under `DATA_ROOT=/var/lib/upgrade`. No state lives in a release
checkout. Run only one worker per store; a duplicate cannot acquire its OS lock.
This is not a cross-host or network-filesystem deployment. SQLite requires WAL
clients to be on the [same host](https://www.sqlite.org/wal.html).

The offered profiles are public Python structural analysis without a baseline,
catalog discovery, and relevance assessment. Repository code is read, never imported
or executed by these profiles. Hosted adaptation, example workflows, target tests,
private retrieval/analysis, local-owner workflow CLIs and PR publishing are blocked.
The services have no Docker socket or Docker-group access. Offering untrusted target
execution requires a separate isolation implementation and independent review; the
existing local Docker runner is insufficient evidence.

The implementation has controlled local regression evidence. **Production admission
defaults closed** until the chosen deployment's evidence is reviewed. No Linux host,
real GitHub/Meta integration, public TLS, systemd sandbox or production load is
certified by local tests.

## Install an immutable release

Use a maintained Linux installation with systemd encrypted credentials, Python 3.12,
Git, Node, uv and Caddy. Install dependencies from `backend/uv.lock` and
`frontend/package-lock.json`; build with `npm ci` and `npm run build` in `frontend`.
Create a root-owned release under `/opt/upgrade/releases/<release-id>`, install the
backend environment with `uv sync --project backend --locked --no-dev`, and select
it with `/opt/upgrade/current`. Give the `upgrade` service account read/execute access
to the release and write access only to `/var/lib/upgrade` and its private temp area.
Never grant it permission to modify `/opt/upgrade`, `/etc/upgrade` or release evidence.
Do not include a developer `.env`, GitHub private key or real repository in the build.

Install [the API unit](../deploy/hosted/upgrade-api.service) and
[the worker unit](../deploy/hosted/upgrade-worker.service) into systemd's unit directory.
Create the unprivileged `upgrade` user/group first. The units create the private state
directory, bound memory/tasks, remove capabilities, restrict filesystem writes and
kill the complete service process group on hard shutdown. Those settings require
deployment verification; they do not establish an untrusted-code sandbox.

Copy [the Caddy template](../deploy/hosted/Caddyfile), replace its hostname and use
exactly the same public HTTPS origin in configuration and the GitHub App callback.
Expose only ports 80/443; keep port 8000 bound to loopback. The API trusts forwarded
headers only from `127.0.0.1`, the Caddy upstream connection. Do not add another proxy
without reviewing its trust chain. See [Uvicorn's proxy settings](https://uvicorn.dev/settings/).
HTTP requests and unexpected host/port authorities are rejected by the application;
successful hosted responses carry HSTS, no-referrer, nosniff and frame-denial headers.
Authenticated API responses retain no-store and existing owner/CSRF checks.

## Deliver secrets

Fill [the hosted environment example](../deploy/hosted/backend.env.example) in a
root-only temporary file outside the checkout. Include the GitHub App client secret,
newest-first Fernet keyring and optional Meta key. The structural profile needs no
Meta key. Encrypt it as a credential named `backend.env`:

```sh
sudo systemd-creds encrypt --name=backend.env /root/upgrade.env /etc/upgrade/backend.env.cred
```

Remove the plaintext temporary copy after independently verifying delivery. Keep
recoverable encryption keys in the operator's managed secret store, separate from
data backups. The API/worker entry points read systemd's `CREDENTIALS_DIRECTORY`;
secrets are absent from unit arguments and frontend builds. See
[systemd credential delivery](https://github.com/systemd/systemd/blob/main/docs/CREDENTIALS.md).
The application account can read delivered credentials because it must call providers;
never run repository code under that account. Disable application/proxy access logs
that would record OAuth callback codes, state or private artifact URLs.

## Review evidence and enable admission

Run commands from the release root using the backend interpreter. For preparation
on an operator workstation, an explicit root-only `--env-file` is also supported:

```sh
backend/.venv/bin/python scripts/validate_hosted.py --env-file /root/upgrade.env --template /root/pending.json
```

The template is deliberately invalid: every evidence outcome is `pending`, hashes
are empty and `approved_by` is empty. It cannot open admission. Collect and review
the following receipts on the actual host using the exact build and offered profiles:

| Gate | Required observation |
|---|---|
| `github_lifecycle` | Two real accounts, sign-in/denial, grant changes, token refresh and revocation |
| `tls_origin_proxy` | Real certificate, secure cookies, host/port, HTTP and spoofed-forwarding rejection, CSRF |
| `owner_isolation` | Foreign list/detail/event/catalog/reference/preview/download and cache access denied |
| `quotas_load_fairness` | Concurrent API-process admission, shared provider/storage limits, queue latency, owner fairness |
| `provider_cancellation` | Active real Meta/GitHub work stops; unknown usage is retained honestly |
| `hard_stop_recovery` | Kill service cgroup during work; old lease fenced, registered cleanup completes, no paid replay |
| `retention_backup_restore` | Expiry/deletion and newer deletion receipts survive a hash-checked restore |
| `key_rotation` | Re-encrypt with new-first keyring, old key removal, reconnect behavior for unavailable keys |
| `migration_rollback` | Upgrade preserves state; old binary refuses newer schema; restore recovers without replay |
| `monitoring` | Ready/worker/queue age, storage, provider latency, cleanup and failure alerts reach operators |
| `independent_review` | Independent review of this analysis-only deployment and its secret/process/storage boundaries |

In the manifest, set each reviewed outcome to `passed`, reference a relative evidence
file beside/below the manifest, and record that file's SHA-256. Record the approving
operator and a validity window of at most 30 days. Choose only the reviewed subset of
`project_analysis`, `catalog_discovery`, `relevance_assessment`. Install the manifest and
evidence root-owned/read-only under `/etc/upgrade/releases`, at `HOSTED_RELEASE_PATH`.
Deploy the same credential and evidence configuration to API and worker services.

The gate binds deployment ID, exact origin, expiry, backend code, built frontend,
locked backend dependencies and deployment templates. Missing/modified/expired files,
missing gates, unknown profiles or build drift pause admission and readiness. The worker
rechecks before claiming, before execution and every three seconds during active work;
removing approval interrupts active work. The gate checks integrity and the operator's
attestation; it cannot independently establish that receipts prove their claimed results.
Never install test-fixture evidence on a real host.

Start the API and worker after `systemctl daemon-reload`. `/health` is liveness;
`/ready` returns only ready/unavailable and 200/503. The API reads durable worker
heartbeats, not its own process state. A hard-killed worker becomes unavailable within
15 seconds. No API process starts a worker or imports local run history in hosted mode.

Invoke `scripts/validate_hosted.py` with delivered credentials to check approval plus
worker availability; it exits nonzero while blocked. This check makes no provider calls
and executes no repository code. It is not an active deployment certification test.

## Monitoring and operations

An authenticated configured numeric GitHub operator can read
`GET /api/operations/metrics`; ordinary users receive 403 and anonymous requests 401.
The response includes queue/outcome/error counts, oldest queued timestamp, resource
reservations, usage/runtime/latency counters, cleanup failures and worker availability.
No source, credentials or model reasoning is included. Alert externally on failed
readiness, sustained queue age, quota/storage saturation and cleanup errors. Configure
monitoring to retain metadata only; do not send cookie headers to analytics.

Existing offline commands now accept delivered hosted configuration:

```sh
backend/.venv/bin/python -m app.jobs.operations metrics
backend/.venv/bin/python -m app.jobs.operations reconcile
backend/.venv/bin/python -m app.jobs.operations prune
backend/.venv/bin/python -m app.jobs.operations backup --destination /var/backups/upgrade/new-backup
backend/.venv/bin/python -m app.jobs.operations restore --source /var/backups/upgrade/new-backup --destination /var/lib/upgrade-restored
backend/.venv/bin/python -m app.jobs.operations rotate-keys
backend/.venv/bin/python -m app.jobs.operations rollback-hosting
```

For hosted commands, use a systemd oneshot/transient service with the same user,
working directory and `LoadCredentialEncrypted` property as the regular services.
Grant that operator unit only the additional backup/destination paths it needs.
Do not export provider secrets into shell arguments. **Stop the worker before offline
operations.** Operators take the worker lock, shared execution lock and exclusive
maintenance lock; an active HTTP response makes maintenance fail for retry. During
maintenance new HTTP requests return 503. Linux request locks are shared across API
processes and remain held through response streaming, so backups and key changes
cannot race API database/filesystem writes.

Backup/restore retains both databases, code/artifact trees and hashes, rejects old
or modified backups, reapplies newer deletion receipts and never replays unfinished
inference. Restore clears worker-health records. Retain encrypted backups for at most
30 days, remove all replicas/caches/backups on that policy, and keep current deletion
receipts available. There are no database/storage replicas in this topology. Do not
introduce one without extending deletion, consistency and recovery verification.

To rotate keys, deliver `new,old` to both services, stop the worker, then run
`rotate-keys` with that same delivered configuration. The transaction re-encrypts every
owner-bound credential under the first key, invalidates outstanding PKCE transactions,
and rolls back all changes if any old ciphertext cannot be decrypted. Verify both
accounts and backup recovery before removing old keys from service configuration;
backups encrypted under old keys still need those keys for their retained lifetime.

## Upgrade, rollback and recovery

Remove approval to pause admission, stop both services and retain a fresh backup and
current deletion receipts. Select the new immutable release, start the API to apply
the forward migration, and run deployment checks. Add fresh build-bound evidence only
after the review succeeds, then start the worker. Queued work requires current approval;
abandoned running work is interrupted and cannot silently replay paid inference.

Database schema 9 adds worker status; schema 10 adds local publishing receipts.
Older binaries reject newer schemas. The reviewed `rollback-hosting` path accepts
schema 9 or 10, transactionally interrupts running jobs, removes worker status and
sets schema 8 while preserving all other rows, including publication idempotency and
uncertain provider outcomes. These receipts survive re-upgrade to prevent duplicate PRs. Stop
the worker and all API services first, retain a backup, run the command with the
current F13 release's interpreter/credentials, then select the compatible schema-8 release before
restarting services. Do not run the current API against the downgraded database: it will
upgrade it again. Later schema changes require a new reviewed rollback.

For recovery from a bad deployment, restore the backup into a **new** directory with
current deletion receipts available. Restore merges each current daily quota counter
using the larger of the backup/current values, so newer charges survive. Update the
canonical `DATA_ROOT` and credentials; if selecting an older binary, perform the
reviewed schema rollback on the restored copy before starting it. Keep admission
closed until operator reconciliation and fresh deployment approval pass. Restoring
data cannot recreate missing provider encryption keys or certify a successful release.
