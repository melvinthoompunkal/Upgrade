import asyncio
import json
import sys
from typing import cast
from uuid import uuid4

from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from app.config import Settings
from app.contracts import RunPlan
from app.errors import GraftError, SmokeCheckError
from app.models.meta_client import MetaClient
from app.smoke_contracts import ProbeAcknowledgement, ProbeArguments, SmokeReport, ToolProbeResult

SOURCE_PATHS = {"README.md", "scrapy/downloadermiddlewares/retry.py"}
TARGET_PATHS = {"pyproject.toml", "src/crawler.py"}


async def probe_tool_round_trip(client: MetaClient) -> ToolProbeResult:
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
    completion, first_call = await client.request(
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
        raise SmokeCheckError("Meta did not make exactly one probe tool call; round trip unproven.")
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
    final = await client.structured(
        stage="tool_probe.response", messages=messages, schema=ProbeAcknowledgement
    )
    if final.data.probe_token != probe_token:
        raise SmokeCheckError("Meta did not return the token provided by the probe tool.")
    return ToolProbeResult(acknowledgement=final.data, calls=[first_call, final.call])


async def run_smoke(client: MetaClient) -> SmokeReport:
    plan = await client.structured(
        stage="schema_probe",
        schema=RunPlan,
        messages=[
            {
                "role": "developer",
                "content": (
                    "This is an API schema smoke check using synthetic inventories. "
                    "It is not a repo audit. "
                    "Plan one source trace and one target profile, exactly once each. "
                    "Use only the supplied file paths. "
                    "Return a concise rationale, not private reasoning."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": "Improve crawler reliability with a small isolated feature.",
                        "source_inventory": sorted(SOURCE_PATHS),
                        "target_inventory": sorted(TARGET_PATHS),
                    }
                ),
            },
        ],
    )
    if (
        not set(plan.data.source_focus_paths) <= SOURCE_PATHS
        or not set(plan.data.target_focus_paths) <= TARGET_PATHS
    ):
        raise SmokeCheckError("Meta's plan referenced a path outside the supplied probe inventory.")
    tool_probe = await probe_tool_round_trip(client)
    return SmokeReport(
        status="passed",
        check="meta_schema_and_tool_round_trip",
        plan=plan.data,
        tool_round_trip=True,
        calls=[plan.call, *tool_probe.calls],
    )


async def _run_configured() -> SmokeReport:
    async with MetaClient(Settings()) as client:
        try:
            return await run_smoke(client)
        except GraftError as exc:
            exc.calls = tuple(client.calls)
            exc.attempts = client.attempts
            raise


def main() -> int:
    try:
        from app.execution import ProcessLock
        from app.tools.runner import PROJECT_ROOT

        with ProcessLock(PROJECT_ROOT / "data/execution.lock"):
            report = asyncio.run(_run_configured())
    except GraftError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": exc.code,
                    "detail": str(exc),
                    "calls": [c.model_dump(mode="json") for c in exc.calls],
                    "attempts": exc.attempts,
                }
            ),
            file=sys.stderr,
        )
        return 1
    except (ValidationError, OSError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "invalid_configuration",
                    "detail": "Check backend/.env settings.",
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
