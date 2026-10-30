"""Owner-scoped job actions, resource summaries and private-transmission consent."""

import hashlib
import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.contracts import RunSummary
from app.api.security import require_local_origin
from app.auth.snapshots import access_error, service
from app.config import META_BASE_URL
from app.deployment import execution_lock_path
from app.errors import GraftError
from app.execution import ProcessLock
from app.jobs.leases import JobLeases
from app.jobs.privacy import POLICY

router = APIRouter(prefix="/api")


class UsageView(BaseModel):
    unfinished_jobs: int
    owner_job_limit: int
    reserved_bytes: int
    storage_limit_bytes: int
    retention_days: int
    daily_usage: dict[str, int]
    daily_limits: dict[str, int]
    private_model_analysis_enabled: bool = False
    hosted_execution_enabled: bool = False


class CleanupView(BaseModel):
    id: str
    state: str
    deleted_count: int


@router.post("/runs/{run_id}/cancel", response_model=RunSummary)
def cancel_run(run_id: str, request: Request):
    require_local_origin(request)
    return JobLeases(request.app.state.runs).cancel(run_id, request.state.principal.owner_id)


@router.get("/usage", response_model=UsageView)
def usage(request: Request):
    store, owner = request.app.state.runs, request.state.principal.owner_id
    with store.connect() as db:
        unfinished = db.execute(
            "SELECT count(*) FROM runs WHERE owner_id=? "
            "AND json_extract(summary,'$.status')!='finished'",
            (owner,),
        ).fetchone()[0]
        size = db.execute(
            "SELECT coalesce(sum(bytes),0) FROM owned_resources WHERE owner_id=?", (owner,)
        ).fetchone()[0]
    with store.usage.connect() as db:
        daily = dict(
            db.execute(
                "SELECT resource,amount FROM resource_usage WHERE owner_id=? AND day=?",
                (owner, datetime.now(UTC).date().isoformat()),
            ).fetchall()
        )
    from app.jobs.quotas import LIMITS

    settings = store.settings
    return UsageView(
        unfinished_jobs=unfinished,
        owner_job_limit=settings.job_owner_limit,
        reserved_bytes=size,
        storage_limit_bytes=settings.storage_owner_bytes,
        retention_days=settings.retention_days,
        daily_usage=daily,
        daily_limits={name: getattr(settings, key) for name, key in LIMITS.items()},
    )


@router.delete("/privacy/data", response_model=CleanupView)
def delete_data(request: Request):
    from app.jobs.retention import Retention

    require_local_origin(request)
    try:
        with ProcessLock(execution_lock_path(request.app.state.settings)):
            return Retention(request.app.state.runs, request.app.state.data_root).delete_owner(
                request.state.principal.owner_id
            )
    except GraftError as error:
        raise HTTPException(409, str(error)) from None
    except OSError:
        raise HTTPException(
            409, "Deletion is incomplete. Retry cleanup; retained rows identify remaining data."
        ) from None


@router.get("/privacy/deletions/{receipt_id}", response_model=CleanupView)
def deletion_status(receipt_id: str, request: Request):
    with request.app.state.runs.connect() as db:
        row = db.execute(
            "SELECT id,state,deleted_count FROM cleanup_receipts WHERE id=? AND owner_id=?",
            (receipt_id, request.state.principal.owner_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Deletion receipt not found.")
    return dict(row)


class ConsentInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_snapshot: str = Field(pattern=r"^[a-f0-9]{32}$")
    target_snapshot: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ConsentDecision(ConsentInputs):
    identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: str
    accepted: bool


class Disclosure(BaseModel):
    identity: str
    policy: str = POLICY
    provider_endpoint: str = META_BASE_URL
    snapshots: list[dict[str, str]]
    transmission: str = (
        "Bounded code excerpts, evidence references and test summaries. "
        "Likely secrets are rejected."
    )
    consented: bool
    analysis_enabled: bool = False


def disclosure(body, request):
    owner = request.state.principal.owner_id
    provider = service(request)
    snapshots = []
    for role, identifier in (("source", body.source_snapshot), ("target", body.target_snapshot)):
        if not identifier:
            continue
        view = provider.get(owner, identifier)
        if view.role != role:
            raise HTTPException(422, "Snapshot role does not match this disclosure.")
        snapshots.append(
            {
                "id": view.id,
                "role": role,
                "repository": view.full_name,
                "sha": view.commit_sha,
                "root": view.project_root,
                "inventory": view.inventory_sha256,
            }
        )
    identity = hashlib.sha256(
        json.dumps(
            {"owner": owner, "snapshots": snapshots, "policy": POLICY, "provider": META_BASE_URL},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with request.app.state.runs.connect() as db:
        consented = (
            db.execute(
                "SELECT 1 FROM model_consents WHERE owner_id=? AND identity=? AND policy=?",
                (owner, identity, POLICY),
            ).fetchone()
            is not None
        )
    return Disclosure(identity=identity, snapshots=snapshots, consented=consented)


@router.post("/privacy/model-disclosure", response_model=Disclosure)
def model_disclosure(body: ConsentInputs, request: Request):
    require_local_origin(request)
    try:
        return disclosure(body, request)
    except GraftError as error:
        raise access_error(error) from None


@router.post("/privacy/model-consent", response_model=Disclosure)
def model_consent(body: ConsentDecision, request: Request):
    require_local_origin(request)
    try:
        provider = service(request)
        owner = request.state.principal.owner_id
        epoch = provider.access.epoch(owner)
        view = disclosure(body, request)
        if body.identity != view.identity or body.policy != POLICY:
            raise HTTPException(409, "The disclosure changed. Review the current pinned snapshots.")
        with request.app.state.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            provider.access.check_in(db, owner, epoch)
            if body.accepted:
                db.execute(
                    "INSERT OR IGNORE INTO model_consents VALUES (?,?,?,?)",
                    (
                        owner,
                        view.identity,
                        POLICY,
                        time.time(),
                    ),
                )
            else:
                db.execute(
                    "DELETE FROM model_consents WHERE owner_id=? AND identity=?",
                    (owner, view.identity),
                )
        return view.model_copy(update={"consented": body.accepted})
    except GraftError as error:
        raise access_error(error) from None
