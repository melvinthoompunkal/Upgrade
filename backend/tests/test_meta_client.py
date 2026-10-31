import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.contracts import RunPlan
from app.errors import ConfigurationError, MetaRequestError, ModelOutputError, SmokeCheckError
from app.models.meta_client import MetaClient
from app.smoke import probe_tool_round_trip, run_smoke

TEST_KEY = "offline-secret-do-not-print"
PLAN = {
    "source_focus_paths": ["scrapy/downloadermiddlewares/retry.py"],
    "target_focus_paths": ["src/crawler.py"],
    "feature_focus": ["bounded retries"],
    "analysis_tasks": ["trace_source", "profile_target"],
    "rationale": "Trace retry behavior and inspect the target fetch boundary.",
}
TOOL_CALL = {
    "id": "call-probe-1",
    "type": "function",
    "function": {"name": "read_probe", "arguments": "{}"},
}


def settings(**changes: object) -> Settings:
    return Settings(
        _env_file=None,
        **{
            "model_api_key": TEST_KEY,
            "meta_model_id": "muse-spark-1.3",
            "meta_max_retries": 0,
            **changes,
        },
    )


def completion(content: object = PLAN, *, calls: list | None = None, **changes: object) -> dict:
    return {
        "id": "chatcmpl-offline-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "muse-spark-1.3",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if calls is not None else "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content) if calls is None else None,
                    **({"tool_calls": calls} if calls is not None else {}),
                },
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        **changes,
    }


async def structured_with(handler) -> object:
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with MetaClient(settings(), http_client=transport) as client:
        return await client.structured(
            stage="test", messages=[{"role": "user", "content": "Make a plan."}], schema=RunPlan
        )


def test_schema_and_actual_tool_message_round_trip() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "muse-spark-1.3"
        assert body["max_completion_tokens"] <= 8000
        assert "max_tokens" not in body
        if len(requests) == 1:
            assert body["response_format"]["json_schema"]["strict"] is True
            return httpx.Response(200, json=completion())
        if len(requests) == 2:
            assert body["tool_choice"] == "auto"
            assert body["parallel_tool_calls"] is False
            assert body["tools"][0]["function"]["parameters"]["additionalProperties"] is False
            return httpx.Response(200, json=completion(calls=[TOOL_CALL]))

        assert len(requests) == 3
        assistant, result = body["messages"][-2:]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"] == [TOOL_CALL]
        assert result["role"] == "tool"
        assert result["tool_call_id"] == TOOL_CALL["id"]
        probe = json.loads(result["content"])
        assert len(probe["probe_token"]) == 32
        return httpx.Response(200, json=completion(probe))

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaClient(settings(), http_client=transport) as client:
            return await run_smoke(client)

    report = asyncio.run(run())
    assert report.status == "passed" and report.tool_round_trip
    assert len(report.calls) == len(requests) == 3
    assert [call.stage for call in report.calls] == [
        "schema_probe",
        "tool_probe.request",
        "tool_probe.response",
    ]
    assert all(call.provider == "meta" and call.usage.total_tokens == 20 for call in report.calls)
    assert TEST_KEY not in report.model_dump_json()


@pytest.mark.parametrize(
    "failure",
    [
        "bad_json",
        "extra_field",
        "duplicate_task",
        "wrong_model",
        "no_usage",
        "truncated",
        "refused",
        "no_choices",
        "missing_fields",
    ],
)
def test_invalid_or_unfinished_outputs_never_count_as_success(failure: str) -> None:
    payload = completion()
    if failure == "bad_json":
        payload["choices"][0]["message"]["content"] = "```json\nnot-json\n```"
    elif failure == "extra_field":
        payload = completion({**PLAN, "command": "run arbitrary shell"})
    elif failure == "duplicate_task":
        payload = completion({**PLAN, "analysis_tasks": ["trace_source", "trace_source"]})
    elif failure == "wrong_model":
        payload["model"] = "another-provider-model"
    elif failure == "no_usage":
        payload["usage"] = None
    elif failure == "truncated":
        payload["choices"][0]["finish_reason"] = "length"
    elif failure == "refused":
        payload["choices"][0]["message"]["refusal"] = "refused"
    elif failure == "no_choices":
        payload["choices"] = []
    elif failure == "missing_fields":
        payload = {}
    with pytest.raises(ModelOutputError):
        asyncio.run(structured_with(lambda _: httpx.Response(200, json=payload)))


