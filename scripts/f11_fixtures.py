"""Independent small repository examples for each reviewed F11 acceptance shape."""

from app.adaptation_contracts import AcceptanceSpec, AdaptationPlan
from app.contracts import Ref

HTTP_BEFORE = """async def fetch(url, *, client):
    response = await client.get(url)
    return response.json()
"""
HTTP_AFTER = """async def fetch(url, *, client):
    response = await client.get(url)
    if response.status_code == 503:
        response = await client.get(url)
    return response.json()
"""
METHOD_BEFORE = """class Reader:
    def fetch(self, url, *, client):
        return client.get(url).json()
"""
METHOD_AFTER = """class Reader:
    def fetch(self, url, *, client):
        response = client.get(url)
        if response.status_code == 503:
            response = client.get(url)
        return response.json()
"""
ASYNC_BEFORE = "async def normalize(value):\n    return value\n"
ASYNC_AFTER = "async def normalize(value):\n    return value.strip()\n"
NEW_BEFORE = "def existing(value):\n    return value\n"
NEW_AFTER = NEW_BEFORE + "\ndef normalize(value):\n    return value.strip()\n"
NEW_METHOD_BEFORE = "class Parser:\n    def existing(self, value):\n        return value\n"
NEW_METHOD_AFTER = NEW_METHOD_BEFORE + (
    "    def normalize(self, value):\n        return value.strip()\n"
)
METHOD_PLAIN_BEFORE = "class Parser:\n    def normalize(self, value):\n        return value\n"
METHOD_PLAIN_AFTER = METHOD_PLAIN_BEFORE.replace("return value", "return value.strip()")

HTTP_TEST = """import httpx
from api import fetch
async def test_native_success():
    import anyio
    await anyio.sleep(0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"value": 17})
    )) as client:
        assert await fetch("https://fixture.invalid/value", client=client) == {"value": 17}
"""
METHOD_TEST = """import httpx
from api import Reader
def test_native_success():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"value": 17})
    )) as client:
        assert Reader().fetch("https://fixture.invalid/value", client=client) == {"value": 17}
"""
EXAMPLES = {
    "async_http": (HTTP_BEFORE, HTTP_AFTER, HTTP_TEST, "function", None, True, True),
    "method_http": (
        METHOD_BEFORE,
        METHOD_AFTER,
        METHOD_TEST,
        "method",
        "Reader",
        False,
        True,
    ),
    "async_function": (
        ASYNC_BEFORE,
        ASYNC_AFTER,
        "from api import normalize\nasync def test_native():\n"
        "    assert await normalize('x') == 'x'\n",
        "function",
        None,
        True,
        False,
    ),
    "instance_method": (
        METHOD_PLAIN_BEFORE,
        METHOD_PLAIN_AFTER,
        "from api import Parser\ndef test_native():\n    assert Parser().normalize('x') == 'x'\n",
        "method",
        "Parser",
        False,
        False,
    ),
    "new_function": (
        NEW_BEFORE,
        NEW_AFTER,
        "from api import existing\ndef test_native():\n    assert existing(' x ') == ' x '\n",
        "new_function",
        None,
        False,
        False,
    ),
    "new_method": (
        NEW_METHOD_BEFORE,
        NEW_METHOD_AFTER,
        "from api import Parser\ndef test_native():\n"
        "    assert Parser().existing(' x ') == ' x '\n",
        "new_method",
        "Parser",
        False,
        False,
    ),
}


def contracts(source, target, example):
    before, after, _, kind, class_name, asynchronous, http = example

    def reference(repo, code):
        return Ref(
            repo_id=repo.snapshot.repo_id,
            commit_sha=repo.snapshot.commit_sha,
            path="src/api.py",
            start_line=1,
            end_line=len(code.splitlines()),
            symbol=None,
        )

    plan = AdaptationPlan(
        schema_version="python-integration-plan-v2",
        included=[
            {
                "id": "behavior",
                "behavior": "Retry one 503." if http else "Strip whitespace.",
                "failure_semantics": "Propagate other failures unchanged.",
                "source_refs": [reference(source, after)],
            }
        ],
        excluded=["No streaming, shared event loop, new dependencies or additional retries."],
        integration_refs=[reference(target, before)],
        production_paths=["src/api.py"],
        new_runtime_dependencies=[],
        rationale="Exercise the explicit reviewed integration API.",
    )
    scenario = None
    if http:
        scenario = {
            "client_parameter": "client",
            "exchanges": [
                {
                    "method": "GET",
                    "url": "https://fixture.invalid/value",
                    "request_json": None,
                    "status_code": status,
                    "response_json": body,
                    "headers_json": "{}",
                }
                for status, body in [(503, '{"error":"busy"}'), (200, '{"value":17}')]
            ],
        }
    spec = AcceptanceSpec(
        schema_version="python-integration-acceptance-v2",
        cases=[
            {
                "id": "retry" if http else "strip",
                "rule_id": "behavior",
                "module": "api",
                "function": "fetch" if http else "normalize",
                "args_json": '["https://fixture.invalid/value"]' if http else '[" x "]',
                "kwargs_json": "{}",
                "expected_json": '{"value":17}' if http else '"x"',
                "expected_exception": None,
                "exception_message": None,
                "invocation": {
                    "kind": kind,
                    "class_name": class_name,
                    "constructor_args_json": "[]",
                    "constructor_kwargs_json": "{}",
                    "await_result": asynchronous,
                    "http": scenario,
                },
            }
        ],
    )
    return plan, spec
