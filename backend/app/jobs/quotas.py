"""Atomic daily charges and disk reservations shared across jobs and continuations."""

from datetime import UTC, datetime

from app.jobs.control import CURRENT, ResourceLimit

LIMITS = {
    "github_requests": "daily_github_requests",
    "model_attempts": "daily_model_attempts",
    "model_input_tokens": "daily_model_input_tokens",
    "model_output_tokens": "daily_model_output_tokens",
}


def charge(resource, amount=1, *, actual=False):
    context = CURRENT.get()
    if context is None:
        return
    if not actual:
        context.check()
    day = datetime.now(UTC).date().isoformat()
    with context.store.usage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not actual:
            for name, setting in LIMITS.items():
                if name.startswith("model_") != resource.startswith("model_"):
                    continue
                used = db.execute(
                    "SELECT amount FROM resource_usage WHERE day=? AND owner_id=? AND resource=?",
                    (day, context.owner, name),
                ).fetchone()
                if (used[0] if used else 0) + (amount if name == resource else 1) > getattr(
                    context.store.settings, setting
                ):
                    raise ResourceLimit("The account's daily provider allowance is exhausted.")
                total = db.execute(
                    "SELECT coalesce(sum(amount),0) FROM resource_usage WHERE day=? AND resource=?",
                    (day, name),
                ).fetchone()[0]
                if total + (amount if name == resource else 1) > getattr(
                    context.store.settings, f"global_{setting}"
                ):
                    raise ResourceLimit("The shared daily provider allowance is exhausted.")
        db.execute(
            "INSERT INTO resource_usage VALUES (?,?,?,?) ON CONFLICT(day,owner_id,resource) "
            "DO UPDATE SET amount=amount+excluded.amount",
            (day, context.owner, resource, amount),
        )
        if context.run_id:
            db.execute(
                "INSERT INTO job_metrics VALUES (?,?,?,?) ON CONFLICT(run_id,resource) "
                "DO UPDATE SET amount=amount+excluded.amount",
                (context.run_id, resource, amount, context.owner),
            )


def metric(resource, amount=1):
    """Secret-free counters; usable during cancellation/finalization."""
    context = CURRENT.get()
    if context and context.run_id:
        with context.store.usage.connect() as db:
            db.execute(
                "INSERT INTO job_metrics VALUES (?,?,?,?) ON CONFLICT(run_id,resource) "
                "DO UPDATE SET amount=amount+excluded.amount",
                (context.run_id, resource, amount, context.owner),
            )
