"""One controller-owned budget for HTTP attempts, returned usage, and elapsed time."""

from dataclasses import dataclass, field
from time import monotonic

from app.contracts import ModelCall
from app.errors import BudgetError
from app.jobs.control import checkpoint


@dataclass
class ModelBudget:
    max_requests: int = 32
    max_input_tokens: int = 400_000
    max_output_tokens: int = 40_000
    max_seconds: float = 1200
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    started: float = field(default_factory=monotonic)

    def check(self) -> None:
        checkpoint()
        if (
            monotonic() - self.started >= self.max_seconds
            or self.input_tokens >= self.max_input_tokens
            or self.output_tokens >= self.max_output_tokens
        ):
            raise BudgetError("Model token or wall-clock budget exhausted.")

    def reserve(self) -> None:
        self.check()
        if self.requests >= self.max_requests:
            raise BudgetError("Model HTTP-attempt budget exhausted.")
        self.requests += 1

    def record(self, call: ModelCall) -> None:
        self.input_tokens += call.usage.prompt_tokens
        self.output_tokens += call.usage.completion_tokens

    def output_limit(self, configured: int) -> int:
        self.check()
        return min(configured, self.max_output_tokens - self.output_tokens)