def test_validation_failure_identifies_schema_stage_and_field_without_provider_content():
    payload = {**PLAN, "rationale": 123, TEST_KEY: "private repository content"}
    with pytest.raises(ModelOutputError) as error:
        asyncio.run(structured_with(lambda _: httpx.Response(200, json=completion(payload))))
    detail = str(error.value)
    assert "RunPlan at test" in detail
    assert "rationale (string_type)" in detail
    assert "<field> (extra_forbidden)" in detail
    assert TEST_KEY not in detail and "private repository content" not in detail
    assert error.value.call is not None


def test_contract_failure_diagnostics_omit_custom_validator_messages():
    from pydantic import model_validator

    from app.contracts import Contract

    class Rejected(Contract):
        value: str

        @model_validator(mode="after")
        def reject(self):
            raise ValueError(f"private provider data: {self.value}")

    async def run():
        transport = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=completion({"value": TEST_KEY}))
            )
        )
        async with MetaClient(settings(), http_client=transport) as client:
            return await client.structured(stage="contract", messages=[], schema=Rejected)

    with pytest.raises(ModelOutputError) as error:
        asyncio.run(run())
    assert "Rejected at contract: <root> (value_error)" in str(error.value)
    assert TEST_KEY not in str(error.value) and "private provider data" not in str(error.value)


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_provider_errors_do_not_expose_response_bodies_or_credentials(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": f"echo {TEST_KEY}"}})

    with pytest.raises(MetaRequestError, match=f"HTTP {status}") as error:
        asyncio.run(structured_with(handler))
    assert TEST_KEY not in str(error.value)


@pytest.mark.parametrize(
    "failure", ["not_called", "unknown_tool", "arguments", "multiple", "id", "wrong_token"]
)
def test_tool_probe_refuses_unproven_or_unregistered_execution(failure: str) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            if failure == "not_called":
                return httpx.Response(200, json=completion("I skipped the tool."))
            call = json.loads(json.dumps(TOOL_CALL))
            if failure == "unknown_tool":
                call["function"]["name"] = "delete_file"
            elif failure == "arguments":
                call["function"]["arguments"] = '{"path":"secret.env"}'
            elif failure == "id":
                call["id"] = ""
            calls = [call, call] if failure == "multiple" else [call]
            return httpx.Response(200, json=completion(calls=calls))
        assert failure == "wrong_token"
        body = json.loads(request.content)
        probe = json.loads(body["messages"][-1]["content"])
        assert probe["probe_token"] != "invented"
        return httpx.Response(
            200, json=completion({"tool_name": "read_probe", "probe_token": "invented"})
        )

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaClient(settings(), http_client=transport) as client:
            await probe_tool_round_trip(client)

    with pytest.raises(SmokeCheckError):
        asyncio.run(run())
    assert count == (2 if failure == "wrong_token" else 1)


def test_missing_credentials_fail_before_client_creation() -> None:
    with pytest.raises(ConfigurationError, match="MODEL_API_KEY"):
        MetaClient(settings(model_api_key=None))


def test_plan_probe_rejects_paths_outside_the_provided_inventory() -> None:
    async def run():
        transport = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json=completion({**PLAN, "target_focus_paths": ["secrets.txt"]})
                )
            )
        )
        async with MetaClient(settings(), http_client=transport) as client:
            await run_smoke(client)

    with pytest.raises(SmokeCheckError, match="outside"):
        asyncio.run(run())
