"""F7 contracts: controller-owned identities and independent, declarative acceptance."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.catalog_contracts import Id
from app.contracts import Contract, FrozenList, Ref, RepoPath
from app.invocation_contracts import Invocation, json_value

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CaseId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


class AdaptationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_repairs: int = Field(default=3, ge=0, le=3)
    max_requests: int = Field(default=32, ge=1, le=32)
    max_input_tokens: int = Field(default=400_000, ge=1, le=400_000)
    max_output_tokens: int = Field(default=40_000, ge=1, le=40_000)
    max_seconds: int = Field(default=1200, ge=1, le=1200)


class AdaptationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["verified_adaptation"]
    assessment_id: Id
    feature_id: Id
    options: AdaptationOptions = Field(default_factory=AdaptationOptions)


class BehaviorRule(Contract):
    id: CaseId
    behavior: str = Field(min_length=1, max_length=600)
    failure_semantics: str = Field(min_length=1, max_length=600)
    source_refs: FrozenList[Ref] = Field(min_length=1, max_length=6)


class AdaptationPlan(Contract):
    schema_version: Literal["python-function-plan-v1", "python-integration-plan-v2"]
    included: FrozenList[BehaviorRule] = Field(min_length=1, max_length=12)
    excluded: FrozenList[str] = Field(min_length=1, max_length=12)
    integration_refs: FrozenList[Ref] = Field(min_length=1, max_length=5)
    production_paths: FrozenList[RepoPath] = Field(min_length=1, max_length=5)
    new_runtime_dependencies: FrozenList[str] = Field(max_length=0)
    rationale: str = Field(min_length=1, max_length=1200)


class FunctionCase(Contract):
    id: CaseId
    rule_id: CaseId
    module: str = Field(pattern=r"^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*$", max_length=160)
    function: str = Field(pattern=r"^[a-zA-Z_]\w*$", max_length=100)
    args_json: str = Field(max_length=8000)
    kwargs_json: str = Field(max_length=8000)
    expected_json: str | None = Field(max_length=8000)
    expected_exception: Literal["ValueError", "TypeError", "KeyError", "IndexError"] | None
    exception_message: str | None = Field(max_length=600)

    @model_validator(mode="after")
    def json_values(self):
        if not isinstance(json_value(self.args_json), list) or not isinstance(
            json_value(self.kwargs_json), dict
        ):
            raise ValueError("Function arguments must be JSON array/object values.")
        if (self.expected_json is None) == (self.expected_exception is None):
            raise ValueError("Specify exactly one return value or expected exception.")
        if self.expected_json is not None:
            json_value(self.expected_json)
            if self.exception_message is not None:
                raise ValueError("Return cases cannot specify an exception message.")
        elif self.exception_message is None:
            raise ValueError("Exception cases require exact failure text.")
        return self


class InvocationCase(FunctionCase):
    invocation: Invocation


class AcceptanceSpec(Contract):
    schema_version: Literal["python-function-acceptance-v1", "python-integration-acceptance-v2"]
    cases: FrozenList[FunctionCase | InvocationCase] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def profile_matches_cases(self):
        integration = self.schema_version == "python-integration-acceptance-v2"
        if any(isinstance(case, InvocationCase) != integration for case in self.cases):
            raise ValueError("Every acceptance case must match the selected adapter version.")
        return self


class ProductionFile(Contract):
    path: RepoPath
    before_sha256: Digest | None
    content: str = Field(min_length=1, max_length=48_000)


class PatchProposal(Contract):
    files: FrozenList[ProductionFile] = Field(min_length=1, max_length=5)
    rationale: str = Field(min_length=1, max_length=1200)


class AdaptationResult(BaseModel):
    schema_version: Literal["verified-adaptation-v1"] = "verified-adaptation-v1"
    feature_id: str
    verdict: Literal["unverified", "already_present", "verified"] = "unverified"
    repair_count: int = 0
    protected_tests_unchanged: bool = False
    patch_artifact: str | None = None
    patch_sha256: str | None = None
    changed_files: int = 0
    changed_lines: int = 0
    new_files: int = 0
    rounds: list[dict] = Field(default_factory=list)
