"""Synthetic saved catalogs for local validation, never live discovery evidence."""

import hashlib

from app.api.contracts import CheckRequest
from app.api.storage import RunStore, now
from app.catalog_contracts import (
    CatalogCounts,
    CatalogView,
    DiscoveryOptions,
    FeatureDetail,
    FeatureSummary,
)
from app.relevance_contracts import (
    AssessmentCounts,
    AssessmentOptions,
    AssessmentView,
    FeatureAssessment,
)

SIZES = (100, 1000, 5000, 10000)


def seed_catalog(
    runs: RunStore, size: int, *, relevant_count: int | None = None, poison_details: bool = False
) -> tuple[CatalogView, AssessmentView]:
    if size not in SIZES:
        raise ValueError("Use a declared release-validation fixture size.")
    relevant_count = size if relevant_count is None else relevant_count
    if not 0 <= relevant_count <= size:
        raise ValueError("Relevant count exceeds the fixture size.")
    run = runs.enqueue(CheckRequest(kind="meta_smoke"), f"synthetic-scale-{size}")
    runs.transition(run.id, stage="complete", message="Synthetic seed only", status="finished")
    catalog = CatalogView(
        id=f"{size:032x}",
        revision=1,
        run_id=run.id,
        source_project="b" * 32,
        source_sha="c" * 40,
        project_root="",
        scope="",
        identity={"fixture": "synthetic-no-inference"},
        state="partial",
        created_at=now(),
        counts=CatalogCounts(discovered=size, code_supported=size),
        options=DiscoveryOptions(),
        qualification="Synthetic scale fixture; no repository discovery or inference occurred.",
    )
    assessment = AssessmentView(
        id=f"{size + 10000:032x}",
        catalog_id=catalog.id,
        catalog_revision=1,
        run_id=run.id,
        target_project=None,
        target_sha=None,
        target_root=None,
        target_origin=None,
        goal="Synthetic saved browsing validation",
        identity=catalog.identity,
        options=AssessmentOptions(),
        shortlist=[],
        state="partial",
        created_at=now(),
        counts=AssessmentCounts(
            discovered=size,
            deeply_assessed=relevant_count,
            relevant=relevant_count,
            unassessed=size - relevant_count,
        ),
        qualification="Synthetic saved assessment; no live relevance judgment occurred.",
    )
    rows, assessments = [], []
    for index in range(size):
        item = FeatureSummary(
            feature_id=f"{index:032x}",
            name=f"Capability {index + 1:05}",
            summary="Synthetic bounded saved behavior summary.",
            category="sequences",
            group="library",
            evidence_status="supported",
            aliases=["An old saved alias " * 100] if index < 25 else [],
        )
        detail = (
            "NOT A DETAIL"
            if poison_details
            else FeatureDetail(
                **item.model_dump(), behavior_key="capability", discovery_refs=[]
            ).model_dump_json()
        )
        rows.append(
            (
                catalog.id,
                1,
                item.feature_id,
                item.name.casefold(),
                item.category,
                item.evidence_status,
                item.model_dump_json(),
                detail,
                hashlib.sha256(detail.encode()).hexdigest(),
                f"{item.name.casefold()} behavior",
            )
        )
        if index < relevant_count:
            judged = FeatureAssessment(
                feature_id=item.feature_id,
                relevance_status="relevant",
                relevance_score=5,
                reason="Synthetic saved relevance.",
                target_status="unknown",
                fit_status="ineligible",
            )
            assessment_detail = "NOT A DETAIL" if poison_details else judged.model_dump_json()
            assessments.append(
                (
                    assessment.id,
                    item.feature_id,
                    "relevant",
                    5,
                    "ineligible",
                    judged.model_dump_json(exclude={"feature_id"}),
                    assessment_detail,
                    hashlib.sha256(assessment_detail.encode()).hexdigest(),
                )
            )
    with runs.connect() as db:
        db.execute(
            "INSERT INTO catalogs VALUES (?,?,?,?,?)",
            (catalog.id, "local-workspace", catalog.source_project, "{}", "synthetic"),
        )
        db.execute(
            "INSERT INTO catalog_revisions VALUES (?,?,?,?,?)",
            (catalog.id, 1, run.id, catalog.model_dump_json(), "[]"),
        )
        db.execute(
            "INSERT INTO relevance_assessments VALUES (?,?,?,?)",
            (assessment.id, "local-workspace", assessment.model_dump_json(), run.id),
        )
        db.executemany("INSERT INTO catalog_features VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT INTO relevance_features VALUES (?,?,?,?,?,?,?,?)", assessments)
    return catalog, assessment
