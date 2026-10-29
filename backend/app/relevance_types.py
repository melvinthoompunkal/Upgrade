"""Small list projection shared without importing model/workflow contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RelevanceStatus = Literal["unassessed", "relevant", "less_relevant", "assessment_failed"]
FitStatus = Literal["unassessed", "ineligible", "eligible"]
TargetStatus = Literal["already_present", "potential_gap", "unknown", "no_target"]


class AssessmentSummary(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    relevance_status: RelevanceStatus = "unassessed"
    relevance_score: int | None = Field(default=None, ge=0, le=5)
    reason: str = Field(default="", max_length=600)
    target_status: TargetStatus = "unknown"
    fit_status: FitStatus = "unassessed"
