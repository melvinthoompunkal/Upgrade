"""Fresh user/App intersection checks and generation fencing for private operations."""

import json
from dataclasses import dataclass

from pydantic import ValidationError

from app.auth.crypto import ReconnectRequired
from app.auth.github import GitHubDenied, GitHubError
from app.auth.repositories import GrantedRepository


class RepositoryAccessDenied(GitHubError):
    code = "repository_access_unavailable"

    def __init__(self):
        super().__init__("Repository access is unavailable. Sync grants or reconnect GitHub.")


@dataclass(frozen=True)
class RepositoryGrant:
    owner: str
    epoch: int
    installation: int
    repository: GrantedRepository


class RepositoryAccess:
    def __init__(self, runs, auth, github):
        self.runs, self.auth, self.github = runs, auth, github

    def epoch(self, owner: str) -> int:
        with self.runs.connect() as db:
            row = db.execute(
                "SELECT connected,access_epoch FROM users WHERE id=?", (owner,)
            ).fetchone()
        if not row or not row["connected"]:
            raise RepositoryAccessDenied()
        return row["access_epoch"]

    def check_in(self, db, owner: str, epoch: int) -> None:
        row = db.execute("SELECT connected,access_epoch FROM users WHERE id=?", (owner,)).fetchone()
        if not row or not row["connected"] or row["access_epoch"] != epoch:
            raise RepositoryAccessDenied()

    def check(self, grant: RepositoryGrant) -> None:
        with self.runs.connect() as db:
            self.check_in(db, grant.owner, grant.epoch)

    def authorize(self, owner: str, repository_id: int) -> RepositoryGrant:
        epoch = self.epoch(owner)
        # IDs must belong to this owner's index. Webhooks clear that index, forcing
        # an explicit fresh sync before old snapshots can be reopened.
        with self.runs.connect() as db:
            row = db.execute(
                "SELECT installation_id,full_name FROM repository_cache "
                "WHERE owner_id=? AND github_id=?",
                (owner, repository_id),
            ).fetchone()
        if row is None:
            raise RepositoryAccessDenied()
        try:
            token = self.auth.access_token(owner, self.github)
            repo = GrantedRepository.model_validate(
                self.github.request("GET", f"/repos/{row['full_name']}", token=token)
            )
            installation = self.github.request(
                "GET", f"/repos/{repo.full_name}/installation", token=self.github.app_jwt()
            )
            if (
                repo.id != repository_id
                or installation.get("id") != row["installation_id"]
                or type(installation.get("id")) is not int
                or installation.get("app_id") != self.github.settings.github_app_id
                or type(installation.get("app_id")) is not int
                or installation.get("suspended_at") is not None
                or not isinstance(installation.get("permissions"), dict)
                or installation["permissions"].get("contents") not in {"read", "write"}
            ):
                raise RepositoryAccessDenied()
        except (GitHubDenied, RepositoryAccessDenied, ReconnectRequired, ValidationError):
            self.invalidate(owner, repository_id, epoch=epoch)
            raise RepositoryAccessDenied() from None
        grant = RepositoryGrant(owner, epoch, row["installation_id"], repo)
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.check_in(db, owner, epoch)
            # Refresh rename/transfer/visibility metadata by immutable numeric ID.
            cached = db.execute(
                "SELECT data FROM repository_cache WHERE owner_id=? AND github_id=?",
                (owner, repository_id),
            ).fetchone()
            if cached:
                data = {**json.loads(cached["data"]), **repo.model_dump()}
                db.execute(
                    "UPDATE repository_cache SET full_name=?,data=? "
                    "WHERE owner_id=? AND github_id=?",
                    (repo.full_name, json.dumps(data), owner, repository_id),
                )
        return grant

    def invalidate(self, owner: str, repository_id: int, *, epoch: int) -> None:
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT access_epoch FROM users WHERE id=?", (owner,)).fetchone()
            if not row or row[0] != epoch:
                return
            db.execute("UPDATE users SET access_epoch=access_epoch+1 WHERE id=?", (owner,))
            db.execute(
                "DELETE FROM repository_cache WHERE owner_id=? AND github_id=?",
                (owner, repository_id),
            )
            db.execute("DELETE FROM repository_sync WHERE owner_id=?", (owner,))
            self.runs.cancel_queued_in(db, owner)
