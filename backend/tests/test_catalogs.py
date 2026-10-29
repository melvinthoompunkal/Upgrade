"""F4: real pinned Git data, controlled model protocol, persistence and immutable queries."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate
from test_meta_client import completion, settings
from test_projects import independent as independent
from test_projects import pin

from app import catalog_store
from app.api.storage import RunStore
from app.catalog_contracts import CatalogContinue, CatalogCreate, CatalogJob, DiscoveryOptions
from app.catalog_discovery import CatalogAnalyzer
from app.catalog_store import CatalogStore
from app.catalog_workflow import record_catalog
from app.errors import EvidenceError
from app.main import create_app
from app.models.meta_client import MetaClient
from app.project_contracts import ProjectAnalysisRequest
from app.projects import ProjectStore

OWNER = "local-workspace"


@pytest.fixture
def library(independent):
    projects, root, git, _, _ = independent
    (root / "library/src/sequence.py").write_text(
        "def chunks(items):\n    return [items]\n\n"
        "def unique(items):\n    return list(dict.fromkeys(items))\n",
        encoding="utf-8",
    )
    (root / "library/README.md").write_text(
        "# Sequence tools\n## Encryption\nEncrypts payloads.\n",
        encoding="utf-8",
    )
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "independent capabilities", cwd=root)
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    selected = pin(projects, sha)
    return projects, selected, root, git


class Provider:
    """Deliberately small fixture answers; production still owns all evidence validation."""

    def __init__(self, selected, *, bad_feature=None, fail_batch=None, duplicate=True):
        self.selected = selected
        self.bad_feature, self.fail_batch, self.duplicate = bad_feature, fail_batch, duplicate
        self.requests = []
        self.discovery_calls = 0

    def ref(self, path, start, end, symbol=None):
        return {
            "repo_id": self.selected.snapshot.repo_id,
            "commit_sha": self.selected.snapshot.commit_sha,
            "path": path,
            "start_line": start,
            "end_line": end,
            "symbol": symbol,
        }

    def hint(self, key, name, ref, parent=None):
        return {
            "behavior_key": key,
            "name": name,
            "summary": name + " behavior",
            "category": "sequences",
            "discovery_refs": [ref],
            "parent_key": parent,
        }

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        inputs = json.loads(body["messages"][1]["content"])
        if "packet" in inputs:
            self.discovery_calls += 1
            if self.discovery_calls == self.fail_batch:
                return httpx.Response(503)
            paths = {item["path"] for item in inputs["packet"]}
            hints = []
            if "src/sequence.py" in paths:
                hints = [
                    self.hint(
                        "chunking", "Chunk sequences", self.ref("src/sequence.py", 1, 2, "chunks")
                    ),
                    self.hint(
                        "uniqueness",
                        "Remove duplicates",
                        self.ref("src/sequence.py", 4, 5, "unique"),
                    ),
                ]
                if self.duplicate:
                    hints.append(
                        self.hint(
                            "chunking",
                            "Sequence chunks",
                            self.ref("src/sequence.py", 1, 1, "chunks"),
                        )
                    )
            if "README.md" in paths:
                hints.append(
                    self.hint("encryption", "Encrypt payloads", self.ref("README.md", 2, 3))
                )
            return httpx.Response(200, json=completion({"candidates": hints}))
        candidate = inputs["candidate"]
        key = candidate["behavior_key"]
        reference = candidate["discovery_refs"][0]
        if body["messages"][-1]["role"] != "tool":
            reads = [
                (reference["path"], reference["start_line"], reference["end_line"]),
                ("LICENSE", 1, 2),
            ]
            if key == "chunking":
                reads.append(("tests/test_sequence.py", 1, 3))
            calls = [
                {
                    "id": f"read-{i}",
                    "type": "function",
                    "function": {
                        "name": "read_range",
                        "arguments": json.dumps(
                            {
                                "path": path,
                                "start_line": start,
                                "end_line": end,
                            }
                        ),
                    },
                }
                for i, (path, start, end) in enumerate(reads)
            ]
            return httpx.Response(200, json=completion(calls=calls))
        card = {
            "feature_id": candidate["feature_id"],
            "name": candidate["name"],
            "behavior": candidate["summary"],
            "entry_points": [reference] if key != "encryption" else [],
            "implementation": [reference] if key != "encryption" else [],
            "dependencies": [],
            "source_tests": [self.ref("tests/test_sequence.py", 2, 3, "test_chunks")]
            if key == "chunking"
            else [],
            "acceptance_behaviors": [candidate["summary"]],
            "transferable_slice": "Behavior analysis only; adaptation is unapproved.",
            "excluded_behaviors": ["Encryption implementation is absent."]
            if key == "encryption"
            else [],
            "license_spdx": "MIT",
            "license_refs": [self.ref("LICENSE", 1, 2)],
            "evidence_status": "partial" if key == "encryption" else "supported",
        }
        if key == self.bad_feature:
            card["implementation"] = [dict(reference, commit_sha="0" * 40)]
        return httpx.Response(
            200,
            json=completion(
                {
                    "source_sha": self.selected.snapshot.commit_sha,
                    "features": [card],
                    "coverage": {
                        "examined_paths": [],
                        "excluded_paths": [],
                        "unexamined_areas": [],
                        "limits_reached": [],
                    },
                }
            ),
        )


def submit(library, **options):
    projects, selected, _, _ = library
    catalogs = CatalogStore(projects.runs)
    view = catalogs.submit(
        OWNER,
        selected,
        CatalogCreate(source_project=selected.id, options=DiscoveryOptions(**options)),
        "initial-catalog-operation",
        settings().meta_model_id,
    )
    return catalogs, view


def execute(library, catalogs, view, tmp_path, provider=None, *, owner=OWNER):
    projects, selected, _, _ = library
    provider = provider or Provider(selected)

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with MetaClient(settings(), http_client=transport) as client:
            return await record_catalog(
                CatalogJob(catalog_id=view.id, revision=view.revision),
                catalogs,
                projects,
                owner,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    report, path = asyncio.run(run())
    return catalogs.get(owner, view.id, view.revision), report, path, provider


def test_multiple_independent_supported_features_docs_claim_and_missing_tests(library, tmp_path):
    catalogs, queued = submit(library)
    view, report, path, provider = execute(library, catalogs, queued, tmp_path)
    assert (
        view.counts.discovered == 3
    )  # Duplicate symbol/behavior is an alias, not another feature.
    assert view.counts.code_supported == 2
    assert view.counts.partial == 1 and view.counts.deeply_traced == 3
    features = catalogs.features(OWNER, view)
    chunks = next(f for f in features if f.behavior_key == "chunking")
    assert chunks.aliases == ["Sequence chunks"]
    assert chunks.card.source_tests and "not executed" in chunks.qualification
    hidden = next(f for f in features if f.behavior_key == "uniqueness")
    assert hidden.evidence_status == "supported" and hidden.missing_evidence
    assert "source tests" in hidden.missing_evidence[0]
    assert all(item.sha256 for item in hidden.evidence)
    claim = next(f for f in features if f.behavior_key == "encryption")
    assert claim.evidence_status == "partial" and not claim.card.implementation
    assert report["analyzed_feature_count"] == 3
    assert "eligible_feature_count" not in report
    assert "MIT License" in (path.parent / "notices/source-notices.json").read_text()
    before = len(provider.requests)
    reopened = CatalogStore(RunStore(library[0].runs.path))
    assert reopened.page(OWNER, view.id, 1).counts == view.counts
    assert reopened.feature(OWNER, view.id, 1, chunks.feature_id) == chunks
    assert len(provider.requests) == before
    assert path.is_file()


def test_failed_feature_preserves_other_validated_cards(library, tmp_path):
    catalogs, view = submit(library)
    view, _, _, _ = execute(
        library,
        catalogs,
        view,
        tmp_path,
        Provider(library[1], bad_feature="uniqueness"),
    )
    assert view.state == "partial" and "failed_feature_trace" in view.limit_reasons
    assert view.counts.code_supported == 1 and view.counts.unverified == 1
    failed = next(f for f in catalogs.features(OWNER, view) if f.behavior_key == "uniqueness")
    assert failed.trace_state == "failed" and failed.failure_code == "invalid_evidence"
    assert failed.card is None


def test_explicit_continuation_freezes_prior_revision_and_budget(library, tmp_path):
    catalogs, view = submit(library, max_files=1, trace_limit=0)
    first, _, _, _ = execute(library, catalogs, view, tmp_path)
    assert first.state == "partial" and first.remaining_paths and first.continuation_available
    before = catalogs.page(OWNER, first.id, 1).model_dump_json()
    with pytest.raises(EvidenceError, match="frozen"):
        catalogs.save(OWNER, first)
    next_view = catalogs.submit(
        OWNER,
        library[1],
        CatalogContinue(base_revision=1),
        "continue-operation-key",
        settings().meta_model_id,
        catalog_id=first.id,
    )
    second, _, _, _ = execute(library, catalogs, next_view, tmp_path)
    assert second.revision == 2 and second.counts.code_supported == 2
    assert second.usage["requests"] > 0
    assert second.cumulative_usage["requests"] == first.usage["requests"] + second.usage["requests"]
    assert catalogs.page(OWNER, first.id, 1).model_dump_json() == before
    with pytest.raises(HTTPException) as stale:
        catalogs.submit(
            OWNER,
            library[1],
            CatalogContinue(base_revision=1),
            "stale-base-key",
            settings().meta_model_id,
            catalog_id=first.id,
        )
    assert stale.value.status_code == 409


def test_cursor_bound_to_owner_revision_filters_with_no_duplicates(library, tmp_path):
    catalogs, view = submit(library)
    view, _, _, provider = execute(library, catalogs, view, tmp_path)
    first = catalogs.page(OWNER, view.id, 1, limit=1)
    second = catalogs.page(OWNER, view.id, 1, limit=1, cursor=first.next_cursor)
    third = catalogs.page(OWNER, view.id, 1, limit=1, cursor=second.next_cursor)
    assert len({p.items[0].feature_id for p in (first, second, third)}) == 3
    assert third.next_cursor is None
    assert all(p.matching_filter == 3 for p in (first, second, third))
    assert len(catalogs.page(OWNER, view.id, 1, q="duplicates").items) == 1
    assert catalogs.page(OWNER, view.id, 1, status="supported").matching_filter == 2
    calls = len(provider.requests)
    for arguments in (
        {"q": "different", "cursor": first.next_cursor},
        {"cursor": first.next_cursor + "tamper"},
    ):
        with pytest.raises(HTTPException) as denied:
            catalogs.page(OWNER, view.id, 1, limit=1, **arguments)
        assert denied.value.status_code == 422
    for operation in (
        lambda: catalogs.get("foreign-owner", view.id),
        lambda: catalogs.page("foreign-owner", view.id, 1, cursor=first.next_cursor),
        lambda: catalogs.feature("foreign-owner", view.id, 1, first.items[0].feature_id),
    ):
        with pytest.raises(HTTPException) as denied:
            operation()
        assert denied.value.status_code == 404
    assert len(provider.requests) == calls


def test_cache_full_identity_and_atomic_idempotency(library, tmp_path):
    catalogs, view = submit(library)
    view, _, _, _ = execute(library, catalogs, view, tmp_path)
    body = CatalogCreate(source_project=library[1].id)
    cached = catalogs.submit(
        OWNER,
        library[1],
        body,
        "cache-operation-key",
        settings().meta_model_id,
        model_configured=False,
    )
    assert cached.id == view.id and cached.cache_hit
    assert (
        catalogs.submit(
            OWNER,
            library[1],
            body,
            "cache-operation-key",
            settings().meta_model_id,
            model_configured=False,
        )
        == cached
    )
    with pytest.raises(HTTPException) as conflict:
        catalogs.submit(
            OWNER,
            library[1],
            body.model_copy(update={"scope": "src"}),
            "cache-operation-key",
            settings().meta_model_id,
        )
    assert conflict.value.status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: catalogs.submit(
                    OWNER,
                    library[1],
                    body.model_copy(update={"use_cache": False}),
                    "concurrent-operation-key",
                    settings().meta_model_id,
                ),
                range(2),
            )
        )
    assert results[0].run_id == results[1].run_id
    changed = catalogs.submit(OWNER, library[1], body, "new-model-operation-key", "changed-model")
    assert changed.id != view.id and not changed.cache_hit


def test_record_cap_and_model_budget_are_partial_with_accountable_spend(library, tmp_path):
    catalogs, view = submit(library, max_candidates=1, max_requests=1)
    view, report, _, provider = execute(library, catalogs, view, tmp_path)
    assert view.state == "partial" and view.counts.discovered == 1
    assert view.counts.code_supported == 0
    assert "job_candidate_limit" in view.limit_reasons
    assert view.usage["requests"] == len(provider.requests) == 1
    assert report["attempts"] and view.continuation_available


def test_failed_discovery_is_not_an_empty_success_and_can_continue(library, tmp_path):
    catalogs, view = submit(library)
    failed, report, _, _ = execute(
        library,
        catalogs,
        view,
        tmp_path,
        Provider(library[1], fail_batch=1),
    )
    assert failed.state == report["status"] == "failed"
    assert failed.failure_code and failed.counts.discovered == 0
    assert failed.remaining_paths and failed.continuation_available
    resumed = catalogs.submit(
        OWNER,
        library[1],
        CatalogContinue(base_revision=1),
        "failed-batch-retry-key",
        settings().meta_model_id,
        catalog_id=view.id,
    )
    recovered, _, _, _ = execute(library, catalogs, resumed, tmp_path)
    assert recovered.counts.code_supported == 2
    assert catalogs.get(OWNER, view.id, 1).state == "failed"


def test_detail_hash_and_snapshot_integrity_rejected(library, tmp_path):
    catalogs, view = submit(library)
    view, _, _, _ = execute(library, catalogs, view, tmp_path)
    feature = catalogs.features(OWNER, view)[0]
    with catalogs.runs.connect() as db:
        db.execute(
            "UPDATE catalog_features SET detail='{}' WHERE feature_id=?", (feature.feature_id,)
        )
    with pytest.raises(EvidenceError, match="integrity"):
        catalogs.feature(OWNER, view.id, 1, feature.feature_id)


def test_api_admission_saved_reads_and_no_bypass(library, tmp_path):
    projects, source, _, _ = library
    with TestClient(
        create_app(settings(), data_root=tmp_path / "api", start_worker=False)
    ) as client:
        client.app.state.worker.available = True
        with client.app.state.runs.connect() as db:
            db.execute(
                "INSERT INTO projects VALUES (?,?,?)", (source.id, OWNER, source.model_dump_json())
            )
        headers = {"Idempotency-Key": "api-catalog-operation"}
        response = client.post("/api/catalogs", json={"source_project": source.id}, headers=headers)
        assert response.status_code == 202, response.text
        data = response.json()
        assert client.get(response.headers["Location"]).json() == data
        assert (
            client.post("/api/catalogs", json={"source_project": source.id}, headers=headers).json()
            == data
        )
        assert client.get(f"/api/catalogs/{data['id']}/features?revision=1").status_code == 409
        assert (
            client.post(
                "/api/runs",
                json={"kind": "catalog_discovery", "catalog_id": data["id"], "revision": 1},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/catalogs",
                json={"source_project": source.id},
                headers={**headers, "Origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        client.app.state.worker.available = False


def test_read_window_and_symbol_checks_survive_catalog_generalization(library):
    projects, source, _, _ = library
    _, repo = projects.open(OWNER, source.id)
    from app.tools.evidence import EvidenceSession

    analyzer = CatalogAnalyzer(None, EvidenceSession(repo))
    provider = Provider(source)
    from app.catalog_contracts import CandidateHint

    hint = CandidateHint.model_validate(
        provider.hint(
            "chunks",
            "Chunks",
            provider.ref("src/sequence.py", 1, 2, "chunks"),
        )
    )
    with pytest.raises(EvidenceError, match="did not read"):
        analyzer.candidate(hint, {})
    analyzer.evidence.read_range("src/sequence.py", 1, 2)
    bad = hint.model_copy(
        update={"discovery_refs": (hint.discovery_refs[0].model_copy(update={"symbol": "unique"}),)}
    )
    with pytest.raises(EvidenceError, match="AST symbol"):
        analyzer.candidate(bad, {})


def test_on_demand_trace_is_a_new_revision_and_keeps_hint_identity(library, tmp_path):
    catalogs, view = submit(library, trace_limit=0)
    first, _, _, _ = execute(library, catalogs, view, tmp_path)
    hint = next(f for f in catalogs.features(OWNER, first) if f.behavior_key == "uniqueness")
    continuation = CatalogContinue(
        base_revision=1,
        options=DiscoveryOptions(max_files=0, trace_limit=1),
        trace_feature_ids=[hint.feature_id],
    )
    next_view = catalogs.submit(
        OWNER,
        library[1],
        continuation,
        "on-demand-trace-key",
        settings().meta_model_id,
        catalog_id=view.id,
    )
    second, _, _, provider = execute(library, catalogs, next_view, tmp_path)
    assert provider.discovery_calls == 0 and len(provider.requests) == 2
    assert catalogs.feature(OWNER, view.id, 1, hint.feature_id).evidence_status == "unverified"
    assert catalogs.feature(OWNER, view.id, 2, hint.feature_id).evidence_status == "supported"
    assert second.counts.code_supported == 1


def test_later_failed_batch_preserves_completed_batch_and_retries_only_remaining(library, tmp_path):
    projects, selected, root, git = library
    for number in range(8):
        (root / f"library/a-{number}.md").write_text("Additional module documentation.\n")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "additional docs", cwd=root)
    selected = pin(projects, git.run("rev-parse", "HEAD", cwd=root).decode().strip())
    library = projects, selected, root, git
    catalogs, view = submit(library)
    failed, _, _, _ = execute(library, catalogs, view, tmp_path, Provider(selected, fail_batch=2))
    assert failed.state == "partial" and failed.counts.discovered == 1
    assert failed.batches[0]["state"] == "completed"
    assert failed.batches[1]["state"] == "failed"
    processed = set(failed.processed_paths)
    second = catalogs.submit(
        OWNER,
        selected,
        CatalogContinue(base_revision=1),
        "later-batch-retry-key",
        settings().meta_model_id,
        catalog_id=view.id,
    )
    second, _, _, provider = execute(library, catalogs, second, tmp_path)
    assert second.counts.code_supported == 2
    packets = [json.loads(request["messages"][1]["content"]) for request in provider.requests]
    assert not processed & {
        item["path"] for packet in packets if "packet" in packet for item in packet["packet"]
    }


def test_worker_interruption_settles_catalog_without_replaying(library):
    catalogs, view = submit(library)
    catalogs.runs.transition(
        view.run_id,
        stage="complete",
        message="Worker interrupted.",
        status="finished",
        outcome="interrupted",
    )
    settled = catalogs.get(OWNER, view.id)
    assert settled.state == "failed" and settled.failure_code == "interrupted"
    assert settled.continuation_available and settled.usage == {}
    assert catalogs.get(OWNER, view.id) == settled
    with catalogs.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_catalog_and_queue_creation_roll_back_together(library, monkeypatch):
    projects, selected, _, _ = library
    catalogs = CatalogStore(projects.runs)

    def fail(*args):
        raise OSError("Controlled metadata failure.")

    monkeypatch.setattr(catalogs, "_operation", fail)
    with pytest.raises(OSError):
        catalogs.submit(
            OWNER,
            selected,
            CatalogCreate(source_project=selected.id),
            "atomic-operation-key",
            settings().meta_model_id,
        )
    with projects.runs.connect() as db:
        for table in ("catalogs", "catalog_revisions", "catalog_operations", "runs", "events"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_feature_hierarchy_is_explicit_and_cycles_are_rejected(library, tmp_path):
    class HierarchyProvider(Provider):
        def hint(self, key, name, ref, parent=None):
            return super().hint(
                key,
                name,
                ref,
                {
                    "chunking": "uniqueness",
                    "uniqueness": "chunking",
                }.get(key),
            )

    catalogs, view = submit(library, trace_limit=0)
    view, _, _, _ = execute(library, catalogs, view, tmp_path, HierarchyProvider(library[1]))
    features = catalogs.features(OWNER, view)
    by_id = {f.feature_id: f for f in features}
    children = [f for f in features if f.parent_feature_id]
    assert len(children) == 1
    assert by_id[children[0].parent_feature_id].parent_feature_id is None
    assert all(f.group for f in features)


def test_source_sha_and_analyzer_versions_invalidate_cache(library, tmp_path, monkeypatch):
    catalogs, view = submit(library)
    first, _, _, _ = execute(library, catalogs, view, tmp_path)
    projects, source, root, git = library
    (root / "library/src/sequence.py").write_text("def changed():\n    return 1\n")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "new source SHA", cwd=root)
    changed = pin(projects, git.run("rev-parse", "HEAD", cwd=root).decode().strip())
    fresh = catalogs.submit(
        OWNER,
        changed,
        CatalogCreate(source_project=changed.id),
        "changed-sha-operation",
        settings().meta_model_id,
    )
    assert fresh.id != first.id and fresh.identity["sha"] != first.identity["sha"]
    monkeypatch.setitem(
        catalog_store.VERSIONS,
        "analyzer",
        "changed-analyzer",
    )
    fresh = catalogs.submit(
        OWNER,
        source,
        CatalogCreate(source_project=source.id),
        "changed-version-key",
        settings().meta_model_id,
    )
    assert fresh.id != first.id


def test_populated_catalog_apis_reject_a_second_account(authenticated_app, library, tmp_path):
    client, state = authenticated_app
    session, _ = login(client)
    owner = session["user"]["id"]
    runs = client.app.state.runs
    projects = ProjectStore(runs, library[0].root)
    source = pin(projects, library[1].snapshot.commit_sha, owner=owner)
    catalogs = CatalogStore(runs)
    view = catalogs.submit(
        owner,
        source,
        CatalogCreate(source_project=source.id),
        "owned-catalog-operation",
        settings().meta_model_id,
    )
    view, _, _, _ = execute((projects, source, *library[2:]), catalogs, view, tmp_path, owner=owner)
    page_url = f"/api/catalogs/{view.id}/features?revision=1&limit=1"
    page = client.get(page_url)
    assert page.status_code == 200 and page.json()["next_cursor"]
    feature_id = page.json()["items"][0]["feature_id"]
    urls = [
        f"/api/catalogs/{view.id}?revision=1",
        page_url,
        f"/api/catalogs/{view.id}/features/{feature_id}?revision=1",
        page_url + "&cursor=" + page.json()["next_cursor"],
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    mutate(client, "/api/auth/logout", session)
    state.update(id=68, login="second-account")
    second, _ = login(client)
    for url in urls:
        response = client.get(url)
        assert response.status_code == 404
        assert "sequence" not in response.text and "discovered" not in response.text
    denied = mutate(
        client, f"/api/catalogs/{view.id}/continuations", second, json={"base_revision": 1}
    )
    assert denied.status_code == 404


def test_catalog_checkpoints_reject_access_epoch_change(authenticated_app, library):
    client, _ = authenticated_app
    session, _ = login(client)
    owner = session["user"]["id"]
    catalogs = CatalogStore(client.app.state.runs)
    view = catalogs.submit(
        owner,
        library[1],
        CatalogCreate(source_project=library[1].id),
        "epoch-catalog-operation",
        settings().meta_model_id,
        require_connected=True,
        access_epoch=0,
    )
    with catalogs.runs.connect() as db:
        db.execute("UPDATE users SET access_epoch=access_epoch+1 WHERE id=?", (owner,))
    from app.auth.access import RepositoryAccessDenied

    with pytest.raises(RepositoryAccessDenied):
        catalogs.save(owner, view.model_copy(update={"state": "running"}), access_epoch=0)
    assert catalogs.get(owner, view.id).state == "queued"


def test_v5_migration_preserves_projects_runs_events_and_credentials(library):
    projects, selected, _, _ = library
    run = projects.runs.enqueue(
        ProjectAnalysisRequest(kind="project_analysis", source_project=selected.id),
        "pre-catalog-project-run",
    )
    with projects.runs.connect() as db:
        for table in (
            "catalogs",
            "catalog_revisions",
            "catalog_features",
            "catalog_operations",
            "catalog_cursor_key",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=5")
        db.execute(
            "INSERT INTO credentials VALUES ('legacy-owner',?,1)", (b"opaque-encrypted-record",)
        )
    migrated = RunStore(projects.runs.path)
    assert migrated.row(run.id)["summary"] == run.model_dump_json()
    assert len(migrated.events(run.id, 0, owner_id=OWNER)) == 1
    assert ProjectStore(migrated, projects.root).get(OWNER, selected.id) == selected
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert (
            db.execute("SELECT ciphertext FROM credentials").fetchone()[0]
            == b"opaque-encrypted-record"
        )
        assert db.execute("SELECT length(key) FROM catalog_cursor_key").fetchone()[0] == 32


def test_disconnect_cancels_catalog_and_frozen_jobs_never_replay(library, tmp_path):
    catalogs, view = submit(library)
    catalogs.runs.cancel_queued(OWNER)
    canceled = catalogs.get(OWNER, view.id)
    assert canceled.state == "canceled" and canceled.failure_code == "canceled"
    provider = Provider(library[1])
    with pytest.raises(EvidenceError, match="Automatic replay"):
        execute(library, catalogs, view, tmp_path, provider)
    assert provider.requests == []


def test_optional_read_log_failure_still_publishes_usage_and_required_json(
    library, tmp_path, monkeypatch
):
    from app.selection_artifacts import ArtifactStore

    publish = ArtifactStore.json

    def deny_read_log(self, name, data):
        if name == "source-evidence-reads.json":
            raise OSError("Controlled sidecar failure.")
        return publish(self, name, data)

    monkeypatch.setattr(ArtifactStore, "json", deny_read_log)
    catalogs, view = submit(library)
    view, report, path, _ = execute(library, catalogs, view, tmp_path)
    stored = json.loads(path.read_text())
    assert stored["usage"] == view.usage and stored["calls"]
    assert stored["warnings"] and "source-evidence-reads.json" in stored["unavailable_artifacts"]
    assert stored["catalog"]["counts"] == view.counts.model_dump()
