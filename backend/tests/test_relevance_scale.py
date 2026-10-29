"""Saved-summary scale acceptance, with no Git/provider calls or full detail decoding."""

import hashlib
import json
from statistics import quantiles
from time import perf_counter
from uuid import uuid4

from app.api.contracts import CheckRequest
from app.api.storage import RunStore, now
from app.catalog_contracts import CatalogCounts, CatalogView, DiscoveryOptions, FeatureSummary
from app.catalog_store import CatalogStore
from app.relevance_contracts import AssessmentCounts, AssessmentOptions, AssessmentView
from app.relevance_store import RelevanceStore
from app.tools.runner import PROJECT_ROOT


def test_saved_query_payload_and_p95_at_declared_sizes(tmp_path):
    runs = RunStore(tmp_path / "scale.sqlite3")
    run = runs.enqueue(CheckRequest(kind="meta_smoke"), "scale-fixture-only")
    runs.transition(run.id, stage="complete", message="Seed only", status="finished")
    catalog_id, assessment_id = uuid4().hex, uuid4().hex
    catalog = CatalogView(
        id=catalog_id,
        revision=1,
        run_id=run.id,
        source_project=uuid4().hex,
        source_sha="a" * 40,
        project_root="",
        scope="",
        identity={},
        created_at=now(),
        state="partial",
        options=DiscoveryOptions(),
    )
    assessment = AssessmentView(
        id=assessment_id,
        catalog_id=catalog_id,
        catalog_revision=1,
        run_id=run.id,
        target_project=None,
        target_sha=None,
        target_root=None,
        target_origin=None,
        goal="Scale fixture",
        identity={},
        options=AssessmentOptions(),
        shortlist=[],
        state="partial",
        created_at=now(),
    )
    with runs.connect() as db:
        db.execute(
            "INSERT INTO catalogs VALUES (?,?,?,?,?)",
            (catalog_id, "local-workspace", catalog.source_project, "{}", "scale"),
        )
        db.execute(
            "INSERT INTO catalog_revisions VALUES (?,?,?,?,?)",
            (catalog_id, 1, run.id, catalog.model_dump_json(), "[]"),
        )
        db.execute(
            "INSERT INTO relevance_assessments VALUES (?,?,?,?)",
            (assessment_id, "local-workspace", assessment.model_dump_json(), run.id),
        )
    receipts = []
    catalogs = CatalogStore(runs)
    for size in (100, 1000, 5000, 10000):
        rows, assessments = [], []
        for index in range(size):
            feature_id = f"{index:032x}"
            item = FeatureSummary(
                feature_id=feature_id,
                name=f"Capability {index:05}",
                summary="Bounded saved behavior summary.",
                category="sequences",
                group="library",
                evidence_status="supported",
                aliases=["An old saved alias " * 100] if index < 25 else [],
            )
            summary = item.model_dump_json()
            # Poison detail: list requests must not parse or consume it.
            rows.append(
                (
                    catalog_id,
                    1,
                    feature_id,
                    item.name.casefold(),
                    item.category,
                    "supported",
                    summary,
                    "NOT A DETAIL",
                    hashlib.sha256(b"NOT A DETAIL").hexdigest(),
                    f"{item.name.casefold()} behavior",
                )
            )
            projection = json.dumps(
                {
                    "relevance_status": "relevant",
                    "relevance_score": 5,
                    "reason": "Saved goal relevance.",
                    "target_status": "unknown",
                    "fit_status": "ineligible",
                }
            )
            assessments.append(
                (
                    assessment_id,
                    feature_id,
                    "relevant",
                    5,
                    "ineligible",
                    projection,
                    "NOT A DETAIL",
                    "0" * 64,
                )
            )
        catalog.counts = CatalogCounts(discovered=size, code_supported=size)
        assessment.counts = AssessmentCounts(discovered=size, deeply_assessed=size, relevant=size)
        with runs.connect() as db:
            db.execute("DELETE FROM catalog_features WHERE catalog_id=?", (catalog_id,))
            db.execute("DELETE FROM relevance_features WHERE assessment_id=?", (assessment_id,))
            db.executemany("INSERT INTO catalog_features VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            db.executemany("INSERT INTO relevance_features VALUES (?,?,?,?,?,?,?,?)", assessments)
            db.execute(
                "UPDATE catalog_revisions SET metadata=? WHERE catalog_id=?",
                (catalog.model_dump_json(), catalog_id),
            )
            db.execute(
                "UPDATE relevance_assessments SET metadata=? WHERE id=?",
                (assessment.model_dump_json(), assessment_id),
            )
        measurements = []
        for _ in range(30):
            start = perf_counter()
            page = catalogs.page(
                "local-workspace",
                catalog_id,
                1,
                assessment_id=assessment_id,
                mode="relevant",
                q="capability",
                category="sequences",
                status="supported",
            )
            measurements.append((perf_counter() - start) * 1000)
        payload_bytes = len(page.model_dump_json().encode())
        p95 = quantiles(measurements, n=100)[94]
        assert len(page.items) == 25 and page.matching_filter == size
        assert all(item.aliases == [] for item in page.items)
        assert payload_bytes <= 100 * 1024 and p95 <= 300
        second = catalogs.page(
            "local-workspace",
            catalog_id,
            1,
            assessment_id=assessment_id,
            mode="relevant",
            q="capability",
            category="sequences",
            status="supported",
            cursor=page.next_cursor,
        )
        assert not set(i.feature_id for i in page.items) & set(i.feature_id for i in second.items)
        last = catalogs.page("local-workspace", catalog_id, 1, q=f"{size - 1:05}")
        assert last.matching_filter == 1
        receipts.append(
            {"rows": size, "samples": 30, "p95_ms": round(p95, 3), "page_bytes": payload_bytes}
        )
    # Kept outside source control; the maintained status document records the measured receipt.
    (PROJECT_ROOT / "data/f5-scale.json").write_text(
        json.dumps(
            {
                "measured_at": now(),
                "scope": (
                    "Local SQLite safe-summary query; seeded rows, no provider or network latency."
                ),
                "measurements": receipts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert RelevanceStore(runs).get("local-workspace", assessment_id).counts.discovered == 10000
