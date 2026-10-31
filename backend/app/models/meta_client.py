"""Meta transport and response validation; tool dispatch belongs to the caller."""

import asyncio
from time import perf_counter
from typing import Any, Literal

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from app.config import META_BASE_URL, Settings
from app.contracts import ModelCall, ModelResult, TokenUsage
from app.errors import ConfigurationError, GraftError, MetaRequestError, ModelOutputError
from app.jobs.privacy import check_transmission
from app.jobs.quotas import charge, metric
from app.models.budget import ModelBudget
from app.models.schema import response_format, validation_summary


class MetaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        budget: ModelBudget | None = None,
    ):
        if not settings.model_configured or settings.model_api_key is None:
            raise ConfigurationError("Set MODEL_API_KEY in backend/.env or your local environment.")
        self._settings = settings
        self.budget = budget if budget is not None else ModelBudget()
        self.attempts: list[dict[str, Any]] = []
        self.calls: list[ModelCall] = []
        self._client = AsyncOpenAI(
            base_url=META_BASE_URL,
            api_key=settings.model_api_key.get_secret_value(),
            timeout=settings.meta_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    async def __aenter__(self) -> "MetaClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.close()

    async def request(
        self,
        *,
        stage: str,
        messages: list[ChatCompletionMessageParam],
        reasoning: Literal["low", "medium", "high"] = "low",
        output_tokens: int | None = None,
        **options: Any,
    ) -> tuple[ChatCompletion, ModelCall]:
        started = perf_counter()
        output_limit = (
            self._settings.meta_max_completion_tokens if output_tokens is None else output_tokens
        )
        if not 1 <= output_limit <= 8000:
            raise ConfigurationError("Model output-token limit must be between 1 and 8000.")
        for attempt in range(1, self._settings.meta_max_retries + 2):
            check_transmission(messages)
            self.budget.reserve()
            try:
                charge("model_attempts")
            except GraftError:
                self.budget.requests -= 1
                raise
            entry = {"stage": stage, "attempt": attempt, "status": "started", "usage": None}
            self.attempts.append(entry)
            attempt_started = perf_counter()
            try:
                completion = await self._client.chat.completions.create(
                    model=self._settings.meta_model_id,
                    messages=messages,
                    max_completion_tokens=self.budget.output_limit(output_limit),
                    reasoning_effort=reasoning,
                    **options,
                )
            except APIError as exc:
                metric("model_unknown_usage")
                entry["status"] = (
                    "http_error" if isinstance(exc, APIStatusError) else "transport_error"
                )
                status = exc.status_code if isinstance(exc, APIStatusError) else None
                entry["http_status"] = status
                retryable = (
                    isinstance(exc, APIConnectionError)
                    or status in {408, 409, 429}
                    or (status is not None and status >= 500)
                )
                if retryable and attempt <= self._settings.meta_max_retries:
                    await asyncio.sleep(0.25 * 2 ** (attempt - 1))
                    continue
                if isinstance(exc, APITimeoutError):
                    detail = "Meta API timed out after the configured retry limit."
                elif status is not None:
                    detail = (
                        f"Meta API returned HTTP {status}. "
                        "Check credentials, quota, and request support."
                    )
                else:
                    detail = (
                        "Could not read a valid Meta API response. Check network/proxy settings."
                    )
                raise MetaRequestError(detail) from None
            except asyncio.CancelledError:
                entry["status"] = "cancelled_usage_unknown"
                metric("model_unknown_usage")
                raise
            finally:
                entry["elapsed_ms"] = round((perf_counter() - attempt_started) * 1000)
            break

        try:
            completion = ChatCompletion.model_validate(completion.model_dump())
            if completion.usage is None:
                raise ValueError
            call = ModelCall(
                provider="meta",
                stage=stage,
                requested_model=self._settings.meta_model_id,
                model_id=completion.model,
                response_id=completion.id,
                elapsed_ms=round((perf_counter() - started) * 1000),
                attempts=attempt,
                usage=TokenUsage(
                    prompt_tokens=completion.usage.prompt_tokens,
                    completion_tokens=completion.usage.completion_tokens,
                    total_tokens=completion.usage.total_tokens,
                ),
            )
        except (ValidationError, ValueError):
            metric("model_unknown_usage")
            entry["status"] = "invalid_metadata"
            raise ModelOutputError("Meta response metadata is missing or inconsistent.") from None
        self.calls.append(call)
        self.budget.record(call)
        charge("model_input_tokens", call.usage.prompt_tokens, actual=True)
        charge("model_output_tokens", call.usage.completion_tokens, actual=True)
        metric("model_latency_ms", call.elapsed_ms)
        entry.update(status="received", response_id=call.response_id, usage=call.usage.model_dump())
        if completion.model != self._settings.meta_model_id:
            raise ModelOutputError("Meta returned a different model than configured.", call=call)
        if len(completion.choices) != 1:
            raise ModelOutputError("Meta response must contain one choice.", call=call)
        choice = completion.choices[0]
        entry["finish_reason"] = choice.finish_reason
        if choice.finish_reason not in {"stop", "tool_calls"} or choice.message.refusal:
            raise ModelOutputError("Meta response was refused or truncated.", call=call)
        self.budget.check()
        return completion, call

    async def structured[T: BaseModel](
        self,
        *,
        stage: str,
        messages: list[ChatCompletionMessageParam],
        schema: type[T],
        reasoning: Literal["low", "medium", "high"] = "low",
        output_tokens: int | None = None,
    ) -> ModelResult[T]:
        completion, call = await self.request(
            stage=stage,
            messages=messages,
            reasoning=reasoning,
            output_tokens=output_tokens,
            response_format=response_format(schema),
        )
        return ModelResult(data=self.parse(completion, call, schema), call=call)

    @staticmethod
    def parse[T: BaseModel](completion: ChatCompletion, call: ModelCall, schema: type[T]) -> T:
        message = completion.choices[0].message
        if (
            completion.choices[0].finish_reason != "stop"
            or message.tool_calls
            or not message.content
        ):
            raise ModelOutputError("Expected a structured response, not a tool call.", call=call)
        try:
            return schema.model_validate_json(message.content)
        except ValidationError as exc:
            raise ModelOutputError(
                f"Meta output failed schema or contract validation for {schema.__name__} "
                f"at {call.stage}: {validation_summary(schema, exc)}.",
                call=call,
            ) from None
