"""Atomic admission and immutable, owner-scoped assessment checkpoints."""

import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException

from app.api.storage import now
from app.catalog_store import TERMINAL as CATALOG_TERMINAL
from app.catalog_store import CatalogStore, fingerprint
from app.errors import EvidenceError
from app.jobs.control import fence_in
from app.relevance_contracts import (
    AssessmentCounts,
    AssessmentView,
    ExpansionSummary,
    FeatureAssessment,
    RelevanceJob,
)
from app.relevance_types import AssessmentSummary

TERMINAL = {"complete", "partial", "failed", "canceled"}
VERSIONS = {"policy": "relevance-v2", "prompt": "expansion-v1", "fit": "fit-v1"}


def assessment_identity(catalog, target, goal, model):
    return {
        **VERSIONS,
        "catalog": catalog.id,
        "revision": catalog.revision,
        "source": catalog.identity,
        "goal_sha256": hashlib.sha256(goal.encode()).hexdigest(),
        "requested_model": model,
        "target": {
            "origin": target.snapshot.origin,
            "sha": target.snapshot.commit_sha,
            "root": target.requested.project_root,
            "inventory": target.inventory_sha256,
            "manifest": target.support.manifest_sha256,
            "profile": target.support.profile_version,
            "adapter": target.support.adapter_version,
        }
        if target
        else None,
        "baseline": "not_run",
        "profile_policy": "bounded-python-packet-v1",
    }


