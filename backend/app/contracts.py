from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

type FrozenList[T] = Annotated[
    tuple[T, ...], BeforeValidator(lambda v: tuple(v) if isinstance(v, list) else v)
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


def validate_repo_path(value: str) -> str:
    """Validate spelling; repository tools additionally enforce inventory membership."""
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or any(char in value for char in '<>"|?*')
        or any(part.endswith((".", " ")) for part in path.parts)
        or any(
            part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10)),
            }
            for part in path.parts
        )
        or any(ord(char) < 32 for char in value)
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.as_posix() != value
    ):
        raise ValueError("Expected a normalized repository-relative file path")
    return value


RepoPath = Annotated[str, AfterValidator(validate_repo_path)]
CommitSHA = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
NonEmptyString = Annotated[str, Field(min_length=1)]


class Ref(Contract):
    repo_id: NonEmptyString
    commit_sha: CommitSHA
    path: RepoPath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    symbol: NonEmptyString | None

    @model_validator(mode="after")
    def ordered_range(self) -> "Ref":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be at least start_line")
        return self


class RepoSnapshot(Contract):
    repo_id: NonEmptyString
    origin: NonEmptyString
    commit_sha: CommitSHA
    inventory_artifact_id: NonEmptyString
    license_refs: FrozenList[Ref]

    @model_validator(mode="after")
    def matching_license_snapshot(self) -> "RepoSnapshot":
        if any(
            ref.repo_id != self.repo_id or ref.commit_sha != self.commit_sha
            for ref in self.license_refs
        ):
            raise ValueError("License references must belong to this exact snapshot")
        return self


class RunPlan(Contract):
    source_focus_paths: FrozenList[RepoPath] = Field(min_length=1, max_length=15)
    target_focus_paths: FrozenList[RepoPath] = Field(min_length=1, max_length=15)
    feature_focus: FrozenList[NonEmptyString] = Field(min_length=1, max_length=8)
    analysis_tasks: FrozenList[Literal["trace_source", "profile_target"]] = Field(
        min_length=2, max_length=2
    )
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def both_analysis_tasks(self) -> "RunPlan":
        if set(self.analysis_tasks) != {"trace_source", "profile_target"}:
            raise ValueError("Plan must request source tracing and target profiling exactly once")
        return self


class TokenUsage(Contract):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent_total(self) -> "TokenUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Token totals are inconsistent")
        return self


class ModelCall(Contract):
    provider: Literal["meta"]
    stage: NonEmptyString
    requested_model: NonEmptyString
    model_id: NonEmptyString
    response_id: NonEmptyString
    elapsed_ms: int = Field(ge=0)
    usage: TokenUsage
    attempts: int = Field(default=1, ge=1)


class ModelResult[T: BaseModel](Contract):
    data: T
    call: ModelCall
