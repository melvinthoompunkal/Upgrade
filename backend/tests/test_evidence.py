import pytest

from app.contracts import Ref
from app.errors import EvidenceError
from app.evidence_contracts import FeatureCard, SourceTrace
from app.tools.evidence import python_index


def ref(session, **changes):
    return Ref(
        repo_id="source",
        commit_sha=session.repo.snapshot.commit_sha,
        path="src/retry.py",
        start_line=3,
        end_line=4,
        symbol="retry",
        **changes,
    )


def card(session):
    r = ref(session)
    return FeatureCard(
        feature_id="retry",
        name="Retry",
        behavior="Returns the retry count.",
        entry_points=[r],
        implementation=[r],
        dependencies=[],
        source_tests=[],
        acceptance_behaviors=["Bound the attempt count."],
        transferable_slice="Attempt count only.",
        excluded_behaviors=["Scheduling"],
        license_spdx=None,
        license_refs=[],
        evidence_status="supported",
    )


def test_ast_qualified_definitions_imports_assignments_and_parse_errors():
    index = python_index(
        "a.py",
        "import time\nLIMIT = 2\nclass Retry:\n    async def run(self):\n        return LIMIT\n",
    )
    assert {s.name for s in index.symbols} == {"time", "LIMIT", "Retry", "Retry.run"}
    assert index.imports == ("time",)
    assert python_index("a.py", "not Python !!").parse_error


def test_ranges_require_valid_bounds_and_citations_require_read_evidence(session):
    with pytest.raises(EvidenceError, match="did not read"):
        session.validate_ref(ref(session))
    session.read_range("src/retry.py", 1, 4)
    excerpt = session.validate_ref(ref(session))
    assert excerpt["text"] == "def retry():\n    return 2\n"
    for start, end in [(0, 1), (2, 1), (1, 201), (1, 5)]:
        with pytest.raises(EvidenceError):
            session.read_range("src/retry.py", start, end)
    for changes in (
        {"commit_sha": "f" * 40},
        {"repo_id": "other"},
        {"symbol": "invented"},
        {"end_line": 5},
    ):
        r = ref(session).model_dump()
        r.update(changes)
        with pytest.raises(EvidenceError):
            session.validate_ref(Ref.model_validate(r))


def test_trace_coverage_is_derived_and_dependencies_require_cited_imports(session):
    session.read_range("src/retry.py", 1, 4)
    c = card(session)
    trace = SourceTrace(
        source_sha=session.repo.snapshot.commit_sha,
        features=[c],
        coverage={
            "examined_paths": ["invented.py"],
            "excluded_paths": [],
            "unexamined_areas": [],
            "limits_reached": [],
        },
    )
    validated = session.validate_trace(trace)
    assert validated.coverage.examined_paths == ("src/retry.py",)
    assert "pyproject.toml" in validated.coverage.unexamined_areas
    import_ref = Ref(
        **{**ref(session).model_dump(), "start_line": 1, "end_line": 1, "symbol": "time"}
    )
    session._validate_dependency("time", "stdlib", (import_ref,))
    with pytest.raises(EvidenceError):
        session._validate_dependency("time", "runtime", (import_ref,))
    with pytest.raises(EvidenceError):
        session._validate_dependency("imaginary", "runtime", (import_ref,))
    with pytest.raises(EvidenceError):
        session._validate_dependency("time", "stdlib", (ref(session),))


def test_locked_collections_cannot_bypass_validation(session):
    c = card(session)
    with pytest.raises(AttributeError):
        c.implementation.append(ref(session))
    assert FeatureCard.model_validate_json(c.model_dump_json()) == c


def test_supported_trace_cannot_omit_direct_runtime_coupling(session):
    # A new committed source import changes the static dependency boundary.
    from app.tools.evidence import python_index

    session.read_range("src/retry.py", 1, 4)
    session.indexes["src/retry.py"] = python_index(
        "src/retry.py", "import third_party\ndef retry(): pass\n"
    )
    c = card(session).model_dump(mode="json")
    # Use text evidence to isolate this test's dependency check from symbol checking.
    c["entry_points"][0]["symbol"] = c["implementation"][0]["symbol"] = None
    trace = SourceTrace(
        source_sha=session.repo.snapshot.commit_sha,
        features=[FeatureCard.model_validate(c)],
        coverage={
            "examined_paths": [],
            "excluded_paths": [],
            "unexamined_areas": [],
            "limits_reached": [],
        },
    )
    with pytest.raises(EvidenceError, match="runtime source imports"):
        session.validate_trace(trace)
