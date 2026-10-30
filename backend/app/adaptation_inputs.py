"""Resolve an explicit F5 shortlist selection without treating relevance as verification."""

from fastapi import HTTPException

from app.catalog_store import CatalogStore, source_identity
from app.relevance_store import RelevanceStore, assessment_identity


def adaptation_inputs(request, projects, owner, model):
    assessments = RelevanceStore(projects.runs)
    view = assessments.get(owner, request.assessment_id)
    if view.state not in {"complete", "partial"} or not view.target_project:
        raise HTTPException(409, "Choose a finished assessment with a pinned target.")
    catalog = CatalogStore(projects.runs).get(owner, view.catalog_id, view.catalog_revision)
    feature = CatalogStore(projects.runs).feature(
        owner, catalog.id, catalog.revision, request.feature_id
    )
    assessment = assessments.feature(
        owner, view.id, catalog.id, catalog.revision, feature.feature_id
    )
    if (
        feature.trace_state != "validated"
        or feature.evidence_status != "supported"
        or not feature.card
        or not feature.card.source_tests
        or assessment.relevance_status != "relevant"
        or assessment.target_status not in {"potential_gap", "already_present"}
        or not assessment.fit
        or not assessment.fit.criteria.meets_thresholds
    ):
        raise HTTPException(
            409, "Select a supported code-backed feature with sufficient assessed fit."
        )
    source = projects.get(owner, catalog.source_project)
    target = projects.get(owner, view.target_project)
    if source.requested.role != "source" or target.requested.role != "target":
        raise HTTPException(409, "Source and target roles do not match the saved assessment.")
    if not target.support.execution_supported:
        raise HTTPException(409, "The target needs a reviewed native offline test adapter.")
    if feature.card.license_spdx != "MIT":
        raise HTTPException(409, "F7 currently supports the reviewed standard MIT reuse profile.")
    if source_identity(source, catalog.scope, model) != catalog.identity or (
        assessment_identity(catalog, target, view.goal, model) != view.identity
    ):
        raise HTTPException(409, "Saved analysis versions changed. Request fresh analysis.")
    return view, catalog, feature, source, target
