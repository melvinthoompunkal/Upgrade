"""Public errors deliberately omit raw provider responses and credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.contracts import ModelCall


class GraftError(Exception):
    code = "graft_error"

    def __init__(self, detail: str, *, call: ModelCall | None = None):
        super().__init__(detail)
        self.call = call
        self.calls: tuple[ModelCall, ...] = (call,) if call is not None else ()
        self.attempts: list[dict[str, Any]] = []


class ConfigurationError(GraftError):
    code = "missing_configuration"


class MetaRequestError(GraftError):
    code = "meta_request_failed"


class ModelOutputError(GraftError):
    code = "invalid_model_output"


class SmokeCheckError(GraftError):
    code = "smoke_check_failed"


class BudgetError(GraftError):
    code = "budget_exhausted"


class RepositoryError(GraftError):
    code = "repository_failed"


class EvidenceError(GraftError):
    code = "invalid_evidence"


class AnalysisVersionError(EvidenceError):
    code = "analysis_versions_changed_start_new"


class UnsupportedTargetError(GraftError):
    code = "unsupported_target"


class UnsupportedSourceError(GraftError):
    code = "unsupported_source"