class RelevanceStore:
    def __init__(self, runs):
        self.runs = runs

    def get(self, owner, assessment_id):
        with self.runs.connect() as db:
            return self._get(db, owner, assessment_id)

    @staticmethod
    def _get(db, owner, assessment_id):
        row = db.execute(
            "SELECT a.metadata,r.summary FROM relevance_assessments a JOIN runs r "
            "ON r.id=a.run_id WHERE a.id=? AND a.owner_id=?",
            (assessment_id, owner),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Assessment not found.")
        view = AssessmentView.model_validate_json(row["metadata"])
        run = json.loads(row["summary"])
        if view.state not in TERMINAL and run["status"] == "finished":
            view.state = "canceled" if run.get("outcome") in {"canceled", "cancelled"} else "failed"
            view.failure_code = (run.get("error") or {}).get(
                "code", "canceled" if view.state == "canceled" else "interrupted"
            )
            db.execute(
                "UPDATE relevance_assessments SET metadata=? WHERE id=?",
                (view.model_dump_json(), view.id),
            )
        return view

    def submit(
        self,
        owner,
        catalog,
        target,
        body,
        key,
        model,
        *,
        model_configured=True,
        require_connected=False,
        access_epoch=None,
    ):
        operation = fingerprint(body.model_dump(mode="json"))
        identity = assessment_identity(catalog, target, body.goal, model)
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM relevance_operations WHERE owner_id=? AND key=?", (owner, key)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != operation:
                    raise HTTPException(409, "This submission key belongs to different inputs.")
                return self._get(db, owner, previous["assessment_id"])
            catalog = CatalogStore._get(db, owner, catalog.id, catalog.revision)
            if catalog.state not in CATALOG_TERMINAL:
                raise HTTPException(409, "Wait for the catalog revision to finish.")
            if not model_configured:
                raise HTTPException(409, "Configure MODEL_API_KEY before assessing relevance.")
            base = self._get(db, owner, body.base_assessment) if body.base_assessment else None
            if base and (base.state not in TERMINAL or base.identity != identity):
                raise HTTPException(
                    409, "Assess more using the same finished catalog/target/goal context."
                )
            if len(set(body.feature_ids)) != len(body.feature_ids):
                raise HTTPException(422, "Choose each feature only once.")
            if len(body.feature_ids) > body.options.feature_limit:
                raise HTTPException(422, "Selected features exceed the declared assessment limit.")
            assessed = set()
            if base:
                assessed = {
                    r[0]
                    for r in db.execute(
                        "SELECT feature_id FROM relevance_features WHERE assessment_id=?",
                        (base.id,),
                    )
                }
            if body.feature_ids:
                for feature_id in body.feature_ids:
                    CatalogStore._feature(db, catalog, feature_id)
                shortlist = body.feature_ids
            else:
                # Retrieve from safe summary rows only; never decode thousands of evidence cards.
                terms = set(re.findall(r"[a-z0-9_]{3,}", body.goal.casefold()))
                rows = db.execute(
                    "SELECT feature_id,search_text,evidence_status FROM catalog_features "
                    "WHERE catalog_id=? AND revision=?",
                    (catalog.id, catalog.revision),
                ).fetchall()
                rows = sorted(
                    (r for r in rows if r["feature_id"] not in assessed),
                    key=lambda r: (
                        -len(terms & set(re.findall(r"[a-z0-9_]+", r["search_text"]))),
                        -(r["evidence_status"] == "supported"),
                        r["feature_id"],
                    ),
                )
                shortlist = [r["feature_id"] for r in rows[: body.options.feature_limit]]
            if not shortlist:
                raise HTTPException(
                    409, "No unassessed features remain. Select a feature to reassess."
                )
            assessment_id = uuid4().hex
            run = self.runs.enqueue_in(
                db,
                RelevanceJob(assessment_id=assessment_id),
                f"relevance:{key}",
                owner_id=owner,
                require_connected=require_connected,
                access_epoch=access_epoch,
            )
            view = AssessmentView(
                id=assessment_id,
                catalog_id=catalog.id,
                catalog_revision=catalog.revision,
                run_id=run.id,
                target_project=target.id if target else None,
                target_sha=target.snapshot.commit_sha if target else None,
                target_root=target.requested.project_root if target else None,
                target_origin=target.snapshot.origin if target else None,
                goal=body.goal,
                identity=identity,
                options=body.options,
                shortlist=shortlist,
                base_assessment=base.id if base else None,
                created_at=now(),
                cumulative_usage=base.cumulative_usage if base else {},
            )
            if base:
                db.execute(
                    "INSERT INTO relevance_features SELECT ?,feature_id,relevance_status,"
                    "relevance_score,fit_status,summary,detail,sha256 FROM relevance_features "
                    "WHERE assessment_id=?",
                    (view.id, base.id),
                )
            self._counts(db, view, catalog.counts.discovered)
            db.execute(
                "INSERT INTO relevance_assessments VALUES (?,?,?,?)",
                (view.id, owner, view.model_dump_json(), run.id),
            )
            db.execute(
                "INSERT INTO relevance_operations VALUES (?,?,?,?)",
                (owner, key, operation, view.id),
            )
            return view

    @staticmethod
    def _counts(db, view, discovered):
        rows = db.execute(
            "SELECT relevance_status,count(*) FROM relevance_features WHERE assessment_id=? "
            "GROUP BY relevance_status",
            (view.id,),
        ).fetchall()
        counts = dict(rows)
        eligible = db.execute(
            "SELECT count(*) FROM relevance_features "
            "WHERE assessment_id=? AND fit_status='eligible'",
            (view.id,),
        ).fetchone()[0]
        view.counts = AssessmentCounts(
            discovered=discovered,
            relevant=counts.get("relevant", 0),
            eligible=eligible,
            deeply_assessed=counts.get("relevant", 0) + counts.get("less_relevant", 0),
            assessment_failed=counts.get("assessment_failed", 0),
            unassessed=discovered - sum(counts.values()),
        )
        # CandidateScore owns the fit-v1 ordering. No noneligible recommendation is published.
        scored = [
            FeatureAssessment.model_validate_json(r[0]).fit
            for r in db.execute(
                "SELECT detail FROM relevance_features "
                "WHERE assessment_id=? AND fit_status='eligible'",
                (view.id,),
            )
        ]
        view.top3 = [c.feature_id[1:] for c in sorted(scored, key=lambda c: c.ranking_key())[:3]]
        # Semantic suggestions have their own ranking, independent of patch eligibility.
        view.expansion_top3 = [
            ExpansionSummary(feature_id=row[0], name=row[1], benefit=row[2])
            for row in db.execute(
                "SELECT r.feature_id,json_extract(c.summary,'$.name'),"
                "json_extract(r.detail,'$.expansion.benefit') "
                "FROM relevance_features r JOIN catalog_features c "
                "ON c.catalog_id=? AND c.revision=? AND c.feature_id=r.feature_id "
                "WHERE r.assessment_id=? AND r.relevance_status='relevant' "
                "AND json_extract(r.detail,'$.expansion') IS NOT NULL "
                "ORDER BY r.relevance_score DESC,r.feature_id ASC LIMIT 3",
                (view.catalog_id, view.catalog_revision, view.id),
            )
        ]

    def save(self, owner, view, feature=None, *, access_epoch=None):
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            fence_in(db)
            current = self._get(db, owner, view.id)
            if current.state in TERMINAL:
                raise EvidenceError("A finished assessment cannot be overwritten.")
            if access_epoch is not None:
                from app.auth.access import RepositoryAccess

                RepositoryAccess(self.runs, None, None).check_in(db, owner, access_epoch)
            catalog = CatalogStore._get(db, owner, view.catalog_id, view.catalog_revision)
            if feature:
                if feature.feature_id not in view.shortlist:
                    raise EvidenceError("Feature is outside the admitted assessment shortlist.")
                CatalogStore._feature(db, catalog, feature.feature_id)
                detail = feature.model_dump_json()
                summary = AssessmentSummary.model_validate(feature.model_dump())
                db.execute(
                    "INSERT OR REPLACE INTO relevance_features VALUES (?,?,?,?,?,?,?,?)",
                    (
                        view.id,
                        feature.feature_id,
                        feature.relevance_status,
                        feature.relevance_score if feature.relevance_score is not None else -1,
                        feature.fit_status,
                        summary.model_dump_json(),
                        detail,
                        hashlib.sha256(detail.encode()).hexdigest(),
                    ),
                )
            self._counts(db, view, catalog.counts.discovered)
            db.execute(
                "UPDATE relevance_assessments SET metadata=? WHERE id=?",
                (view.model_dump_json(), view.id),
            )

    def feature(self, owner, assessment_id, catalog_id, revision, feature_id):
        view = self.get(owner, assessment_id)
        self.bind(view, catalog_id, revision)
        with self.runs.connect() as db:
            catalog = CatalogStore._get(db, owner, catalog_id, revision)
            CatalogStore._feature(db, catalog, feature_id)
            row = db.execute(
                "SELECT detail,sha256 FROM relevance_features "
                "WHERE assessment_id=? AND feature_id=?",
                (view.id, feature_id),
            ).fetchone()
        if not row:
            return FeatureAssessment(feature_id=feature_id, relevance_status="unassessed")
        if hashlib.sha256(row["detail"].encode()).hexdigest() != row["sha256"]:
            raise EvidenceError("Saved assessment failed its integrity check.")
        return FeatureAssessment.model_validate_json(row["detail"])

    @staticmethod
    def bind(view, catalog_id, revision):
        if (view.catalog_id, view.catalog_revision) != (catalog_id, revision):
            raise HTTPException(409, "Assessment belongs to a different catalog revision.")
        if view.state not in TERMINAL:
            raise HTTPException(409, "Wait for this assessment to finish before browsing it.")
