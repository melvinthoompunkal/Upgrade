import json
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from app.config import META_BASE_URL, Settings
from app.contracts import (
    ModelCall,
    ModelResult,
    ProbeAcknowledgement,
    ProbeArguments,
    TokenUsage,
    ToolProbeResult,
)
from app.errors import ConfigurationError, MetaRequestError, ModelOutputError, SmokeCheckError


def response_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }


class MetaClient:
    """Bounded, async access to Meta only; no provider or model fallback."""

    def __init__(self, settings: Settings, *, http_client: httpx.AsyncClient | None = None):
        if not settings.model_configured or settings.model_api_key is None:
            raise ConfigurationError("Set MODEL_API_KEY in backend/.env or your local environment.")
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=META_BASE_URL,
            api_key=settings.model_api_key.get_secret_value(),
            timeout=settings.meta_timeout_seconds,
            max_retries=settings.meta_max_retries,
            http_client=http_client,
        )

    async def __aenter__(self) -> "MetaClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.close()

    async def _request(
        self,
        *,
        stage: str,
        messages: list[ChatCompletionMessageParam],
        **options: Any,
    ) -> tuple[ChatCompletion, ModelCall]:
        started = perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=self._settings.meta_model_id,
                messages=messages,
                max_completion_tokens=self._settings.meta_max_completion_tokens,
                reasoning_effort="low",
                **options,
            )
        except APITimeoutError:
            raise MetaRequestError("Meta API timed out after the configured retry limit.") from None
        except APIConnectionError:
            raise MetaRequestError(
                "Could not connect to Meta API. Check network/proxy settings."
            ) from None
        except APIStatusError as exc:
            raise MetaRequestError(
                f"Meta API returned HTTP {exc.status_code}. "
                "Check credentials, quota, and request support."
            ) from None
        except APIError:
            raise MetaRequestError("Meta API returned an invalid or unreadable response.") from None

        try:
            # The SDK's normal parser is permissive about missing provider fields.
            completion = ChatCompletion.model_validate(completion.model_dump())
        except ValidationError:
            raise ModelOutputError("Meta response is missing required completion fields.") from None
        if completion.model != self._settings.meta_model_id:
            raise ModelOutputError("Meta returned a different model than the configured model ID.")
        if len(completion.choices) != 1 or completion.usage is None:
            raise ModelOutputError("Meta response must contain one choice and token usage.")
        choice = completion.choices[0]
        if choice.finish_reason not in {"stop", "tool_calls"} or choice.message.refusal:
            raise ModelOutputError(
                "Meta response was refused, truncated, or did not finish normally."
            )
        try:
            call = ModelCall(
                provider="meta",
                stage=stage,
                requested_model=self._settings.meta_model_id,
                model_id=completion.model,
                response_id=completion.id,
                elapsed_ms=round((perf_counter() - started) * 1000),
                usage=TokenUsage(
                    prompt_tokens=completion.usage.prompt_tokens,
                    completion_tokens=completion.usage.completion_tokens,
                    total_tokens=completion.usage.total_tokens,
                ),
            )
        except ValidationError:
            raise ModelOutputError("Meta response metadata is missing or inconsistent.") from None
        return completion, call

    async def structured[T: BaseModel](
        self,
        *,
        stage: str,
        messages: list[ChatCompletionMessageParam],
        schema: type[T],
    ) -> ModelResult[T]:
        completion, call = await self._request(
            stage=stage, messages=messages, response_format=response_format(schema)
        )
        message = completion.choices[0].message
        if (
            completion.choices[0].finish_reason != "stop"
            or message.tool_calls
            or not message.content
        ):
            raise ModelOutputError(
                "Expected a structured response, not an empty reply or tool call."
            )
        try:
            data = schema.model_validate_json(message.content)
        except ValidationError:
            raise ModelOutputError(
                "Meta output failed the requested schema or contract validation."
            ) from None
        return ModelResult(data=data, call=call)

    async def probe_tool_round_trip(self) -> ToolProbeResult:
        """Prove a real tool call and consumption of a value only the tool can provide."""
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "developer",
                "content": (
                    "This is a read-only API integration check. "
                    "Call read_probe with empty arguments "
                    "to obtain a probe_token, then return the tool_name and exact probe_token. "
                    "Do not invent the token."
                ),
            },
            {"role": "user", "content": "Read the probe tool and acknowledge its returned token."},
        ]
        completion, first_call = await self._request(
            stage="tool_probe.request",
            messages=messages,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_probe",
                        "description": "Read the harmless in-memory integration probe token.",
                        "strict": True,
                        "parameters": {**ProbeArguments.model_json_schema(), "required": []},
                    },
                }
            ],
            tool_choice="auto",  # Meta rejects required/none/named choices.
            parallel_tool_calls=False,
        )
        message = completion.choices[0].message
        tool_calls = message.tool_calls or []
        if completion.choices[0].finish_reason != "tool_calls" or len(tool_calls) != 1:
            raise SmokeCheckError(
                "Meta did not make exactly one probe tool call; round trip unproven."
            )
        tool_call = tool_calls[0]
        if tool_call.type != "function" or tool_call.function.name != "read_probe":
            raise SmokeCheckError("Meta requested an unsupported tool; nothing was executed.")
        if not 1 <= len(tool_call.id) <= 64:
            raise SmokeCheckError("Meta returned an invalid probe tool-call ID.")
        try:
            ProbeArguments.model_validate_json(tool_call.function.arguments)
        except ValidationError:
            raise SmokeCheckError("Probe tool arguments must be an empty JSON object.") from None

        # Created only after the tool call: a correct final reply proves consumption of its result.
        probe_token = uuid4().hex
        messages.append(
            cast(ChatCompletionMessageParam, message.model_dump(mode="json", exclude_none=True))
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"tool_name": "read_probe", "probe_token": probe_token}),
            }
        )
        final = await self.structured(
            stage="tool_probe.response", messages=messages, schema=ProbeAcknowledgement
        )
        if final.data.probe_token != probe_token:
            raise SmokeCheckError("Meta did not return the token provided by the probe tool.")
        return ToolProbeResult(acknowledgement=final.data, calls=[first_call, final.call])
