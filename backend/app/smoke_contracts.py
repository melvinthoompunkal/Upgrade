from typing import Literal

from app.contracts import Contract, FrozenList, ModelCall, NonEmptyString, RunPlan


class ProbeArguments(Contract):
    """The smoke tool takes no arguments and cannot read user files."""


class ProbeAcknowledgement(Contract):
    tool_name: Literal["read_probe"]
    probe_token: NonEmptyString


class ToolProbeResult(Contract):
    acknowledgement: ProbeAcknowledgement
    calls: FrozenList[ModelCall]


class SmokeReport(Contract):
    status: Literal["passed"]
    check: Literal["meta_schema_and_tool_round_trip"]
    plan: RunPlan
    tool_round_trip: bool
    calls: FrozenList[ModelCall]
