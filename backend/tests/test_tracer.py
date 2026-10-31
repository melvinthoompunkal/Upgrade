import asyncio
import json

import httpx
import pytest
from test_evidence import card
from test_meta_client import completion, settings

from app.catalog_contracts import CandidateBatch
from app.contracts import Ref
from app.errors import EvidenceError, UnsupportedSourceError
from app.evidence_tools import EvidenceTools, collect_evidence
from app.models.meta_client import MetaClient
from app.tracer import SourceTracer


def test_unsupported_source_stops_before_any_model_request(session):
    async def run():
        def reject_request(_):
            pytest.fail("Unsupported sources must not consume model requests.")

        transport = httpx.AsyncClient(transport=httpx.MockTransport(reject_request))
        async with MetaClient(settings(), http_client=transport) as client:
            with pytest.raises(UnsupportedSourceError, match="lacks the Scrapy retry"):
                await SourceTracer(client, session).trace("Increase web scraping capabilities.")
            assert client.calls == [] and client.budget.requests == 0
            assert session.windows == []

    asyncio.run(run())


def test_real_tool_protocol_builds_validated_trace_from_pinned_reads(session, monkeypatch):
    monkeypatch.setattr(
        "app.tracer.FOCUS_PATHS", ("src/retry.py", "pyproject.toml", "tests/test_retry.py")
    )
    requests = []

    def reference(path, start, end, symbol=None):
        return Ref(
            repo_id="source",
            commit_sha=session.repo.snapshot.commit_sha,
            path=path,
            start_line=start,
            end_line=end,
            symbol=symbol,
        )

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["reasoning_effort"] == "medium"
        assert body["max_completion_tokens"] == 8000
        assert {t["function"]["name"] for t in body["tools"]} == {
            "read_range",
            "python_evidence",
            "search_symbols",
        }
        if len(requests) == 1:
            calls = []
            for path, end in [
                ("src/retry.py", 4),
                ("pyproject.toml", 3),
                ("tests/test_retry.py", 2),
            ]:
                calls.append(
                    {
                        "id": f"read-{len(calls)}",
                        "type": "function",
                        "function": {
                            "name": "read_range",
                            "arguments": json.dumps(
                                {"path": path, "start_line": 1, "end_line": end}
                            ),
                        },
                    }
                )
            return httpx.Response(200, json=completion(calls=calls))
        results = [m for m in body["messages"] if m["role"] == "tool"]
        assert len(results) == 3
        assert "def retry" in json.loads(results[0]["content"])["text"]
        c = card(session).model_dump(mode="json")
        c["implementation"].append(reference("pyproject.toml", 1, 3).model_dump(mode="json"))
        c["source_tests"] = [
            reference("tests/test_retry.py", 1, 2, "test_retry").model_dump(mode="json")
        ]
        data = {
            "source_sha": session.repo.snapshot.commit_sha,
            "features": [c],
            "coverage": {
                "examined_paths": [],
                "excluded_paths": [],
                "unexamined_areas": [],
                "limits_reached": [],
            },
        }
        return httpx.Response(200, json=completion(data))

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaClient(settings(), http_client=transport) as client:
            tracer = SourceTracer(client, session)
            trace = await tracer.trace("bounded retries")
            assert trace.features[0].evidence_status == "supported"
            assert len(tracer.events) == 3
            assert client.budget.requests == 2

    asyncio.run(run())


def test_unregistered_tools_and_invalid_ranges_are_never_executed(session):
    tracer = SourceTracer(None, session)
    for name, args in [
        ("shell", '{"command":"echo unsafe"}'),
        ("read_range", '{"path":"../outside","start_line":1,"end_line":2}'),
        ("read_range", '{"path":"src/retry.py","start_line":1,"end_line":500}'),
    ]:
        with pytest.raises(EvidenceError):
            tracer.dispatch(name, args)
    assert session.windows == []


def test_final_evidence_turn_returns_a_result_within_the_existing_budget(session):
    requests = []
    tools = EvidenceTools(session, ("src/retry.py",))

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) < 3:
            return httpx.Response(
                200,
                json=completion(
                    calls=[
                        {
                            "id": f"read-{len(requests)}",
                            "type": "function",
                            "function": {
                                "name": "read_range",
                                "arguments": json.dumps(
                                    {
                                        "path": "src/retry.py",
                                        "start_line": 1,
                                        "end_line": 4,
                                    }
                                ),
                            },
                        }
                    ]
                ),
            )
        assert "tools" not in body and "tool_choice" not in body
        assert "final structured" in body["messages"][-1]["content"]
        assert len([m for m in body["messages"] if m["role"] == "tool"]) == 2
        return httpx.Response(
            200,
            json=completion(
                {
                    "candidates": [
                        {
                            "behavior_key": "retry-count",
                            "name": "Retry count",
                            "summary": "Returns a count.",
                            "category": "retry",
                            "parent_key": None,
                            "discovery_refs": [
                                {
                                    "repo_id": "source",
                                    "commit_sha": session.repo.snapshot.commit_sha,
                                    "path": "src/retry.py",
                                    "start_line": 3,
                                    "end_line": 4,
                                    "symbol": "retry",
                                }
                            ],
                        }
                    ]
                }
            ),
        )

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaClient(settings(), http_client=transport) as client:
            result = await collect_evidence(
                client,
                tools,
                [{"role": "user", "content": "Discover"}],
                CandidateBatch,
                stage="catalog.discover",
                turns=3,
            )
            session.validate_ref(result.candidates[0].discovery_refs[0])
            assert client.budget.requests == 3 and len(tools.events) == 2

    asyncio.run(run())
