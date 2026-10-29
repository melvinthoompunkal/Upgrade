"""Single-use browser-bound authorization and expiring, hashed application sessions."""

import hashlib
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.migrations import LOCAL_OWNER
from app.api.storage import RunStore
from app.auth.crypto import CredentialCipher, ReconnectRequired
from app.auth.github import GitHubClient, GitHubDenied, GitHubError, GitHubTokens, GitHubUser

SESSION_COOKIE = "upgrade_session"
AUTH_COOKIE = "upgrade_auth"
SESSION_SECONDS = 7 * 24 * 3600
IDLE_SECONDS = 24 * 3600


class CredentialData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    expires: float = Field(ge=0, allow_inf_nan=False)
    refresh_expires: float = Field(ge=0, allow_inf_nan=False)


class AuthVerifier(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    owner_id: str
    login: str
    name: str | None
    csrf: str
    connected: bool


LOCAL_PRINCIPAL = Principal(LOCAL_OWNER, "Local workspace", None, "", False)


class AuthStore:
    def __init__(self, runs: RunStore, cipher: CredentialCipher):
        self.runs, self.cipher = runs, cipher

    def begin(self, destination: str) -> tuple[str, str, str]:
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.runs.connect() as db:
            db.execute("DELETE FROM auth_transactions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO auth_transactions VALUES (?,?,?,?,?)",
                (
                    digest(state),
                    digest(browser),
                    self.cipher.encrypt("auth", {"verifier": verifier}),
                    destination,
                    time.time() + 600,
                ),
            )
        return state, browser, verifier

    def consume(self, state: str, browser: str) -> tuple[str, str] | None:
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM auth_transactions WHERE state_hash=?", (digest(state),)
            ).fetchone()
            if (
                not row
                or row["expires"] <= time.time()
                or not secrets.compare_digest(row["browser_hash"], digest(browser))
            ):
                return None
            db.execute("DELETE FROM auth_transactions WHERE state_hash=?", (digest(state),))
        # Commit consumption before decoding. A lost key or damaged record cannot
        # roll back the single-use boundary or escape as an unsanitized callback error.
        try:
            verifier = AuthVerifier.model_validate(self.cipher.decrypt("auth", row["verifier"]))
        except (ReconnectRequired, ValidationError):
            return None
        return verifier.verifier, row["destination"]

    def encrypted_tokens(self, owner: str, tokens: GitHubTokens) -> bytes:
        return self.cipher.encrypt(
            owner,
            {
                "access_token": tokens.access_token.get_secret_value(),
                "refresh_token": tokens.refresh_token.get_secret_value(),
                "expires": time.time() + tokens.expires_in,
                "refresh_expires": time.time() + tokens.refresh_token_expires_in,
            },
        )

    def sign_in(self, user: GitHubUser, tokens: GitHubTokens, prior_session: str = "") -> str:
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        current = time.time()
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM users WHERE github_id=?", (user.id,)).fetchone()
            owner = row[0] if row else uuid4().hex
            db.execute(
                """INSERT INTO users(id,github_id,login,name,connected) VALUES (?,?,?,?,1)
                ON CONFLICT(github_id) DO UPDATE SET login=excluded.login,
                name=excluded.name,connected=1,access_epoch=users.access_epoch+1""",
                (owner, user.id, user.login, user.name),
            )
            db.execute(
                "INSERT OR REPLACE INTO credentials VALUES (?,?,?)",
                (owner, self.encrypted_tokens(owner, tokens), current),
            )
            db.execute("DELETE FROM repository_sync WHERE owner_id=?", (owner,))
            db.execute("DELETE FROM repository_cache WHERE owner_id=?", (owner,))
            db.execute(
                "DELETE FROM sessions WHERE token_hash=? OR expires<?",
                (digest(prior_session), current),
            )
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                (digest(session), owner, csrf, current, current + SESSION_SECONDS, current),
            )
        return session

    def principal(self, session: str) -> Principal | None:
        current = time.time()
        with self.runs.connect() as db:
            row = db.execute(
                """SELECT sessions.*,users.login,users.name,users.connected
                FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=?""",
                (digest(session),),
            ).fetchone()
            if not row:
                return None
            if row["expires"] <= current or row["last_seen"] + IDLE_SECONDS <= current:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(session),))
                return None
            db.execute(
                "UPDATE sessions SET last_seen=? WHERE token_hash=?", (current, digest(session))
            )
            return Principal(
                row["user_id"], row["login"], row["name"], row["csrf"], bool(row["connected"])
            )

    def logout(self, session: str) -> None:
        with self.runs.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(session),))

    def access_token(self, owner: str, github: GitHubClient) -> str:
        # A short SQLite write transaction serializes refresh-token consumption across
        # API threads/processes. Provider calls have a bounded timeout; hosted leases
        # and distributed credential storage remain a separate release requirement.
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                token = self._credential_token(db, owner, github)
            except (GitHubError, ReconnectRequired):
                # Settle this failed authorization before releasing the same lock.
                # Cleanup after rollback could erase a concurrently renewed grant.
                self._disconnect(db, owner)
                token = None
        if token is None:
            raise ReconnectRequired(
                "GitHub authorization is unavailable. Reconnect GitHub."
            ) from None
        return token

    def _credential_token(self, db, owner: str, github: GitHubClient) -> str:
        row = db.execute("SELECT * FROM credentials WHERE user_id=?", (owner,)).fetchone()
        if not row:
            raise ReconnectRequired("Reconnect GitHub to access repositories.")
        try:
            data = CredentialData.model_validate(self.cipher.decrypt(owner, row["ciphertext"]))
        except ValidationError:
            raise ReconnectRequired("Stored GitHub credentials are unusable.") from None
        if data.expires <= time.time() + 60:
            if data.refresh_expires <= time.time():
                raise ReconnectRequired("GitHub authorization expired. Reconnect GitHub.")
            tokens = github.refresh(data.refresh_token)
            db.execute(
                "UPDATE credentials SET ciphertext=?,updated=? WHERE user_id=?",
                (self.encrypted_tokens(owner, tokens), time.time(), owner),
            )
            return tokens.access_token.get_secret_value()
        # Reencrypt with the current first key, allowing gradual rotation.
        db.execute(
            "UPDATE credentials SET ciphertext=? WHERE user_id=?",
            (self.cipher.encrypt(owner, data.model_dump()), owner),
        )
        return data.access_token

    def _disconnect(self, db, owner: str) -> None:
        db.execute("DELETE FROM credentials WHERE user_id=?", (owner,))
        db.execute("UPDATE users SET connected=0,access_epoch=access_epoch+1 WHERE id=?", (owner,))
        db.execute("DELETE FROM repository_sync WHERE owner_id=?", (owner,))
        db.execute("DELETE FROM repository_cache WHERE owner_id=?", (owner,))
        self.runs.cancel_queued_in(db, owner)

    def disconnect(self, owner: str) -> None:
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._disconnect(db, owner)

    def disconnect_provider(self, owner: str, github: GitHubClient) -> str:
        # Keep provider revocation and local cleanup under the refresh/reconnect lock.
        # A failed provider call still removes local access and explicitly reports that
        # the remote grant must be removed in GitHub settings.
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            status = "revoked"
            try:
                github.revoke_authorization(self._credential_token(db, owner, github))
            except (GitHubError, ReconnectRequired):
                status = "manual_revocation_required"
            self._disconnect(db, owner)
        return status

    def current_access(self, owner: str, github: GitHubClient) -> int:
        with self.runs.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (owner,)).fetchone()
        if not row or not row["connected"]:
            raise ReconnectRequired("Reconnect GitHub before continuing.")
        epoch = row["access_epoch"]
        try:
            profile = github.profile(self.access_token(owner, github))
            if profile.id != row["github_id"]:
                raise GitHubDenied("GitHub identity changed. Reconnect GitHub.")
        except GitHubDenied:
            with self.runs.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT access_epoch FROM users WHERE id=?", (owner,)
                ).fetchone()
                if current and current[0] == epoch:
                    self._disconnect(db, owner)
            raise ReconnectRequired("GitHub authorization was revoked. Reconnect GitHub.") from None
        self.check_epoch(owner, epoch)
        return epoch

    def check_epoch(self, owner: str, epoch: int) -> None:
        with self.runs.connect() as db:
            row = db.execute(
                "SELECT connected,access_epoch FROM users WHERE id=?", (owner,)
            ).fetchone()
        if not row or not row["connected"] or row["access_epoch"] != epoch:
            raise ReconnectRequired("GitHub access changed. Reconnect or sync before retrying.")
