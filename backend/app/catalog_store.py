"""Atomic owner-scoped catalog jobs, immutable revisions and bounded saved projections."""

import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException

from app.api.storage import now
from app.catalog_contracts import (
    CatalogContinue,
    CatalogCounts,
    CatalogCreate,
    CatalogJob,
    CatalogView,
    FeatureDetail,
    FeatureSummary,
)
from app.errors import EvidenceError
from app.jobs.control import fence_in

VERSIONS = {"analyzer": "python-catalog-v1", "prompt": "discovery-v2", "schema": "catalog-v1"}
TERMINAL = {"complete_for_scope", "partial", "failed", "canceled"}
CATALOG_LIMIT = 5000


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_identity(selected, scope: str, model: str) -> dict:
    return {
        **VERSIONS,
        "origin": selected.snapshot.origin,
        "repo_id": selected.snapshot.repo_id,
        "sha": selected.snapshot.commit_sha,
        "root": selected.requested.project_root,
        "inventory": selected.inventory_sha256,
        "manifest": selected.support.manifest_sha256,
        "profile": selected.support.profile_version,
        "scope": scope,
        "requested_model": model,
        "access": "owner-scoped-public-project",
    }


class CatalogStore:
    def __init__(self, runs):
        self.runs = runs

    def get(self, owner: str, catalog_id: str, revision: int | None = None) -> CatalogView:
        with self.runs.connect() as db:
            return self._get(db, owner, catalog_id, revision)

    @staticmethod
    def _get(db, owner, catalog_id, revision=None) -> CatalogView:
        row = db.execute(
            "SELECT r.metadata,s.summary FROM catalogs c JOIN catalog_revisions r "
            "ON c.id=r.catalog_id JOIN runs s ON s.id=r.run_id "
            "WHERE c.owner_id=? AND c.id=? "
            + ("AND r.revision=? " if revision is not None else "")
            + "ORDER BY r.revision DESC LIMIT 1",
            (owner, catalog_id, revision) if revision is not None else (owner, catalog_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Catalog revision not found.")
        view = CatalogView.model_validate_json(row["metadata"])
        run = json.loads(row["summary"])
        if view.state not in TERMINAL and run["status"] == "finished":
            # Restart/disconnect settles the job without replaying inference or losing batches.
            canceled = run.get("outcome") in {"canceled", "cancelled"}
            view.state = "canceled" if canceled else "failed"
            view.failure_code = (run.get("error") or {}).get(
                "code", "canceled" if canceled else "interrupted"
            )
            view.continuation_available = True
            db.execute(
                "UPDATE catalog_revisions SET metadata=? WHERE catalog_id=? AND revision=?",
                (view.model_dump_json(), catalog_id, view.revision),
            )
        return view

    def submit(
        self,
        owner: str,
        selected,
        body: CatalogCreate | CatalogContinue,
        key: str,
        model: str,
        *,
        catalog_id: str | None = None,
        require_connected=False,
        access_epoch: int | None = None,
        model_configured=True,
    ) -> CatalogView:
        operation = fingerprint({"catalog": catalog_id, "body": body.model_dump(mode="json")})
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM catalog_operations WHERE owner_id=? AND key=?", (owner, key)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != operation:
                    raise HTTPException(409, "This submission key belongs to different inputs.")
                return self._get(
                    db, owner, previous["catalog_id"], previous["revision"]
                ).model_copy(update={"cache_hit": bool(previous["cache_hit"])})
            base = None
            trace_ids = []
            if isinstance(body, CatalogContinue):
                base = self._get(db, owner, catalog_id, body.base_revision)
                latest = self._get(db, owner, catalog_id)
                if base.revision != latest.revision or base.state not in TERMINAL:
                    raise HTTPException(409, "Continue the latest finished catalog revision.")
                if base.identity != source_identity(selected, base.scope, model):
                    raise HTTPException(409, "Discovery versions changed. Start a new catalog.")
                if base.counts.discovered >= CATALOG_LIMIT and body.options.max_files:
                    raise HTTPException(409, "The catalog reached its 5,000-entry limit.")
                trace_ids = list(dict.fromkeys(body.trace_feature_ids))
                if len(trace_ids) > body.options.trace_limit:
                    raise HTTPException(422, "Requested features exceed the declared trace limit.")
                for feature_id in trace_ids:
                    self._feature(db, base, feature_id)
                if not base.continuation_available and not trace_ids:
                    raise HTTPException(409, "No unexamined discovery scope remains.")
                scope, identity = base.scope, base.identity
                revision = base.revision + 1
            else:
                scope = body.scope
                identity = source_identity(selected, scope, model)
                cache_key = fingerprint(
                    {"identity": identity, "options": body.options.model_dump()}
                )
                if body.use_cache:
                    cached = db.execute(
                        "SELECT c.id,r.revision FROM catalogs c JOIN catalog_revisions r "
                        "ON c.id=r.catalog_id WHERE c.owner_id=? AND c.cache_key=? "
                        "AND r.revision=1 AND json_extract(r.metadata,'$.state') IN "
                        "('partial','complete_for_scope') ORDER BY c.rowid DESC LIMIT 1",
                        (owner, cache_key),
                    ).fetchone()
                    if cached:
                        view = self._get(db, owner, cached["id"], cached["revision"])
                        view.cache_hit = True
                        self._operation(db, owner, key, operation, view)
                        return view
                catalog_id, revision = uuid4().hex, 1
            if not model_configured:
                raise HTTPException(409, "Configure MODEL_API_KEY before starting discovery.")
            job = CatalogJob(catalog_id=catalog_id, revision=revision)
            run = self.runs.enqueue_in(
                db,
                job,
                f"catalog:{key}",
                owner_id=owner,
                require_connected=require_connected,
                access_epoch=access_epoch,
            )
            view = CatalogView(
                id=catalog_id,
                revision=revision,
                base_revision=base.revision if base else None,
                run_id=run.id,
                source_project=selected.id,
                source_sha=selected.snapshot.commit_sha,
                project_root=selected.requested.project_root,
                scope=scope,
                identity=identity,
                options=body.options,
                created_at=now(),
            )
            if base:
                for field in (
                    "counts",
                    "processed_paths",
                    "remaining_paths",
                    "examined_paths",
                    "excluded_paths",
                    "unexamined_areas",
                    "batches",
                    "cumulative_usage",
                ):
                    setattr(view, field, getattr(base, field))
                db.execute(
                    "INSERT INTO catalog_features SELECT catalog_id,?,feature_id,name,category,"
                    "evidence_status,summary,detail,sha256,search_text FROM catalog_features "
                    "WHERE catalog_id=? AND revision=?",
                    (revision, catalog_id, base.revision),
                )
            else:
                db.execute(
                    "INSERT INTO catalogs VALUES (?,?,?,?,?)",
                    (
                        catalog_id,
                        owner,
                        selected.id,
                        canonical(identity),
                        cache_key,
                    ),
                )
            db.execute(
                "INSERT INTO catalog_revisions VALUES (?,?,?,?,?)",
                (
                    catalog_id,
                    revision,
                    run.id,
                    view.model_dump_json(),
                    canonical(trace_ids),
                ),
            )
            self._operation(db, owner, key, operation, view)
            return view

    @staticmethod
    def _operation(db, owner, key, operation, view):
        db.execute(
            "INSERT INTO catalog_operations VALUES (?,?,?,?,?,?)",
            (
                owner,
                key,
                operation,
                view.id,
                view.revision,
                int(view.cache_hit),
            ),
        )

    def trace_ids(self, owner, view) -> list[str]:
        self.get(owner, view.id, view.revision)
        with self.runs.connect() as db:
            return json.loads(
                db.execute(
                    "SELECT trace_ids FROM catalog_revisions WHERE catalog_id=? AND revision=?",
                    (view.id, view.revision),
                ).fetchone()[0]
            )

    def save(
        self, owner, view: CatalogView, features=(), *, removed_ids=(), access_epoch=None
    ) -> CatalogView:
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            fence_in(db)
            current = self._get(db, owner, view.id, view.revision)
            if current.state in TERMINAL:
                raise EvidenceError("A frozen catalog revision cannot be overwritten.")
            if access_epoch is not None:
                from app.auth.access import RepositoryAccess

                RepositoryAccess(self.runs, None, None).check_in(db, owner, access_epoch)
            for feature_id in removed_ids:
                db.execute(
                    "DELETE FROM catalog_features "
                    "WHERE catalog_id=? AND revision=? AND feature_id=?",
                    (view.id, view.revision, feature_id),
                )
            for feature in features:
                detail = feature.model_dump_json()
                summary = FeatureSummary.model_validate(
                    feature.model_dump(include=set(FeatureSummary.model_fields))
                )
                summary.aliases = []
                db.execute(
                    "INSERT OR REPLACE INTO catalog_features VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        view.id,
                        view.revision,
                        feature.feature_id,
                        feature.name.casefold(),
                        feature.category,
                        feature.evidence_status,
                        summary.model_dump_json(),
                        detail,
                        hashlib.sha256(detail.encode()).hexdigest(),
                        " ".join(
                            (
                                feature.name,
                                feature.summary,
                                feature.category,
                                feature.group,
                                *feature.aliases,
                            )
                        ).casefold(),
                    ),
                )
            counts = dict(
                db.execute(
                    "SELECT evidence_status,count(*) FROM catalog_features "
                    "WHERE catalog_id=? AND revision=? GROUP BY evidence_status",
                    (view.id, view.revision),
                ).fetchall()
            )
            traced = db.execute(
                "SELECT count(*) FROM catalog_features WHERE catalog_id=? AND revision=? "
                "AND json_extract(summary,'$.trace_state')!='untraced'",
                (view.id, view.revision),
            ).fetchone()[0]
            view.counts = CatalogCounts(
                discovered=sum(counts.values()),
                code_supported=counts.get("supported", 0),
                partial=counts.get("partial", 0),
                unsupported=counts.get("unsupported", 0),
                unverified=counts.get("unverified", 0),
                deeply_traced=traced,
            )
            db.execute(
                "UPDATE catalog_revisions SET metadata=? WHERE catalog_id=? AND revision=?",
                (view.model_dump_json(), view.id, view.revision),
            )
        return view

    @staticmethod
    def _feature(db, view, feature_id) -> FeatureDetail:
        row = db.execute(
            "SELECT detail,sha256 FROM catalog_features WHERE catalog_id=? AND revision=? "
            "AND feature_id=?",
            (view.id, view.revision, feature_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Feature not found in this catalog revision.")
        if hashlib.sha256(row["detail"].encode()).hexdigest() != row["sha256"]:
            raise EvidenceError("Saved catalog evidence failed its integrity check.")
        return FeatureDetail.model_validate_json(row["detail"])

    def feature(self, owner, catalog_id, revision, feature_id) -> FeatureDetail:
        view = self.get(owner, catalog_id, revision)
        with self.runs.connect() as db:
            return self._feature(db, view, feature_id)

    def features(self, owner, view) -> list[FeatureDetail]:
        self.get(owner, view.id, view.revision)
        with self.runs.connect() as db:
            ids = db.execute(
                "SELECT feature_id FROM catalog_features WHERE catalog_id=? AND revision=? "
                "ORDER BY name,feature_id",
                (view.id, view.revision),
            ).fetchall()
            return [self._feature(db, view, row[0]) for row in ids]

    def page(self, owner, catalog_id, revision, **query):
        from app.catalog_query import feature_page

        return feature_page(self, owner, catalog_id, revision, **query)
