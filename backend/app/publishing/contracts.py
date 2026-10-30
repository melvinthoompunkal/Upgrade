"""Hash-bound publishing input and credential-free durable receipts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adaptation_contracts import Digest
from app.project_refs import RefRequest


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repository_id: int = Field(gt=0)
    base_branch: str = Field(min_length=1, max_length=200)
    patch_sha256: Digest
    notices_sha256: Digest
    reviewed: Literal[True]

    @field_validator("base_branch")
    @classmethod
    def valid_branch(cls, value):
        return RefRequest.valid_ref(value)


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: int
    repository: str
    base_branch: str
    base_sha: str
    branch: str
    patch_sha256: Digest
    notices_sha256: Digest
    created_at: str
    state: Literal["prepared", "committed", "branched", "published", "stale", "retryable"] = (
        "prepared"
    )
    tree_sha: str | None = None
    commit_sha: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    failure_code: str | None = None


class PublishReview(BaseModel):
    enabled: bool
    reason: str
    target_origin: str | None = None
    base_sha: str | None = None
    patch_sha256: str | None = None
    notices_sha256: str | None = None
    repository_id: int | None = None
    base_branch: str | None = None
    publication: Publication | None = None


class PullRepository(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: int = Field(gt=0)


class PullBranch(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    ref: str
    sha: str
    repo: PullRepository


class PublishedPull(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    number: int = Field(gt=0)
    html_url: str
    body: str
    head: PullBranch
    base: PullBranch
