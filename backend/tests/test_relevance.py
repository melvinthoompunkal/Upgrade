"""F5: real pinned code, controlled semantic judgments and inference-free queries."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate
from test_catalogs import execute as discover
from test_catalogs import independent as independent
from test_catalogs import library as library
from test_catalogs import submit as submit_catalog
from test_meta_client import completion, settings
from test_projects import pin

from app.api.storage import RunStore
from app.errors import EvidenceError
from app.main import create_app
from app.models.meta_client import MetaClient
from app.models.schema import response_format
from app.relevance_contracts import (
    AssessmentCreate,
    AssessmentOptions,
    ExpansionJudgment,
    RelevanceJob,
    RelevanceJudgment,
)
from app.relevance_store import RelevanceStore
from app.relevance_workflow import record_relevance

OWNER = "local-workspace"


@pytest.fixture
def catalog_context(library, tmp_path):
    catalogs, queued = submit_catalog(library)
    view, _, _, _ = discover(library, catalogs, queued, tmp_path)
    catalogs.runs.transition(view.run_id, stage="complete", message="Done", status="finished")
    target = pin(library[0], library[1].snapshot.commit_sha, role="target")
    return catalogs, view, target


class SemanticProvider:
    def __init__(self, *, fail=None, relevance=None):
        self.calls = []
        self.fail, self.relevance = fail, relevance

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        data = json.loads(body["messages"][1]["content"])
        feature = data["candidate"]
        ref = dict(feature["discovery_refs"][0])
        key = feature["behavior_key"]
        rating = (
            self.relevance
            if self.relevance is not None
            else {"chunking": 5, "uniqueness": 3}.get(key, 0)
        )
        target_refs, criteria = [], None
        status = "no_target"
        if data["target"]:
            target_refs = [
                {
                    **ref,
                    "repo_id": data["target"]["repo_id"],
                    "commit_sha": data["target"]["commit_sha"],
                    "path": "src/sequence.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "chunks",
                }
            ]
            status = "already_present" if key == "chunking" else "potential_gap"
            criteria = {
                name: {
                    "rating": 5,
                    "reason": "Pinned code supports this judgment.",
                    "evidence": target_refs,
                }
                for name in (
                    "gap_fit",
                    "isolation",
                    "dependency_cost",
                    "stack_compatibility",
                    "testability",
                )
            }
        if key == self.fail:
            ref["commit_sha"] = "0" * 40
        expansion = None
        if data["target"] and rating >= 3 and status == "potential_gap":
            expansion = {
                "source_capability": "Remove repeated sequence entries while preserving order.",
                "existing_target_behavior": "chunks returns the original sequence as one group.",
                "proposed_extension": "Offer optional ordered deduplication before chunking.",
                "benefit": "Avoid repeated work on duplicate input entries.",
                "integration_points": [
                    {"target_ref": target_refs[0], "change": "Normalize items before grouping."}
                ],
                "constraints": ["Keep existing chunking behavior by default."],
                "uncertainties": ["Only bounded target code was read; other helpers may exist."],
                "source_refs": [ref],
                "target_refs": target_refs,
            }
        return httpx.Response(
            200,
            json=completion(
                {
                    "feature_id": feature["feature_id"],
                    "relevance": rating,
                    "reason": "Useful for the declared goal."
                    if rating >= 3
                    else "Unrelated to the goal.",
                    "source_refs": [ref],
                    "target_refs": target_refs,
                    "target_status": status,
                    "criteria": criteria,
                    "expansion": expansion,
                }
            ),
        )


def submit(context, **changes):
    catalogs, catalog, target = context
    body = AssessmentCreate(
        **{
            "catalog_id": catalog.id,
            "catalog_revision": catalog.revision,
            "target_project": target.id,
            "goal": "Chunk sequences and remove duplicates",
            **changes,
        }
    )
    store = RelevanceStore(catalogs.runs)
    return store, store.submit(
        OWNER,
        catalog,
        target if body.target_project else None,
        body,
        "initial-assessment-key",
        settings().meta_model_id,
    )


def execute(library, store, view, tmp_path, provider=None):
    provider = provider or SemanticProvider()

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with MetaClient(settings(), http_client=transport) as client:
            return await record_relevance(
                RelevanceJob(assessment_id=view.id),
                store,
                library[0],
                OWNER,
                tmp_path / "artifacts",
                settings(),
                client=client,
            )

    report, path = asyncio.run(run())
    result = store.get(OWNER, view.id)
    store.runs.transition(view.run_id, stage="complete", message="Done", status="finished")
    return result, report, path, provider


def test_high_relevance_already_present_and_full_rejections(library, catalog_context, tmp_path):
    store, queued = submit(catalog_context)
    view, report, path, provider = execute(library, store, queued, tmp_path)
    assert view.counts.deeply_assessed == 3 and view.counts.relevant == 2
    assert view.counts.eligible == 0 and view.top3 == []
    assert view.counts.unassessed == view.counts.assessment_failed == 0
    catalogs, catalog, _ = catalog_context
    page = catalogs.page(OWNER, catalog.id, 1, assessment_id=view.id, mode="relevant")
    assert len(page.items) == page.matching_filter == 2
    chunks = page.items[0]
    fit = store.feature(OWNER, view.id, catalog.id, 1, chunks.feature_id)
    assert fit.target_status == "already_present" and fit.fit.total == 100
    assert not fit.fit.eligible and len(fit.gates) == 10
    assert any(g.name == "target_gap" and g.status == "fail" for g in fit.gates)
    assert any("frozen" in r.lower() for r in fit.rejections)
    assert report["eligible_feature_count"] == 0 and path.is_file()
    assert "MIT License" in (path.parent / "notices/source-notices.json").read_text()
    assert "MIT License" in (path.parent / "notices/target-notices.json").read_text()
    assert view.usage["requests"] == len(provider.calls) == 3
    assert response_format(RelevanceJudgment)["type"] == "json_schema"
    assert response_format(ExpansionJudgment)["type"] == "json_schema"
    assert fit.expansion is None  # Existing behavior is not offered as a new capability.
    idea_id = view.expansion_top3[0].feature_id
    idea = store.feature(OWNER, view.id, catalog.id, 1, idea_id)
    assert view.expansion_top3[0].name == catalogs.feature(OWNER, catalog.id, 1, idea_id).name
    assert idea.target_status == "potential_gap" and idea.expansion
    assert idea.expansion.integration_points[0].target_ref.repo_id == "target"
    assert idea.fit_status == "ineligible"  # Useful ideas require no native test baseline.
    assert json.loads(path.read_text())["relevance"]["expansion_top3"][0]["feature_id"] == idea_id
    assert json.loads((path.parent / f"assessments/{idea_id}.json").read_text())["expansion"]


def test_shortlist_unassessed_and_explicit_more_are_immutable(library, catalog_context, tmp_path):
    store, queued = submit(catalog_context, options=AssessmentOptions(feature_limit=1))
    first, _, _, _ = execute(library, store, queued, tmp_path)
    assert first.counts.deeply_assessed == 1 and first.counts.unassessed == 2
    catalogs, catalog, target = catalog_context
    before = catalogs.page(OWNER, catalog.id, 1, assessment_id=first.id).model_dump_json()
    more = AssessmentCreate(
        catalog_id=catalog.id,
        catalog_revision=1,
        target_project=target.id,
        goal=first.goal,
        base_assessment=first.id,
    )
    next_view = store.submit(
        OWNER, catalog, target, more, "more-assessment-key", settings().meta_model_id
    )
    second, _, _, provider = execute(library, store, next_view, tmp_path)
    assert len(provider.calls) == 2 and second.counts.deeply_assessed == 3
    assert second.cumulative_usage["requests"] == first.usage["requests"] + second.usage["requests"]
    assert catalogs.page(OWNER, catalog.id, 1, assessment_id=first.id).model_dump_json() == before
    with pytest.raises(EvidenceError, match="finished"):
        store.save(OWNER, first)
    with pytest.raises(EvidenceError, match="Automatic replay"):
        execute(library, store, first, tmp_path)


def test_failed_citation_does_not_make_unexplored_features_irrelevant(
    library, catalog_context, tmp_path
):
    store, view = submit(catalog_context)
    view, _, _, provider = execute(
        library, store, view, tmp_path, SemanticProvider(fail="uniqueness")
    )
    assert view.state == "partial" and view.counts.assessment_failed == 1
    assert view.counts.deeply_assessed == 2 and view.counts.relevant == 1
    page = catalog_context[0].page(
        OWNER, view.catalog_id, 1, assessment_id=view.id, relevance_status="assessment_failed"
    )
    assert page.matching_filter == 1 and page.items[0].assessment.relevance_score is None
    assert len(provider.calls) == 3


@pytest.mark.parametrize("tamper", ["commit", "repository", "unread", "missing", "overlap"])
def test_expansion_cannot_claim_unread_or_foreign_integration_code(
    library, catalog_context, tmp_path, tamper
):
    semantic = SemanticProvider()

    def provider(request):
        response = semantic(request).json()
        output = json.loads(response["choices"][0]["message"]["content"])
        idea = output["expansion"]
        if idea:
            point = idea["integration_points"][0]["target_ref"]
            if tamper == "commit":
                point["commit_sha"] = "0" * 40
            elif tamper == "repository":
                idea["source_refs"] = idea["target_refs"]
            elif tamper == "unread":
                point.update(start_line=800, end_line=802, symbol=None)
            elif tamper == "missing":
                output["expansion"] = None
            else:
                output["target_status"] = "already_present"
        return httpx.Response(200, json=completion(output))

    store, queued = submit(catalog_context)
    view, _, _, _ = execute(library, store, queued, tmp_path, provider)
    assert view.state == "partial" and view.counts.assessment_failed == 1
    assert view.expansion_top3 == []
    with store.runs.connect() as db:
        failure = db.execute(
            "SELECT detail FROM relevance_features WHERE assessment_id=? "
            "AND relevance_status='assessment_failed'",
            (view.id,),
        ).fetchone()[0]
    assert json.loads(failure)["failure_code"] == "invalid_evidence"


def test_saved_v1_assessments_remain_readable_without_recommendations(
    library, catalog_context, tmp_path
):
    import hashlib

    store, queued = submit(catalog_context)
    view, _, _, _ = execute(library, store, queued, tmp_path)
    idea_id = view.expansion_top3[0].feature_id
    with store.runs.connect() as db:
        saved = json.loads(
            db.execute(
                "SELECT detail FROM relevance_features WHERE assessment_id=? AND feature_id=?",
                (view.id, idea_id),
            ).fetchone()[0]
        )
        saved.pop("expansion")
        detail = json.dumps(saved)
        db.execute(
            "UPDATE relevance_features SET detail=?,sha256=? "
            "WHERE assessment_id=? AND feature_id=?",
            (detail, hashlib.sha256(detail.encode()).hexdigest(), view.id, idea_id),
        )
        metadata = view.model_dump(mode="json")
        metadata.pop("expansion_top3")
        metadata["identity"].update(policy="relevance-v1", prompt="relevance-v1")
        db.execute(
            "UPDATE relevance_assessments SET metadata=? WHERE id=?",
            (json.dumps(metadata), view.id),
        )
    assert store.get(OWNER, view.id).expansion_top3 == []
    assert store.feature(OWNER, view.id, view.catalog_id, 1, idea_id).expansion is None


def test_budget_stops_and_retains_actual_usage(library, catalog_context, tmp_path):
    store, view = submit(catalog_context, options=AssessmentOptions(max_requests=1))
    view, report, _, provider = execute(library, store, view, tmp_path)
    assert len(provider.calls) == view.usage["requests"] == report["usage"]["requests"] == 1
    assert view.counts.deeply_assessed == 1 and view.counts.unassessed == 2
    assert view.state == "partial" and "model_budget" in view.limit_reasons


def test_goal_only_unrelated_and_missing_context(library, catalog_context, tmp_path):
    store, view = submit(catalog_context, target_project=None, goal="Unrelated goal")
    view, _, _, _ = execute(library, store, view, tmp_path, SemanticProvider(relevance=0))
    assert view.target_sha is None and view.counts.relevant == view.counts.eligible == 0
    assert view.expansion_top3 == []
    catalogs, catalog, _ = catalog_context
    page = catalogs.page(OWNER, catalog.id, 1, mode="relevant", assessment_id=view.id)
    assert not page.items and page.matching_filter == 0
    with pytest.raises(HTTPException) as missing:
        catalogs.page(OWNER, catalog.id, 1, mode="relevant")
    assert missing.value.status_code == 409
    with pytest.raises(ValueError):
        AssessmentCreate(catalog_id=catalog.id, catalog_revision=1, goal="  ")


def test_queries_ties_context_bound_cursors_and_no_inference(library, catalog_context, tmp_path):
    store, view = submit(catalog_context)
    view, _, _, provider = execute(library, store, view, tmp_path)
    catalogs, catalog, _ = catalog_context
    before = len(provider.calls)
    for sort in ("category", "name", "relevance"):
        seen, cursor = [], None
        while True:
            page = catalogs.page(
                OWNER, catalog.id, 1, assessment_id=view.id, sort=sort, limit=1, cursor=cursor
            )
            seen.extend(item.feature_id for item in page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 3
    first = catalogs.page(OWNER, catalog.id, 1, assessment_id=view.id, limit=1)
    for changes in (
        {"mode": "relevant"},
        {"q": "other"},
        {"sort": "name"},
        {"fit_status": "eligible"},
        {"assessment_id": None},
    ):
        with pytest.raises(HTTPException) as invalid:
            catalogs.page(
                OWNER,
                catalog.id,
                1,
                **{
                    "assessment_id": view.id,
                    "limit": 1,
                    "cursor": first.next_cursor,
                    **changes,
                },
            )
        assert invalid.value.status_code == 422
    assert (
        catalogs.page(
            OWNER,
            catalog.id,
            1,
            q="duplicates",
            category="sequences",
            status="supported",
            assessment_id=view.id,
        ).matching_filter
        == 1
    )
    assert (
        catalogs.page(
            OWNER, catalog.id, 1, fit_status="eligible", assessment_id=view.id
        ).matching_filter
        == 0
    )
    assert len(provider.calls) == before


def test_context_changes_and_atomic_idempotency(library, catalog_context, tmp_path):
    store, first = submit(catalog_context)
    view, _, _, _ = execute(library, store, first, tmp_path)
    _, catalog, target = catalog_context
    body = AssessmentCreate(
        catalog_id=catalog.id,
        catalog_revision=1,
        target_project=target.id,
        goal=view.goal,
        base_assessment=view.id,
        feature_ids=[view.shortlist[0]],
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: store.submit(
                    OWNER, catalog, target, body, "concurrent-assessment", settings().meta_model_id
                ),
                range(2),
            )
        )
    assert results[0].id == results[1].id
    with pytest.raises(HTTPException) as conflict:
        store.submit(
            OWNER,
            catalog,
            target,
            body.model_copy(update={"goal": "Changed"}),
            "concurrent-assessment",
            settings().meta_model_id,
        )
    assert conflict.value.status_code == 409
    for changed_body, model in (
        (body.model_copy(update={"goal": body.goal + " "}), settings().meta_model_id),
        (body, "different-model"),
    ):
        with pytest.raises(HTTPException) as stale:
            store.submit(OWNER, catalog, target, changed_body, "changed-context-key", model)
        assert stale.value.status_code == 409


def test_api_admission_and_foreign_nested_contexts(library, catalog_context, tmp_path):
    catalogs, catalog, target = catalog_context
    with TestClient(create_app(settings(), data_root=tmp_path, start_worker=False)) as client:
        client.app.state.worker.available = True
        headers = {"Origin": "http://127.0.0.1:5173", "Idempotency-Key": "api-relevance-operation"}
        body = {
            "catalog_id": catalog.id,
            "catalog_revision": 1,
            "target_project": target.id,
            "goal": "Chunk sequences",
        }
        response = client.post("/api/relevance-assessments", json=body, headers=headers)
        assert response.status_code == 202, response.text
        assert response.headers["Location"].endswith(response.json()["id"])
        view = RelevanceStore(catalogs.runs).get(OWNER, response.json()["id"])
        view, _, _, _ = execute(library, RelevanceStore(catalogs.runs), view, tmp_path)
        assert (
            client.get(
                f"/api/catalogs/{catalog.id}/features",
                params={
                    "revision": 1,
                    "mode": "relevant",
                    "assessment_id": view.id,
                },
            ).json()["matching_filter"]
            == 2
        )
        feature_id = view.shortlist[0]
        assert (
            client.get(f"/api/relevance-assessments/{view.id}/features/{feature_id}").status_code
            == 200
        )
        assert (
            client.get(f"/api/relevance-assessments/{view.id}/features/{'0' * 32}").status_code
            == 404
        )
        assert (
            client.post(
                "/api/runs",
                headers=headers,
                json={
                    "kind": "relevance_assessment",
                    "assessment_id": view.id,
                },
            ).status_code
            == 422
        )
        for params in ({"sort": "invented"}, {"mode": "invented"}):
            assert (
                client.get(
                    f"/api/catalogs/{catalog.id}/features", params={"revision": 1, **params}
                ).status_code
                == 422
            )
        client.app.state.worker.available = False


def test_second_account_cannot_read_assessment_or_join(
    library, catalog_context, authenticated_app, tmp_path
):
    store, view = submit(catalog_context)
    view, _, _, _ = execute(library, store, view, tmp_path)
    client, _ = authenticated_app
    # Fixtures share the same database; these saved records belong to local-workspace.
    session, _ = login(client)
    for path in (
        f"/api/relevance-assessments/{view.id}",
        f"/api/relevance-assessments/{view.id}/features/{view.shortlist[0]}",
        f"/api/catalogs/{view.catalog_id}/features?revision=1&assessment_id={view.id}",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
    assert (
        mutate(
            client,
            "/api/relevance-assessments",
            session,
            json={
                "catalog_id": view.catalog_id,
                "catalog_revision": 1,
                "goal": "Foreign attempt",
            },
        ).status_code
        == 404
    )


def test_interruption_integrity_and_v6_migration(library, catalog_context, tmp_path):
    store, view = submit(catalog_context)
    with store.runs.connect() as db:
        db.execute("PRAGMA user_version=6")
    restored = RelevanceStore(RunStore(store.runs.path))
    assert restored.get(OWNER, view.id) == view
    store.runs.cancel_queued(OWNER)
    settled = restored.get(OWNER, view.id)
    assert settled.state == "canceled"
    with pytest.raises(EvidenceError, match="Automatic replay"):
        execute(library, restored, settled, tmp_path)


def test_worker_dispatch_publication_and_run_projection(
    library, catalog_context, tmp_path, monkeypatch
):
    from app import relevance_workflow
    from app.api.artifacts import RunArtifacts
    from app.api.worker import LocalWorker

    store, view = submit(catalog_context)
    provider = SemanticProvider()

    def client_factory(config, *, budget):
        return MetaClient(
            config,
            budget=budget,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
        )

    monkeypatch.setattr(relevance_workflow, "MetaClient", client_factory)
    worker = LocalWorker(store.runs, tmp_path / "artifacts", settings())
    worker.process(store.runs.claim())
    row = store.runs.row(view.run_id)
    assert json.loads(row["summary"])["outcome"] == "complete"
    detail = RunArtifacts(worker.root, row).detail()
    assert detail.catalog_id == view.catalog_id
    assert detail.relevance_assessment_id == view.id
    assert detail.analyzed_feature_count == 3 and detail.eligible_feature_count == 0
    assert detail.artifacts and not detail.error
    assert detail.references and all(ref.excerpt_available for ref in detail.references)


def test_optional_readlog_failure_preserves_required_receipt(
    library, catalog_context, tmp_path, monkeypatch
):
    from app.selection_artifacts import ArtifactStore

    publish = ArtifactStore.json

    def deny_readlog(self, name, data):
        if name == "source-evidence-reads.json":
            raise OSError("Controlled sidecar failure")
        return publish(self, name, data)

    monkeypatch.setattr(ArtifactStore, "json", deny_readlog)
    store, queued = submit(catalog_context)
    view, report, path, _ = execute(library, store, queued, tmp_path)
    saved = json.loads(path.read_text())
    assert saved["usage"] == view.usage and saved["calls"]
    assert saved["warnings"] and "source-evidence-reads.json" in saved["unavailable_artifacts"]
    assert report["analyzed_feature_count"] == 3


def test_checkpoint_fences_changed_github_access(library, catalog_context):
    from app.auth.access import RepositoryAccessDenied

    store, view = submit(catalog_context)
    with store.runs.connect() as db:
        db.execute("INSERT INTO users VALUES ('fixture-owner',918,'pin-owner',NULL,1,1)")
        db.execute(
            "UPDATE relevance_assessments SET owner_id='fixture-owner' WHERE id=?", (view.id,)
        )
    with pytest.raises(RepositoryAccessDenied):
        store.save("fixture-owner", view, access_epoch=0)
    assert store.get("fixture-owner", view.id).state == "queued"
