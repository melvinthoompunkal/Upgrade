"""Durably registered generated containers, reconciled only under execution ownership."""

import re

from app.jobs.control import CURRENT
from app.jobs.resources import Resources


def register_container(name):
    context = CURRENT.get()
    if context:
        return Resources(context.store).reserve(
            context.owner, "container", name, 0, run_id=context.run_id
        )
    return None


def forget_container(identifier):
    context = CURRENT.get()
    if context and identifier:
        Resources(context.store).forget(identifier)


def reconcile_containers(store):
    from app.tools.runner import docker
    from app.tools.runner_artifacts import RunnerError

    with store.connect() as db:
        rows = db.execute("SELECT * FROM owned_resources WHERE kind='container'").fetchall()
    for row in rows:
        if not re.fullmatch(r"graft-test-[0-9a-f]{32}", row["location"]):
            raise RunnerError("Invalid owned container identity; operator recovery is required.")
        # Docker rm fails for an already-removed container; inspect the exact generated name.
        names = docker(
            "ps", "-a", "--filter", f"name=^/{row['location']}$", "--format", "{{.Names}}"
        )
        if names:
            if names != row["location"]:
                raise RunnerError("Owned container lookup was inconsistent.")
            docker("rm", "--force", row["location"])
        Resources(store).forget(row["id"])
