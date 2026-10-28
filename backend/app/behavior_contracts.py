"""Versioned behavior shared by selection and the frozen retry diagnostic.

This contract describes proposed behavior. It is not proof of an implemented patch.
"""

from typing import Annotated, Literal

from pydantic import Field

from app.contracts import Contract, FrozenList


class RetryBehavior(Contract):
    version: Literal["retry-v1"]
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"]
    retry_statuses: FrozenList[Annotated[int, Field(strict=True, ge=100, le=599)]]
    max_total_attempts: int = Field(strict=True, ge=1, le=100)
    final_error: Literal["raise_for_status", "return_response", "suppress"]
    parsing: Literal["after_success", "before_status_check"]
    transport_errors: Literal["propagate", "retry", "suppress"]
    backoff: bool
    retry_after: bool


REGISTERED_RETRY = RetryBehavior(
    version="retry-v1",
    method="GET",
    retry_statuses=[503],
    max_total_attempts=3,
    final_error="raise_for_status",
    parsing="after_success",
    transport_errors="propagate",
    backoff=False,
    retry_after=False,
)
