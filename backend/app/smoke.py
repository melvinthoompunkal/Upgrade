import asyncio
import json
import sys

from pydantic import ValidationError

from app.config import Settings
from app.contracts import RunPlan, SmokeReport
from app.errors import GraftError, SmokeCheckError
from app.models.meta_client import MetaClient

SOURCE_PATHS = {"README.md", "scrapy/downloadermiddlewares/retry.py"}
TARGET_PATHS = {"pyproject.toml", "src/crawler.py"}


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
    tool_probe = await client.probe_tool_round_trip()
    return SmokeReport(
        status="passed",
        check="meta_schema_and_tool_round_trip",
        plan=plan.data,
        tool_round_trip=True,
        calls=[plan.call, *tool_probe.calls],
    )


async def _run_configured() -> SmokeReport:
    async with MetaClient(Settings()) as client:
        return await run_smoke(client)


def main() -> int:
    try:
        report = asyncio.run(_run_configured())
    except GraftError as exc:
        print(
            json.dumps({"status": "failed", "error_code": exc.code, "detail": str(exc)}),
            file=sys.stderr,
        )
        return 1
    except ValidationError:
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
