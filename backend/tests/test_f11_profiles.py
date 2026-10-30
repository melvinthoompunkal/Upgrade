"""Reviewed adapter contracts, truthful gaps, exact licenses and immutable ref resolution."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.adaptation_acceptance import render_acceptance, validate_acceptance
from app.adaptation_contracts import AcceptanceSpec, AdaptationPlan
from app.adaptation_policy import MIT_BODY
from app.errors import EvidenceError
from app.invocation_contracts import Invocation
from app.models.schema import response_format
from app.project_adapters import execution_reasons
from app.project_privacy import require_project_analysis
from app.project_refs import RefRequest, resolve_ref
from app.runtime_profiles import validate_preparation
from app.source_use import BSD_BODY, validate_notice
from app.tools.evidence import python_index
from scripts.f11_fixtures import EXAMPLES, contracts


def fixture(name="async_http"):
    example = EXAMPLES[name]
    source = SimpleNamespace(snapshot=SimpleNamespace(repo_id="source", commit_sha="a" * 40))
    target = SimpleNamespace(
        snapshot=SimpleNamespace(repo_id="target", commit_sha="b" * 40),
        read=lambda path: example[0],
    )
    return target, *contracts(source, target, example)


@pytest.mark.parametrize("name", EXAMPLES)
def test_each_explicit_integration_profile_has_valid_strict_contract_and_frozen_tests(name):
    repo, plan, spec = fixture(name)
    validate_acceptance(spec, plan, repo)
    assert spec.cases[0].invocation.kind in EXAMPLES[name]
    text = render_acceptance(spec)
    compile(text, "controller-generated.py", "exec")  # Compile only; never import target code.
    assert b"F7_EXPECTATION:" in text and b"asyncio.run" in text
    assert response_format(AcceptanceSpec) and response_format(AdaptationPlan)


def test_preparation_manifest_cannot_advertise_uninstalled_packages():
    profile = validate_preparation()
    assert profile["runtime_packages"] == {"httpx": "0.28.1", "anyio": "4.15.1"}


@pytest.mark.parametrize(
    "change",
    [
        {"await_result": False},
        {"class_name": "Missing", "kind": "method"},
        {"kind": "new_function"},
    ],
)
def test_acceptance_rejects_wrong_sync_class_and_existing_new_api(change):
    repo, plan, spec = fixture()
    raw = spec.model_dump(mode="json")
    raw["cases"][0]["invocation"].update(change)
    with pytest.raises(EvidenceError):
        validate_acceptance(AcceptanceSpec.model_validate(raw), plan, repo)


def test_profiles_cannot_mix_cases_or_silently_downgrade():
    repo, plan, spec = fixture()
    with pytest.raises(EvidenceError, match="versions"):
        validate_acceptance(
            spec, plan.model_copy(update={"schema_version": "python-function-plan-v1"}), repo
        )
    with pytest.raises(ValidationError):
        AcceptanceSpec.model_validate(
            {
                **spec.model_dump(mode="json"),
                "schema_version": "python-function-acceptance-v1",
            }
        )


@pytest.mark.parametrize("args", ["{}", "[NaN]", "[1e1000]", "[" * 100 + "]" * 100])
def test_constructor_arguments_are_bounded_json(args):
    _, _, spec = fixture("instance_method")
    data = spec.cases[0].invocation.model_dump(mode="json")
    with pytest.raises(ValidationError):
        Invocation.model_validate({**data, "constructor_args_json": args})


@pytest.mark.parametrize("spdx,body", [("MIT", MIT_BODY), ("BSD-2-Clause", BSD_BODY)])
def test_standard_notices_are_exact_and_additional_obligations_are_rejected(spdx, body):
    notice = f"Copyright 2026 River contributors\n{body}\n"
    validate_notice(notice, spdx, MIT_BODY)
    for changed in (
        notice + "Additional conditions apply.",
        notice.replace("copyright notice", "notice", 1),
    ):
        with pytest.raises(EvidenceError):
            validate_notice(changed, spdx, MIT_BODY)


@pytest.mark.parametrize(
    "name", ["../main", "a..b", "main.lock", "x?", "x\ny", "-x", "a//b", "x@{1}"]
)
def test_invalid_refs_never_reach_github(name):
    with pytest.raises(ValidationError):
        RefRequest(url="https://github.com/team/repo", kind="branch", name=name)


@pytest.mark.parametrize("kind,annotated", [("branch", False), ("tag", False), ("tag", True)])
def test_branch_and_tags_resolve_to_one_commit_and_never_follow_provider_urls(
    monkeypatch, kind, annotated
):
    from app import project_refs

    calls = []
    namespace = "heads" if kind == "branch" else "tags"

    def request(method, path, **kwargs):
        calls.append(path)
        assert kwargs["token"] is None
        if "/git/tags/" in path:
            return {"object": {"type": "commit", "sha": "b" * 40}}
        return {
            "ref": f"refs/{namespace}/release/v2",
            "object": {
                "type": "tag" if annotated else "commit",
                "sha": "a" * 40,
                "url": "https://untrusted.invalid/never-follow",
            },
        }

    monkeypatch.setattr(
        project_refs, "GitHubClient", lambda settings: SimpleNamespace(request=request)
    )
    context = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=None)),
        state=SimpleNamespace(principal=SimpleNamespace(owner_id="owner")),
    )
    result = resolve_ref(
        RefRequest(url="https://github.com/team/repo", kind=kind, name="release/v2"), context
    )
    assert result.commit_sha == ("b" if annotated else "a") * 40
    assert calls == [f"/repos/team/repo/git/ref/{namespace}/release%2Fv2"] + (
        [f"/repos/team/repo/git/tags/{'a' * 40}"] if annotated else []
    )


def test_private_snapshot_alias_cannot_enable_any_analysis():
    from fastapi import HTTPException

    for selected in [
        SimpleNamespace(private=True, repository_snapshot=None),
        SimpleNamespace(private=False, repository_snapshot="a" * 32),
    ]:
        with pytest.raises(HTTPException) as error:
            require_project_analysis(selected)
        assert error.value.status_code == 409


@pytest.mark.parametrize(
    "code",
    [
        "normalize = lambda value: value\n",
        "from other import normalize\n",
        "normalize: str = 'existing API'\n",
    ],
)
def test_new_api_cannot_overwrite_an_existing_binding(code):
    repo, plan, spec = fixture("new_function")
    repo.read = lambda path: code
    with pytest.raises(EvidenceError, match="absent"):
        validate_acceptance(spec, plan, repo)


@pytest.mark.parametrize(
    "test,reason",
    [
        ("@pytest.fixture\nasync def resource():\n    return 1\n", "Async fixtures"),
        (
            "@pytest.mark.anyio\nasync def test_native():\n    assert True\n",
            "backend or shared-loop",
        ),
        (
            "@pytest.mark.asyncio(loop_scope='session')\n"
            "async def test_native():\n    assert True\n",
            "backend or shared-loop",
        ),
    ],
)
def test_native_async_preflight_rejects_unreviewed_fixture_or_backend_semantics(test, reason):
    files = {"src/api.py": "VALUE = 1\n", "tests/test_api.py": "import pytest\n" + test}
    repo = SimpleNamespace(
        read=files.__getitem__,
        inventory=SimpleNamespace(
            files=[SimpleNamespace(path=path) for path in files],
            excluded_paths=[],
            limits_reached=[],
        ),
    )
    reasons = execution_reasons(
        repo,
        {"project": {"requires-python": ">=3.12", "dependencies": []}},
        {path: python_index(path, text) for path, text in files.items()},
    )
    assert any(reason in item for item in reasons)


@pytest.mark.parametrize(
    "code",
    [
        "class Parser:\n    @property\n    def normalize(self):\n        return 'value'\n",
        "class Parser:\n    @staticmethod\n    def normalize(value):\n        return value\n",
        "@decorated\nclass Parser:\n    def normalize(self, value):\n        return value\n",
    ],
)
def test_instance_methods_cannot_silently_be_properties_or_decorated_apis(code):
    repo, plan, spec = fixture("instance_method")
    repo.read = lambda path: code
    with pytest.raises(EvidenceError, match="undecorated"):
        validate_acceptance(spec, plan, repo)


def test_new_method_cannot_shadow_an_inherited_api():
    repo, plan, spec = fixture("new_method")
    repo.read = lambda path: "class Parser(Base):\n    pass\n"
    with pytest.raises(EvidenceError, match="inherited"):
        validate_acceptance(spec, plan, repo)


def test_http_script_preserves_json_null(monkeypatch):
    import sys

    from app.invocation_runtime import _integration_call

    # Trusted inline stub exercises the controller helper, never repository code.
    module = SimpleNamespace(fetch=lambda url, *, client: client.get(url).json())
    monkeypatch.setitem(sys.modules, "_f11_trusted_stub", module)
    _, _, spec = fixture()
    invocation = spec.cases[0].invocation.model_dump(mode="json")
    invocation["await_result"] = False
    exchange = invocation["http"]["exchanges"][0]
    invocation["http"]["exchanges"] = [{**exchange, "status_code": 200, "response_json": "null"}]
    assert (
        _integration_call(
            "_f11_trusted_stub",
            "fetch",
            ["https://fixture.invalid/value"],
            {},
            invocation,
            "F7_EXPECTATION:null",
            None,
            None,
        )
        is None
    )
