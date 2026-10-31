"""FE corpus: real independent Git bytes, parserless retrieval and controlled provider IO."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from test_meta_client import completion, settings

from app.adaptation_inputs import adaptation_inputs
from app.api.artifacts import RunArtifacts, digest, summary_from_report
from app.api.storage import RunStore
from app.catalog_contracts import CatalogContinue, CatalogCreate, CatalogJob, DiscoveryOptions
from app.catalog_discovery import matching_candidate
from app.catalog_store import CatalogStore, source_identity
from app.catalog_workflow import record_catalog
from app.errors import EvidenceError
from app.models.meta_client import MetaClient
from app.models.schema import strict_schema
from app.project_contracts import ProjectRequest, ProjectSelection
from app.projects import ProjectStore, inventory_digest
from app.relevance_contracts import AssessmentCreate, RelevanceJob, SemanticJudgmentV2
from app.relevance_store import RelevanceStore
from app.relevance_workflow import record_relevance
from app.text_contracts import (
    LEGACY_POLICY,
    TEXT_POLICY,
    CandidateBatchV2,
    FeatureCardV2,
    ListPaths,
    SourceTraceV2,
    TextInventory,
    TextRefV2,
    TextSearch,
)
from app.text_discovery import text_packet
from app.text_evidence import TextEvidenceSession, TextEvidenceTools
from app.tools.git import Git

OWNER = "local-workspace"


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    root.mkdir()
    git = Git(tmp_path / "control")
    git.run("init", "--template=", str(root))
    files = {
        "web/README.md": "# Tools\n## Encryption\nAdvertised encryption only.\n",
        "web/src/normalize.ts": "// padding\n" * 120
        + "//"
        + "x" * 7000
        + "\n"
        + "// more padding\n" * 60
        + "export function normalize(input: string) {\n  return input.trim().toLowerCase();\n}\n",
        "web/src/route.yaml": "routes:\n  - path: /health\n    response: healthy\n",
        "web/src/imports.ts": "import {\n  normalize,\n} from './normalize';\n",
        "web/dynamic/prose.py": '"""Advertised encryption\nNo implementation exists.\n"""\n',
        "web/src/prose.ts": "/* Advertised encryption\nNo implementation exists.\n*/\n",
        "web/src/many.ts": "\n".join(f"export const marker{i} = {i};" for i in range(35)) + "\n",
        "web/docs/README.md": "# Advertised encryption\nNo implementation is included.\n",
        "web/.npmrc": "never_expose_this_configuration_value\n",
        "bare/core.py": "def normalize(value):\n    return value.strip()\n",
        "javascript/main.js": "export function normalize(value) { return value.trim(); }\n",
        "web/tests/helper.test.ts": "export function normalize() { return 'test only'; }\n",
        "web/core/normalize.rs": "pub fn normalize(value: &str) -> &str { value.trim() }\n",
        "web/dynamic/pyproject.toml": "[project]\ndynamic=['name']\n",
        "web/dynamic/main.py": "def normalize(value):\n    return value.strip()\n",
        "web/unknown/parserless.newlang": "module normalize\ntransform input => trim(input)\n",
        "web/bin/tool": "#!/usr/bin/env node\nprocess.stdout.write('hello');\n",
        "web/LICENSE": "MIT License\nCopyright corpus\n",
        "web/package.json": '{"name":"corpus","scripts":{"test":"never execute"}}\n',
        "web/node_modules/ignored.ts": "throw new Error('never run');\n",
        "web/.env": "NEVER_EXPORT=private\n",
        "web/binary.bin": "binary\x00data",
        "web/notebook.ipynb": "{}\n",
        "target/main.go": "package main\nfunc render(input string) string { return input }\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    (root / "web/latin.txt").write_bytes(b"caf\xe9")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "independent text corpus", cwd=root)
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    original = Git.run

    def transport(git, *args, **kwargs):
        return original(
            git,
            *(str(root) if arg == "https://github.com/corpus/text" else arg for arg in args),
            **kwargs,
        )

    monkeypatch.setattr(Git, "run", transport)
    projects = ProjectStore(RunStore(tmp_path / "web/runs.sqlite3"), tmp_path / "projects")

    def pin(role="source", prefix="web", profile="repository-text-v1"):
        return projects.create(
            OWNER,
            ProjectRequest(
                url="https://github.com/corpus/text",
                commit_sha=sha,
                role=role,
                project_root=prefix,
                analysis_profile=profile,
            ),
        )

    return projects, pin, root, git


def reference(selected, path, start=1, end=1):
    return dict(
        schema_version="text-ref-v2",
        project_id=selected.id,
        project_root=selected.requested.project_root,
        role=selected.requested.role,
        repo_id=selected.snapshot.repo_id,
        commit_sha=selected.snapshot.commit_sha,
        path=path,
        start_line=start,
        end_line=end,
        symbol=None,
        blob_sha256=None,
        excerpt_sha256=None,
    )


def test_no_manifest_pin_policy_roles_scope_and_reopen(corpus):
    projects, pin, _, _ = corpus
    selected = pin()
    assert selected.support.analysis_available
    assert not selected.support.execution_supported
    assert selected.support.readable_source_files >= 5
    assert {"TypeScript", "Rust", "Python"} <= selected.support.detected_languages.keys()
    assert selected.inventory_policy == TEXT_POLICY
    reopened, repo = projects.open(OWNER, selected.id)
    assert inventory_digest(repo) == selected.inventory_sha256
    saved_inventory = TextInventory.model_validate_json(repo.inventory.model_dump_json())
    assert saved_inventory == repo.inventory
    assert all(
        f.role and f.classification_basis and f.blob_oid and f.lines >= 0
        for f in saved_inventory.files
    )
    assert all(f.path.startswith("web/") for f in repo.inventory.files)
    assert repo.read("web/src/normalize.ts")
    for path in ("target/main.go", "src/normalize.ts", "web/.env"):
        with pytest.raises(EvidenceError):
            repo.read(path)
    outcomes = {e.path: e.reason for e in repo.inventory.exclusions}
    assert outcomes["web/binary.bin"] == outcomes["web/latin.txt"] == "binary_or_non_utf8"
    assert outcomes["web/notebook.ipynb"] == "unsupported_compound_source_mapping"
    assert "web/node_modules" in outcomes
    assert reopened.support.profile_version == "repository-text-v1"
    with pytest.raises(HTTPException):
        projects.get("different-owner", selected.id)


@pytest.mark.parametrize(
    "scope", ["web/src", "web/core", "web/dynamic", "web/unknown", "target", "bare", "javascript"]
)
def test_unfamiliar_languages_and_dynamic_or_missing_metadata_do_not_gate_analysis(corpus, scope):
    projects, pin, _, _ = corpus
    selected = pin(prefix=scope)
    assert selected.support.analysis_available
    if scope in {"web/src", "bare", "javascript"}:
        assert not selected.support.manifest_paths
    if scope in {"web/src", "javascript"}:
        assert selected.support.python_files == 0
    _, repo = projects.open(OWNER, selected.id)
    session = TextEvidenceSession(repo, selected.id)
    first = repo.inventory.files[0]
    session.read_range(first.path, 1, 1)
    assert session.validate_ref(session.ref(first.path, 1, 1))["ref"]["blob_sha256"]


def test_legacy_policy_digest_and_descriptor_defaults_are_unchanged(corpus):
    projects, pin, _, _ = corpus
    old = pin(profile="python-evidence-v1")
    raw = old.model_dump(mode="json")
    raw.pop("inventory_policy")
    raw["requested"].pop("analysis_profile")
    historical = ProjectSelection.model_validate(raw)
    assert historical.inventory_policy == LEGACY_POLICY
    assert historical.support.profile_version == "python-evidence-v1"
    _, repo = projects.open(OWNER, old.id)
    assert inventory_digest(repo) == old.inventory_sha256
    assert "src/normalize.ts" not in {f.path for f in repo.inventory.files}
    modern = pin()
    assert modern.id != old.id and modern.inventory_sha256 != old.inventory_sha256
    assert source_identity(modern, "", "muse-spark-1.3") != source_identity(
        old, "", "muse-spark-1.3"
    )


def test_provenance_rejects_foreign_unread_and_stale_refs_and_invented_parser(corpus):
    projects, pin, _, _ = corpus
    selected = pin()
    _, repo = projects.open(OWNER, selected.id)
    session = TextEvidenceSession(repo, selected.id)
    session.read_range("web/src/normalize.ts", 182, 184)
    good = session.ref("web/src/normalize.ts", 182, 184)
    bound = TextRefV2.model_validate(session.validate_ref(good)["ref"])
    assert bound.blob_sha256 and bound.excerpt_sha256
    for change in (
        {"project_id": "0" * 32},
        {"project_root": "other"},
        {"role": "target"},
        {"commit_sha": "0" * 40},
        {"start_line": 1},
        {"end_line": 185},
        {"path": "web/not-found.ts"},
        {"blob_sha256": "0" * 64},
        {"excerpt_sha256": "0" * 64},
    ):
        with pytest.raises(EvidenceError):
            session.validate_ref(good.model_copy(update=change))
    card = FeatureCardV2.model_validate(card_data("ffeature", good.model_dump(mode="json")))
    assert session.validate_card(card).structural_validation == "text_only"
    with pytest.raises(EvidenceError, match="parser"):
        session.validate_card(card.model_copy(update={"structural_validation": "parser_validated"}))
    for path, start, end in (
        ("web/README.md", 1, 1),
        ("web/tests/helper.test.ts", 1, 1),
        ("web/src/imports.ts", 1, 3),
        ("web/src/imports.ts", 2, 2),
        ("web/dynamic/prose.py", 1, 3),
        ("web/src/prose.ts", 1, 3),
    ):
        session.read_range(path, 1, end)
        wrong = session.ref(path, start, end)
        with pytest.raises(EvidenceError, match="production"):
            session.validate_card(
                card.model_copy(update={"entry_points": (wrong,), "implementation": (wrong,)})
            )


def test_retrieval_beyond_heads_exact_hit_logs_and_unread_ranges(corpus):
    projects, pin, _, _ = corpus
    selected = pin()
    _, repo = projects.open(OWNER, selected.id)
    session = TextEvidenceSession(repo, selected.id)
    tools = TextEvidenceTools(session)
    hits = tools.dispatch(
        "search_text",
        json.dumps(
            {"query": "function normalize", "path_prefix": "web/src", "offset": 0, "start_line": 1}
        ),
    )
    ref = TextRefV2.model_validate(hits["matches"][0]["ref"])
    assert ref.start_line == 182
    session.validate_ref(ref)
    assert session.searches and session.coverage().partially_read_files
    data = text_packet(session, ["web/src/normalize.ts"], {"web/src/normalize.ts": 121})
    assert data[0]["window"]["start_line"] == 122
    assert "oversized_seed_lines_unread" in session.coverage().limits_reached
    assert any(
        121 in range(r.start_line, r.end_line + 1)
        for f in session.coverage().partially_read_files
        for r in f.unread_ranges
    )
    shortened = text_packet(session, ["web/src/normalize.ts"], {"web/src/normalize.ts": 101})
    assert shortened[0]["next_start"] == 121
    session.max_files = 1
    with pytest.raises(EvidenceError, match="64-file"):
        session.read_range("web/src/route.yaml", 1, 1)
    for tool in ("shell", "python_evidence"):
        with pytest.raises(EvidenceError):
            tools.dispatch(tool, "{}")


def test_search_paginates_inside_a_file_and_exposes_only_returned_hits(corpus):
    projects, pin, _, _ = corpus
    selected = pin(prefix="web/src")
    _, repo = projects.open(OWNER, selected.id)
    session = TextEvidenceSession(repo, selected.id)
    tools = TextEvidenceTools(session)
    first = tools.dispatch(
        "search_text",
        json.dumps(
            {
                "query": "marker",
                "path_prefix": "web/src/many.ts",
                "offset": 0,
                "start_line": 1,
            }
        ),
    )
    assert len(first["matches"]) == 20 and first["incomplete"]
    second = tools.dispatch(
        "search_text",
        json.dumps(
            {
                "query": "marker",
                "path_prefix": "web/src/many.ts",
                "offset": first["next_offset"],
                "start_line": first["next_start_line"],
            }
        ),
    )
    assert len(second["matches"]) == 15 and not second["incomplete"]
    assert len(session.windows) == 35 and session.read_bytes < 2000


def test_docs_only_availability_cannot_promote_advertising_to_implementation(corpus):
    projects, pin, _, _ = corpus
    selected = pin(prefix="web/docs")
    assert selected.support.analysis_available and not selected.support.readable_source_files
    _, repo = projects.open(OWNER, selected.id)
    session = TextEvidenceSession(repo, selected.id)
    session.read_range("web/docs/README.md", 1, 2)
    ref = session.ref("web/docs/README.md", 1, 2)
    card = FeatureCardV2.model_validate(card_data("fadvertised", ref.model_dump(mode="json")))
    with pytest.raises(EvidenceError, match="operative production"):
        session.validate_card(card)
    assert (
        session.validate_card(
            card.model_copy(update={"evidence_status": "partial"})
        ).evidence_status
        == "partial"
    )


def test_tree_budget_exclusions_symlinks_submodules_and_content_deferrals(corpus, tmp_path):
    from app.tools.repositories import RepositoryManager

    _, _, root, git = corpus
    base = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    link = (
        git.run("hash-object", "-w", "--stdin", cwd=root, input=b"../target/main.go")
        .decode()
        .strip()
    )
    git.run("update-index", "--add", "--cacheinfo", f"120000,{link},web/link", cwd=root)
    git.run("update-index", "--add", "--cacheinfo", f"160000,{base},web/submodule", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "nonordinary entries", cwd=root)
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    manager = RepositoryManager(tmp_path / "bounded", inventory_policy=TEXT_POLICY)
    storage = manager.directory / "source.git"
    git.run("clone", "--bare", "--no-local", "--template=", str(root), str(storage))
    repo = manager._inventory(storage, "source", "https://github.com/corpus/text", sha, "web")
    exclusions = {e.path: e.reason for e in repo.inventory.exclusions}
    assert (
        exclusions["web/link"]
        == exclusions["web/submodule"]
        == "symlink_submodule_or_nonordinary_blob"
    )
    assert "web/.npmrc" in exclusions
    assert not any(e.path.startswith("web/node_modules/") for e in repo.inventory.tree_entries)
    small = manager._inventory(
        storage, "source", "https://github.com/corpus/text", sha, "web", max_entries=4
    )
    assert not small.inventory.enumeration_complete and small.inventory.deferred_paths
    content = manager._inventory(
        storage, "source", "https://github.com/corpus/text", sha, "web", max_text_bytes=100
    )
    assert content.inventory.enumeration_complete and content.inventory.remaining_tree_entries == 0
    assert (
        content.inventory.deferred_paths
        and "analyzed_text_bytes" in content.inventory.limits_reached
    )


def card_data(feature_id, ref, *, partial=False):
    return {
        "schema_version": "feature-card-v2",
        "analysis_profile": "repository-text-v1",
        "feature_id": feature_id,
        "name": "Normalize text",
        "behavior": "Trim and lowercase text.",
        "entry_points": [] if partial else [ref],
        "implementation": [] if partial else [ref],
        "dependencies": [],
        "source_tests": [],
        "acceptance_behaviors": ["Trim whitespace"],
        "transferable_slice": "Conceptual normalization behavior",
        "excluded_behaviors": [],
        "license_spdx": None,
        "license_refs": [],
        "evidence_status": "partial" if partial else "supported",
        "structural_validation": "text_only",
        "symbol_anchors": [],
        "execution_verification": "not_run",
        "missing_evidence": ["No tests executed"],
    }


class TextProvider:
    def __init__(self, selected):
        self.selected, self.requests = selected, []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        inputs = json.loads(body["messages"][1]["content"])
        if "candidate" in inputs:
            candidate = inputs["candidate"]
            ref = candidate["discovery_refs"][0]
            card = card_data(
                candidate["feature_id"], ref, partial=candidate["behavior_key"] == "encryption"
            )
            return httpx.Response(
                200,
                json=completion(
                    {
                        "schema_version": "source-trace-v2",
                        "source_sha": self.selected.snapshot.commit_sha,
                        "features": [card],
                    }
                ),
            )
        paths = {item["path"] for item in inputs["packet"]}
        key, ref = None, None
        if "web/src/normalize.ts" in paths:
            if not any(m["role"] == "tool" for m in body["messages"]):
                return httpx.Response(
                    200,
                    json=completion(
                        calls=[
                            {
                                "id": "read-late-body",
                                "type": "function",
                                "function": {
                                    "name": "read_range",
                                    "arguments": json.dumps(
                                        {
                                            "path": "web/src/normalize.ts",
                                            "start_line": 182,
                                            "end_line": 184,
                                        }
                                    ),
                                },
                            }
                        ]
                    ),
                )
            key, ref = "normalization", reference(self.selected, "web/src/normalize.ts", 182, 184)
        elif "web/README.md" in paths:
            key, ref = "encryption", reference(self.selected, "web/README.md", 2, 3)
        hints = (
            [
                {
                    "behavior_key": key,
                    "name": key,
                    "summary": key + " behavior",
                    "category": "tools",
                    "discovery_refs": [ref],
                    "parent_key": None,
                }
            ]
            if key
            else []
        )
        return httpx.Response(200, json=completion({"candidates": hints}))


def discover(corpus, tmp_path, **options):
    projects, pin, _, _ = corpus
    selected = pin()
    catalogs = CatalogStore(projects.runs)
    view = catalogs.submit(
        OWNER,
        selected,
        CatalogCreate(source_project=selected.id, options=DiscoveryOptions(**options)),
        "text-corpus-catalog",
        settings().meta_model_id,
    )
    provider = TextProvider(selected)

    async def run():
        async with MetaClient(
            settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        ) as client:
            return await record_catalog(
                CatalogJob(catalog_id=view.id, revision=1),
                catalogs,
                projects,
                OWNER,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    report, path = asyncio.run(run())
    return selected, catalogs, catalogs.get(OWNER, view.id), provider, report, path


def test_parserless_discovery_trace_saved_browse_export_and_adaptation_fence(
    corpus, tmp_path, monkeypatch
):
    import app.project_adapters

    monkeypatch.setattr(
        app.project_adapters,
        "python_facts",
        lambda *_: pytest.fail("Text analysis invoked AST admission"),
    )
    selected, catalogs, view, provider, report, path = discover(corpus, tmp_path)
    assert view.counts.code_supported == 1, report
    assert view.counts.partial == 1 and view.analysis_profile == "repository-text-v1"
    features = catalogs.features(OWNER, view)
    supported = next(f for f in features if f.evidence_status == "supported")
    assert supported.card.execution_verification == "not_run"
    assert supported.card.implementation[0].start_line == 182
    assert all(e.ref.blob_sha256 and e.ref.excerpt_sha256 for e in supported.evidence)
    assert {b["pass"] for b in view.batches if b["stage"] == "discovery"} == {
        "documentation",
        "implementation",
    }
    assert view.coverage_v2.partially_read_files and view.continuation_available
    calls = len(provider.requests)
    catalogs.page(OWNER, view.id, 1)
    catalogs.feature(OWNER, view.id, 1, supported.feature_id)
    assert len(provider.requests) == calls
    artifacts = RunArtifacts(
        path.parent.parent,
        {
            "summary": summary_from_report(
                view.run_id, "catalog_discovery", report
            ).model_dump_json(),
            "report": path.parent.name + "/report.json",
            "digest": digest(path.read_bytes()),
        },
    )
    assert artifacts.references() and all(
        r.excerpt_available for r in artifacts.references().values()
    )
    assert (
        json.loads(artifacts.published("catalog-features.json"))[0]["schema_version"]
        == "catalog-feature-v2"
    )
    from unittest.mock import patch

    with patch("app.adaptation_inputs.RelevanceStore") as assessments:
        assessments.return_value.get.return_value = SimpleNamespace(
            state="complete", target_project="target", catalog_id=view.id, catalog_revision=1
        )
        with pytest.raises(HTTPException, match="Generic text"):
            adaptation_inputs(
                SimpleNamespace(assessment_id="a", feature_id=supported.feature_id),
                corpus[0],
                OWNER,
                settings().meta_model_id,
            )


@pytest.mark.parametrize(
    "target_status,rating",
    [
        ("potential_gap", 4),
        ("already_present", 4),
        ("unknown", 4),
        ("unknown", 0),
        ("no_target", 4),
    ],
)
def test_cross_language_semantic_comparison_keeps_fit_unassessed(
    corpus, tmp_path, target_status, rating
):
    selected, catalogs, catalog, _, _, _ = discover(corpus, tmp_path)
    projects, pin, _, _ = corpus
    target = pin("target", "target") if target_status != "no_target" else None
    feature = next(f for f in catalogs.features(OWNER, catalog) if f.evidence_status == "supported")
    assessments = RelevanceStore(projects.runs)
    view = assessments.submit(
        OWNER,
        catalog,
        target,
        AssessmentCreate(
            catalog_id=catalog.id,
            catalog_revision=1,
            target_project=target.id if target else None,
            goal="Normalize rendering input",
            feature_ids=[feature.feature_id],
        ),
        "text-corpus-comparison",
        settings().meta_model_id,
    )
    source_ref = feature.card.implementation[0].model_dump(mode="json")
    target_ref = reference(target, "target/main.go", 1, 2) if target else None
    judgment = {
        "schema_version": "semantic-judgment-v2",
        "feature_id": feature.feature_id,
        "relevance": rating,
        "reason": "Normalization could improve the existing renderer.",
        "source_refs": [source_ref],
        "target_refs": [target_ref]
        if target_status in {"potential_gap", "already_present"}
        else [],
        "target_status": target_status,
        "expansion": {
            "source_capability": "Normalize text",
            "existing_target_behavior": "Returns raw input",
            "proposed_extension": "Trim and lowercase inputs",
            "benefit": "Consistent output",
            "integration_points": [
                {"target_ref": target_ref, "change": "Normalize before returning"}
            ],
            "constraints": ["Review Go Unicode semantics"],
            "uncertainties": ["Other callers were not examined"],
            "source_refs": [source_ref],
            "target_refs": [target_ref],
            "transfer_mode": "conceptual_cross_language",
        },
    }
    if target_status != "potential_gap":
        judgment["expansion"] = None

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(judgment))

    async def run():
        async with MetaClient(
            settings(),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            return await record_relevance(
                RelevanceJob(assessment_id=view.id),
                assessments,
                projects,
                OWNER,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    report, _ = asyncio.run(run())
    result = assessments.feature(OWNER, view.id, catalog.id, 1, feature.feature_id)
    assert result.relevance_status == ("relevant" if rating >= 3 else "less_relevant"), report
    assert result.target_status == target_status
    if target_status == "potential_gap":
        assert result.expansion.transfer_mode == "conceptual_cross_language"
        assert assessments.get(OWNER, view.id).expansion_top3
    else:
        assert result.expansion is None
    assert result.fit is None and result.fit_status == "unassessed"
    assert result.evidence[0].ref.excerpt_sha256
    context = json.loads(requests[0]["messages"][1]["content"])
    assert context["candidate"]["behavior"] == feature.card.behavior
    assert "card" not in context["candidate"]
    ranges = [
        (e["ref"]["path"], e["ref"]["start_line"], e["ref"]["end_line"])
        for e in context["source_excerpts"]
    ]
    assert len(ranges) == len(set(ranges))


@pytest.mark.parametrize(
    "schema",
    [CandidateBatchV2, FeatureCardV2, SourceTraceV2, TextSearch, ListPaths, SemanticJudgmentV2],
)
def test_provider_schemas_pass_strict_preflight(schema):
    assert strict_schema(schema)["additionalProperties"] is False


def test_declarative_source_and_same_name_in_unrelated_modules(corpus, tmp_path):
    selected, catalogs, view, _, _, _ = discover(corpus, tmp_path)
    _, repo = corpus[0].open(OWNER, selected.id)
    session = TextEvidenceSession(repo, selected.id)
    session.read_range("web/src/route.yaml", 1, 3)
    declaration = session.ref("web/src/route.yaml", 1, 3)
    card = FeatureCardV2.model_validate(card_data("frouting", declaration.model_dump(mode="json")))
    assert session.validate_card(card).evidence_status == "supported"
    feature = next(f for f in catalogs.features(OWNER, view) if f.evidence_status == "supported")
    other = feature.model_copy(
        update={
            "feature_id": "0" * 32,
            "card": card,
            "discovery_refs": [session.ref("web/core/normalize.rs", 1, 1)],
        }
    )
    assert not matching_candidate(feature, other)


def test_continuation_advances_windows_preserves_ids_coverage_and_usage(corpus, tmp_path):
    selected, catalogs, first, provider, _, _ = discover(corpus, tmp_path)
    ids = {f.feature_id for f in catalogs.features(OWNER, first)}
    second = catalogs.submit(
        OWNER,
        selected,
        CatalogContinue(base_revision=1, options=DiscoveryOptions(trace_limit=0)),
        "continue-text-corpus",
        settings().meta_model_id,
        catalog_id=first.id,
    )

    async def run():
        async with MetaClient(
            settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        ) as client:
            return await record_catalog(
                CatalogJob(catalog_id=second.id, revision=2),
                catalogs,
                corpus[0],
                OWNER,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    asyncio.run(run())
    saved = catalogs.get(OWNER, first.id, 2)
    assert ids <= {f.feature_id for f in catalogs.features(OWNER, saved)}
    assert saved.seed_offsets["web/src/normalize.ts"] > first.seed_offsets["web/src/normalize.ts"]
    assert saved.cumulative_usage["requests"] == first.usage["requests"] + saved.usage["requests"]
    assert len(saved.coverage_v2.read_ranges) > len(first.coverage_v2.read_ranges)
    assert catalogs.get(OWNER, first.id, 1).seed_offsets == first.seed_offsets


def test_changed_versions_stop_before_provider_and_require_new_catalog(corpus, tmp_path):
    projects, pin, _, _ = corpus
    selected = pin()
    catalogs = CatalogStore(projects.runs)
    view = catalogs.submit(
        OWNER,
        selected,
        CatalogCreate(source_project=selected.id),
        "incompatible-text-job",
        settings().meta_model_id,
    )
    view.identity["retrieval"] = "retired-policy"
    catalogs.save(OWNER, view)
    provider = TextProvider(selected)

    async def run():
        async with MetaClient(
            settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        ) as client:
            return await record_catalog(
                CatalogJob(catalog_id=view.id, revision=1),
                catalogs,
                projects,
                OWNER,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    report, _ = asyncio.run(run())
    assert report["status"] == "failed" and not provider.requests
    with pytest.raises(HTTPException, match="Start a new catalog"):
        catalogs.submit(
            OWNER,
            selected,
            CatalogContinue(base_revision=1),
            "changed-continuation",
            settings().meta_model_id,
            catalog_id=view.id,
        )


def test_small_budget_retains_unverified_hints_and_actual_usage(corpus, tmp_path):
    projects, pin, _, _ = corpus
    selected = pin()
    catalogs = CatalogStore(projects.runs)
    view = catalogs.submit(
        OWNER,
        selected,
        CatalogCreate(source_project=selected.id, options=DiscoveryOptions(max_requests=1)),
        "small-text-budget",
        settings().meta_model_id,
    )
    provider = TextProvider(selected)

    async def run():
        async with MetaClient(
            settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        ) as client:
            return await record_catalog(
                CatalogJob(catalog_id=view.id, revision=1),
                catalogs,
                projects,
                OWNER,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    asyncio.run(run())
    saved = catalogs.get(OWNER, view.id)
    assert saved.usage["requests"] == len(provider.requests) == 1
    assert saved.counts.unverified == 1 and not saved.counts.code_supported
    assert saved.continuation_available and saved.remaining_paths


@pytest.mark.skipif(
    not os.environ.get("FE_BROWSER_CONTEXT"), reason="Explicit saved-data browser seed"
)
def test_saved_browser_seed(corpus, tmp_path):
    _, catalogs, view, _, report, path = discover(corpus, tmp_path)
    catalogs.runs.transition(
        view.run_id,
        stage="complete",
        message="Controlled provider fixture",
        status="finished",
        outcome=view.state,
        publication=(
            path.relative_to(tmp_path / "artifacts").as_posix(),
            digest(path.read_bytes()),
        ),
    )
    feature = next(f for f in catalogs.features(OWNER, view) if f.evidence_status == "supported")
    context = {
        "data_root": str(tmp_path),
        "catalog_id": view.id,
        "revision": 1,
        "feature_id": feature.feature_id,
        "usage": report["usage"],
        "run_id": view.run_id,
    }
    Path(os.environ["FE_BROWSER_CONTEXT"]).write_text(json.dumps(context), encoding="utf-8")
