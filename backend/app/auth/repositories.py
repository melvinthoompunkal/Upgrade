"""Resumable, owner-scoped indexing of the GitHub user/App access intersection."""

import json
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.auth.crypto import ReconnectRequired
from app.auth.github import GitHubError
from app.errors import GraftError
from app.execution import ProcessLock

router = APIRouter(prefix="/api/repositories")
FRESH_SECONDS = 900
MAX_INSTALLATIONS = 1000
MAX_REPOSITORIES = 10000


class Account(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    login: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")


class GrantedRepository(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: int = Field(gt=0)
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    owner: Account
    private: bool
    default_branch: str = Field(min_length=1, max_length=255)
    archived: bool


class RepositoryItem(GrantedRepository):
    installation_id: int
    capability: Literal["browse_index"] = "browse_index"
    support_reason: str = (
        "Repository access is indexed. An execution adapter is required for analysis."
    )


class RepositoryPage(BaseModel):
    items: list[RepositoryItem]
    next_cursor: int | None
    revision: str | None
    sync_status: Literal[
        "not_synced", "partial", "complete", "no_installations", "expired", "limit_reached"
    ]
    discovered_count: int
    updated_at: float | None


def require_connection(request: Request):
    if request.app.state.settings.auth_mode != "github":
        raise HTTPException(409, "Repository connections require configured GitHub mode.")
    if not request.state.principal.connected:
        raise HTTPException(409, "Reconnect GitHub before syncing repositories.")
    return request.state.principal.owner_id


class RepositoryIndex:
    def __init__(self, runs, auth, github):
        self.runs, self.auth, self.github = runs, auth, github

    def page(self, owner: str, cursor=0, query="", account="", visibility="all") -> RepositoryPage:
        with self.runs.connect() as db:
            sync = db.execute("SELECT * FROM repository_sync WHERE owner_id=?", (owner,)).fetchone()
            if not sync:
                return RepositoryPage(
                    items=[],
                    next_cursor=None,
                    revision=None,
                    sync_status="not_synced",
                    discovered_count=0,
                    updated_at=None,
                )
            state = json.loads(sync["state"])
            status = state["status"] if sync["updated"] + FRESH_SECONDS > time.time() else "expired"
            if status == "expired":
                return RepositoryPage(
                    items=[],
                    next_cursor=None,
                    revision=sync["revision"],
                    sync_status=status,
                    discovered_count=0,
                    updated_at=sync["updated"],
                )
            where = "owner_id=? AND revision=?"
            values: list = [owner, sync["revision"]]
            if query:
                where += " AND instr(lower(full_name),lower(?))>0"
                values.append(query)
            if account:
                where += " AND lower(json_extract(data,'$.owner.login'))=lower(?)"
                values.append(account)
            if visibility != "all":
                where += " AND json_extract(data,'$.private')=?"
                values.append(visibility == "private")
            rows = db.execute(
                "SELECT data FROM repository_cache WHERE "
                + where
                + " ORDER BY full_name,github_id LIMIT 26 OFFSET ?",
                (*values, cursor),
            ).fetchall()
            count = db.execute(
                "SELECT count(*) FROM repository_cache WHERE owner_id=? AND revision=?",
                (owner, sync["revision"]),
            ).fetchone()[0]
        return RepositoryPage(
            items=[RepositoryItem.model_validate_json(row[0]) for row in rows[:25]],
            next_cursor=cursor + 25 if len(rows) > 25 else None,
            revision=sync["revision"],
            sync_status=status,
            discovered_count=count,
            updated_at=sync["updated"],
        )

    def advance(self, owner: str, *, restart: bool) -> None:
        with ProcessLock(self.runs.path.parent / f"repository-sync-{owner}.lock"):
            with self.runs.connect() as db:
                sync = db.execute(
                    "SELECT * FROM repository_sync WHERE owner_id=?", (owner,)
                ).fetchone()
            if restart or not sync:
                revision = uuid4().hex
                state = {
                    "phase": "installations",
                    "page": 1,
                    "installations": [],
                    "index": 0,
                    "count": 0,
                    "status": "partial",
                }
                self.save(owner, revision, state, [], clear=True)
            else:
                revision, state = sync["revision"], json.loads(sync["state"])
            if state["status"] != "partial":
                return
            token = self.auth.access_token(owner, self.github)
            for _ in range(4):  # An explicit continuation, never unbounded background HTTP.
                records = []
                if state["phase"] == "installations":
                    body = self.github.request(
                        "GET", f"/user/installations?per_page=100&page={state['page']}", token=token
                    )
                    installations = body.get("installations")
                    if not isinstance(installations, list) or len(installations) > 100:
                        raise GitHubError("GitHub returned an invalid installation page.")
                    for item in installations:
                        if not isinstance(item, dict) or not isinstance(
                            item.get("permissions", {}), dict
                        ):
                            raise GitHubError("GitHub returned invalid installation metadata.")
                        number = item.get("id")
                        if type(number) is not int or number <= 0:
                            raise GitHubError("GitHub returned an invalid installation identity.")
                        if not item.get("suspended_at") and item.get("permissions", {}).get(
                            "contents"
                        ) in {"read", "write"}:
                            if number not in state["installations"]:
                                state["installations"].append(number)
                    state["page"] += 1
                    if len(installations) < 100:
                        state.update(phase="repositories", page=1)
                    if len(state["installations"]) >= MAX_INSTALLATIONS or state["page"] > 100:
                        state["status"] = "limit_reached"
                else:
                    if state["index"] >= len(state["installations"]):
                        state["status"] = (
                            "complete" if state["installations"] else "no_installations"
                        )
                        self.save(owner, revision, state, records)
                        break
                    installation = state["installations"][state["index"]]
                    body = self.github.request(
                        "GET",
                        f"/user/installations/{installation}/repositories?per_page=100&page={state['page']}",
                        token=token,
                    )
                    repos = body.get("repositories")
                    if not isinstance(repos, list) or len(repos) > 100:
                        raise GitHubError("GitHub returned an invalid repository page.")
                    try:
                        records = [
                            RepositoryItem(
                                **GrantedRepository.model_validate(item).model_dump(),
                                installation_id=installation,
                            )
                            for item in repos
                        ]
                    except (ValidationError, TypeError):
                        raise GitHubError("GitHub returned invalid repository metadata.") from None
                    state["count"] += len(records)
                    state["page"] += 1
                    if len(repos) < 100:
                        state["index"] += 1
                        state["page"] = 1
                    if state["count"] >= MAX_REPOSITORIES:
                        state["status"] = "limit_reached"
                self.save(owner, revision, state, records)
                if state["status"] != "partial":
                    break

    def save(self, owner, revision, state, records, *, clear=False):
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            connected = db.execute("SELECT connected FROM users WHERE id=?", (owner,)).fetchone()
            sync = db.execute(
                "SELECT revision FROM repository_sync WHERE owner_id=?", (owner,)
            ).fetchone()
            if (
                not connected
                or not connected[0]
                or (not clear and (not sync or sync[0] != revision))
            ):
                raise ReconnectRequired("Repository access changed. Refresh the connection state.")
            if clear:
                db.execute("DELETE FROM repository_cache WHERE owner_id=?", (owner,))
            for item in records:
                db.execute(
                    "INSERT OR REPLACE INTO repository_cache VALUES (?,?,?,?,?,?)",
                    (
                        owner,
                        item.id,
                        revision,
                        item.installation_id,
                        item.full_name,
                        item.model_dump_json(),
                    ),
                )
            db.execute(
                "INSERT OR REPLACE INTO repository_sync VALUES (?,?,?,?)",
                (owner, revision, json.dumps(state), time.time()),
            )


def index(request):
    return RepositoryIndex(request.app.state.runs, request.app.state.auth, request.app.state.github)


@router.get("", response_model=RepositoryPage)
def list_repositories(
    request: Request,
    cursor: int = Query(0, ge=0, le=10000),
    q: str = Query("", max_length=200),
    owner: str = Query("", max_length=100),
    visibility: Literal["all", "public", "private"] = "all",
):
    user = require_connection(request)
    return index(request).page(user, cursor, q, owner, visibility)


@router.post("/sync", response_model=RepositoryPage)
def sync_repositories(request: Request, restart: bool = False):
    user = require_connection(request)
    try:
        index(request).advance(user, restart=restart)
    except GraftError as error:
        raise HTTPException(409, str(error)) from None
    return index(request).page(user)
