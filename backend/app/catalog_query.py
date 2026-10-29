"""Inference-free catalog views with assessment-bound stable seek pagination."""

import base64
import hashlib
import hmac
import json

from fastapi import HTTPException

from app.catalog_contracts import FeaturePage, FeatureSummary
from app.catalog_store import TERMINAL, canonical, fingerprint
from app.relevance_store import RelevanceStore
from app.relevance_types import AssessmentSummary


def feature_page(
    catalogs,
    owner,
    catalog_id,
    revision,
    *,
    q="",
    status=None,
    category=None,
    limit=25,
    cursor=None,
    assessment_id=None,
    mode="all",
    sort=None,
    relevance_status=None,
    fit_status=None,
):
    view = catalogs.get(owner, catalog_id, revision)
    if view.state not in TERMINAL:
        raise HTTPException(409, "Feature pages become available when this revision finishes.")
    assessment = RelevanceStore(catalogs.runs).get(owner, assessment_id) if assessment_id else None
    if assessment:
        RelevanceStore.bind(assessment, catalog_id, revision)
    elif mode == "relevant" or relevance_status or fit_status or sort == "relevance":
        raise HTTPException(409, "Choose a saved target/goal assessment for this view.")
    sort = sort or ("relevance" if mode == "relevant" else "category")
    q = " ".join(q.split()).casefold()
    binding = fingerprint(
        [
            owner,
            catalog_id,
            revision,
            assessment_id,
            mode,
            q,
            status,
            category,
            limit,
            sort,
            relevance_status,
            fit_status,
        ]
    )
    conditions = ["f.catalog_id=?", "f.revision=?"]
    values = [catalog_id, revision]
    for condition, value in (
        ("instr(f.search_text,?)>0", q),
        ("f.evidence_status=?", status),
        ("f.category=?", category),
        ("coalesce(a.relevance_status,'unassessed')=?", relevance_status),
        ("coalesce(a.fit_status,'unassessed')=?", fit_status),
    ):
        if value:
            conditions.append(condition)
            values.append(value)
    if mode == "relevant":
        conditions.append("a.relevance_status='relevant'")
    keys = {
        "name": ["f.name", "f.feature_id"],
        "category": ["f.category", "f.name", "f.feature_id"],
        "relevance": ["-coalesce(a.relevance_score,-1)", "f.name", "f.feature_id"],
    }[sort]
    table = (
        "catalog_features f LEFT JOIN relevance_features a "
        "ON a.assessment_id=? AND a.feature_id=f.feature_id"
    )
    with catalogs.runs.connect() as db:
        secret = db.execute("SELECT key FROM catalog_cursor_key WHERE id=1").fetchone()[0]
        total = db.execute(
            "SELECT count(*) FROM " + table + " WHERE " + " AND ".join(conditions),
            (assessment_id, *values),
        ).fetchone()[0]
        if cursor:
            try:
                payload, signature = cursor.split(".")
                expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError
                context, position = json.loads(base64.urlsafe_b64decode(payload))
                if (
                    context != binding
                    or not isinstance(position, list)
                    or len(position) != len(keys)
                ):
                    raise ValueError
                if any(not isinstance(p, str) for p in position[1:]):
                    raise ValueError
                if sort == "relevance":
                    if type(position[0]) is not int or not -5 <= position[0] <= 1:
                        raise ValueError
                elif not isinstance(position[0], str):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise HTTPException(422, "Invalid cursor for this revision and query.") from None
            conditions.append("(" + ",".join(keys) + ")>(" + ",".join("?" for _ in keys) + ")")
            values.extend(position)
        rows = db.execute(
            "SELECT f.summary,a.summary AS assessment_summary,"
            + ",".join(f"{k} AS k{i}" for i, k in enumerate(keys))
            + " FROM "
            + table
            + " WHERE "
            + " AND ".join(conditions)
            + " ORDER BY "
            + ",".join(keys)
            + " LIMIT ?",
            (assessment_id, *values, limit + 1),
        ).fetchall()
    following = None
    if len(rows) > limit:
        last = rows[limit - 1]
        payload = base64.urlsafe_b64encode(
            canonical([binding, [last[f"k{i}"] for i in range(len(keys))]]).encode()
        ).decode()
        following = payload + "." + hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    items = []
    for row in rows[:limit]:
        item = FeatureSummary.model_validate_json(row["summary"])
        # Legacy revisions may contain long alias lists; aliases belong in lazy detail.
        item.aliases = []
        if assessment:
            item.assessment = (
                AssessmentSummary.model_validate_json(row["assessment_summary"])
                if row["assessment_summary"]
                else AssessmentSummary()
            )
        items.append(item)
    return FeaturePage(
        catalog_id=catalog_id,
        catalog_revision=revision,
        items=items,
        counts=view.counts,
        matching_filter=total,
        next_cursor=following,
        assessment_id=assessment_id,
        mode=mode,
        assessment_counts=assessment.counts.model_dump() if assessment else None,
        limit_reasons=assessment.limit_reasons if assessment else view.limit_reasons,
    )
