import asyncio
import io
import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel
from test_meta_client import PLAN, completion, settings

from app.contracts import Contract, RunPlan
from app.errors import BudgetError, ConfigurationError, ModelOutputError
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.models.schema import response_format
from app.tools import runner
from app.tools.runner_artifacts import RunnerError, read_regular_file


@pytest.mark.parametrize("schema_kind", ["default", "unrestricted"])
def test_incompatible_strict_schemas_fail_locally(schema_kind):
    class Defaulted(Contract):
        note: str = ""

    class Unrestricted(BaseModel):
        note: str

    with pytest.raises(ConfigurationError):
        response_format(Defaulted if schema_kind == "default" else Unrestricted)


def test_plan_collections_are_immutable_and_wire_arrays_are_preserved():
    plan = RunPlan.model_validate(PLAN)
    with pytest.raises(AttributeError):
        plan.analysis_tasks.append("trace_source")
    assert json.loads(plan.model_dump_json())["analysis_tasks"] == PLAN["analysis_tasks"]


def test_failed_generation_retains_call_and_each_http_retry_is_accounted():
    async def run():
        count = 0

        def handler(_):
            nonlocal count
            count += 1
            if count < 3:
                return httpx.Response(500, json={"error": "private body"})
            payload = completion()
            payload["choices"][0]["message"]["content"] = "invalid-json"
            return httpx.Response(200, json=payload)

        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaClient(settings(meta_max_retries=2), http_client=transport) as client:
            with pytest.raises(ModelOutputError) as error:
                await client.structured(stage="audit", messages=[], schema=RunPlan)
            assert error.value.call.response_id == "chatcmpl-offline-fixture"
            assert error.value.call.attempts == 3
            assert client.budget.requests == count == len(client.attempts) == 3
            assert [a["usage"] for a in client.attempts][:2] == [None, None]
            assert client.budget.input_tokens == 12
            assert "private body" not in json.dumps(client.attempts)

    asyncio.run(run())


def test_budget_prevents_extra_http_attempt_and_expired_runs():
    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        async with MetaClient(
            settings(meta_max_retries=2), http_client=transport, budget=ModelBudget(max_requests=2)
        ) as client:
            with pytest.raises(BudgetError):
                await client.structured(stage="limited", messages=[], schema=RunPlan)
            assert len(client.attempts) == client.budget.requests == 2

    asyncio.run(run())
    with pytest.raises(BudgetError):
        ModelBudget(max_seconds=0).reserve()


def make_link(link, destination):
    import os
    import subprocess

    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(destination)],
            capture_output=True,
            check=True,
        )
    else:
        link.symlink_to(destination, target_is_directory=True)


def test_linked_root_and_linked_ancestor_are_rejected(tmp_path):
    physical = tmp_path / "physical"
    (physical / "nested").mkdir(parents=True)
    (physical / "nested/file").write_text("harmless")
    link = tmp_path / "alias"
    make_link(link, physical)
    with pytest.raises(RunnerError, match="links or junctions"):
        read_regular_file(link, "nested/file")
    with pytest.raises(RunnerError, match="links or junctions"):
        read_regular_file(link / "nested", "file")
    assert read_regular_file(physical, "nested/file") == b"harmless"


@pytest.mark.parametrize("filename", ["stdout.txt", "stderr.txt"])
@pytest.mark.parametrize("operation", ["open", "write", "read"])
def test_each_stream_io_failure_is_owned_and_reported(tmp_path, monkeypatch, filename, operation):
    class FaultStream(io.BytesIO):
        def read(self, size=-1):
            if operation == "read":
                raise OSError("sensitive host details")
            return super().read(size)

    class Process:
        args = ["fake", "attach"]
        returncode = 0
        stdout = FaultStream(b"out") if filename == "stdout.txt" else io.BytesIO(b"out")
        stderr = FaultStream(b"err") if filename == "stderr.txt" else io.BytesIO(b"err")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    original_open = Path.open

    class FaultOutput:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, content):
            raise OSError("sensitive host details")

    def open_file(path, mode="r", *args, **kwargs):
        if path.name == filename and mode == "wb":
            if operation == "open":
                raise OSError("sensitive host details")
            if operation == "write":
                return FaultOutput()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(runner, "_stop_container", lambda name: None)
    _, diagnostic = runner._attach("owned-test", tmp_path, 1)
    assert filename in diagnostic and "I/O error" in diagnostic
    assert "sensitive" not in diagnostic


def test_missing_log_cannot_yield_successful_capture(tmp_path, monkeypatch):
    directory = tmp_path / "capture"

    def docker(*args):
        if args[0] == "create":
            (directory / "results/junit.xml").write_text(
                '<testsuite><testcase name="ok"/></testsuite>'
            )
        if args[0] == "inspect":
            return '{"ExitCode": 0}'
        return ""

    monkeypatch.setattr(runner, "docker", docker)
    monkeypatch.setattr(runner, "_attach", lambda *args: (0, ""))
    capture = runner.run_suite(
        suite="isolation",
        image_id="sha256:test",
        snapshot=tmp_path,
        acceptance=tmp_path,
        directory=directory,
    )
    assert capture.status == "error"
    assert "missing stdout/stderr" in capture.diagnostic
