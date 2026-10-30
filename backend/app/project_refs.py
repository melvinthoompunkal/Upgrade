"""Resolve one explicitly named GitHub branch/tag; workers receive only the resulting SHA."""

import re
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.access import RepositoryAccess
from app.auth.github import GitHubClient, GitHubError


class RefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    url: str | None = Field(
        default=None,
        max_length=200,
        pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )
    repository_id: int | None = Field(default=None, gt=0)
    kind: Literal["branch", "tag"]
    name: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def one_repository(self):
        if (self.url is None) == (self.repository_id is None):
            raise ValueError("Select exactly one public URL or authorized repository ID.")
        return self

    @field_validator("name")
    @classmethod
    def valid_ref(cls, value):
        if (
            re.search(r"[\s\x00-\x20\x7f~^:?*\[\\]", value)
            or any(s in value for s in ("..", "@{", "//"))
            or value.startswith(("-", "/"))
            or value.endswith(("/", "."))
            or any(p.startswith(".") or p.endswith(".lock") for p in value.split("/"))
            or value == "@"
        ):
            raise ValueError("Use an explicit valid branch or tag name.")
        return value


class ResolvedRef(BaseModel):
    kind: Literal["branch", "tag"]
    name: str
    commit_sha: str
    repository_id: int | None


def resolve_ref(body: RefRequest, request):
    state, owner = request.app.state, request.state.principal.owner_id
    grant, access, token = None, None, None
    if body.repository_id:
        if not state.settings.github_private_fetch_enabled:
            raise HTTPException(409, "Authorized repository retrieval is disabled.")
        access = RepositoryAccess(state.runs, state.auth, state.github)
        grant = access.authorize(owner, body.repository_id)
        full_name = grant.repository.full_name
        github = state.github
        token = state.auth.access_token(owner, github)
    else:
        full_name = body.url.removeprefix("https://github.com/").removesuffix(".git")
        # Public resolution never borrows account credentials.
        github = GitHubClient(state.settings)
    namespace = "heads" if body.kind == "branch" else "tags"
    result = github.request(
        "GET", f"/repos/{full_name}/git/ref/{namespace}/{quote(body.name, safe='')}", token=token
    )
    if result.get("ref") != f"refs/{namespace}/{body.name}":
        raise GitHubError("GitHub returned a different named reference.")
    obj = result.get("object")
    for _ in range(5):
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("sha"), str)
            or not re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", obj["sha"])
        ):
            break
        if obj.get("type") == "commit":
            if grant:
                fresh = access.authorize(owner, body.repository_id)
                if (fresh.epoch, fresh.installation) != (grant.epoch, grant.installation):
                    from app.auth.access import RepositoryAccessDenied

                    raise RepositoryAccessDenied()
                access.check(fresh)
            return ResolvedRef(
                kind=body.kind,
                name=body.name,
                commit_sha=obj["sha"],
                repository_id=body.repository_id,
            )
        if body.kind != "tag" or obj.get("type") != "tag":
            break
        obj = github.request("GET", f"/repos/{full_name}/git/tags/{obj['sha']}", token=token).get(
            "object"
        )
    raise GitHubError("The selected reference does not resolve to a supported immutable commit.")
