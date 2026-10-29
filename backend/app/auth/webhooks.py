"""Authenticated durable inbox; invalidation and delivery dedup commit together."""

import hashlib
import hmac
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/webhooks")
MAX_BODY = 1024 * 1024


def numeric(value):
    return value if type(value) is int and value > 0 else None


def object_id(body, field):
    value = body.get(field)
    return numeric(value.get("id")) if isinstance(value, dict) else None


def accept_delivery(auth, delivery: str, event: str, raw: bytes) -> bool:
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "Invalid GitHub webhook payload.") from None
    digest = hashlib.sha256(raw).hexdigest()
    with auth.runs.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT body_digest,event FROM github_webhook_deliveries WHERE delivery_id=?",
            (delivery,),
        ).fetchone()
        if previous:
            if previous["body_digest"] != digest or previous["event"] != event:
                raise HTTPException(409, "Conflicting GitHub delivery identity.")
            return False
        db.execute(
            "INSERT INTO github_webhook_deliveries VALUES (?,?,?,?)",
            (delivery, digest, event, time.time()),
        )
        if event == "github_app_authorization" and body.get("action") == "revoked":
            sender = object_id(body, "sender")
            if not sender:
                raise HTTPException(400, "Missing GitHub authorization identity.")
            row = db.execute("SELECT id FROM users WHERE github_id=?", (sender,)).fetchone()
            if row:
                auth._disconnect(db, row["id"])
        elif event in {"installation", "installation_repositories", "repository"}:
            installation = object_id(body, "installation")
            repository = object_id(body, "repository") if event == "repository" else None
            if not installation and not repository:
                raise HTTPException(400, "Missing GitHub repository grant identity.")
            owners = db.execute(
                "SELECT owner_id FROM repository_cache WHERE installation_id=? OR github_id=? "
                "UNION SELECT owner_id FROM repository_snapshots "
                "WHERE installation_id=? OR github_id=? "
                "UNION SELECT owner_id FROM repository_sync,json_each(state,'$.installations') "
                "WHERE json_each.value=?",
                (installation, repository, installation, repository, installation),
            ).fetchall()
            for row in owners:
                owner = row["owner_id"]
                db.execute("UPDATE users SET access_epoch=access_epoch+1 WHERE id=?", (owner,))
                db.execute("DELETE FROM repository_cache WHERE owner_id=?", (owner,))
                db.execute("DELETE FROM repository_sync WHERE owner_id=?", (owner,))
                auth.runs.cancel_queued_in(db, owner)
            # An old/reordered grant-added delivery never restores cached access.
            # Every subsequent private operation must ask both user and App again.
    return True


@router.post("/github", status_code=202)
async def github_webhook(request: Request):
    settings = request.app.state.settings
    if settings.auth_mode != "github" or not settings.github_webhook_secret:
        raise HTTPException(404, "Not found.")
    signature = request.headers.get("x-hub-signature-256", "")
    delivery = request.headers.get("x-github-delivery", "")
    event = request.headers.get("x-github-event", "")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise HTTPException(401, "Invalid GitHub webhook signature.")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery) or not re.fullmatch(
        r"[a-z_]{1,80}", event
    ):
        raise HTTPException(400, "Invalid GitHub webhook identity.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "GitHub webhook exceeded the supported size.")
    expected = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.get_secret_value().encode(), raw, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid GitHub webhook signature.")
    # SQLite work is synchronous; move it off the ASGI event loop.
    import asyncio

    accepted = await asyncio.to_thread(
        accept_delivery, request.app.state.auth, delivery, event, bytes(raw)
    )
    return {"status": "accepted" if accepted else "duplicate"}
