"""Preregister scoped live samples, execute normal budgeted jobs and retain FE5 receipts."""

import argparse
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.worker import LocalWorker
from app.catalog_contracts import CatalogCreate, DiscoveryOptions
from app.catalog_store import CatalogStore
from app.config import Settings
from app.contracts import CommitSHA, RepoPath
from app.errors import GraftError
from app.execution import cli_execution
from app.jobs.control import CURRENT
from app.jobs.leases import JobLeases
from app.project_contracts import ProjectRequest
from app.projects import ProjectStore
from app.selection_artifacts import prepare_directory
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import read_regular_file

sys.path.insert(0, str(PROJECT_ROOT))
from scripts.release_validation import source_manifest, timestamp, write_json

OWNER = "local-workspace"


class ExpectedBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    behavior: str
    path: RepoPath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    url: str
    commit_sha: CommitSHA
    project_root: str
    expected: list[ExpectedBehavior] = Field(min_length=1)


class EvaluationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: str
    cases: list[EvaluationCase] = Field(min_length=1)


def reference_checks(projects, selected, excerpts, coverage):
    """Exported excerpts must exactly reproduce admitted pinned ranges and controller hashes."""
    _, repo = projects.open(OWNER, selected.id)
    checked = 0
    for excerpt in excerpts:
        ref = excerpt.ref
        lines = repo.read(ref.path).splitlines()
        text = "\n".join(lines[ref.start_line - 1 : ref.end_line]) + "\n"
        blob = next(f.sha256 for f in repo.inventory.files if f.path == ref.path)
        read_lines = {
            line
            for window in coverage.read_ranges
            if window.path == ref.path
            for line in range(window.start_line, window.end_line + 1)
        }
        if (
            ref.project_id != selected.id
            or ref.project_root != repo.prefix
            or ref.role != selected.requested.role
            or ref.repo_id != repo.snapshot.repo_id
            or ref.commit_sha != repo.snapshot.commit_sha
            or not 1 <= ref.start_line <= ref.end_line <= len(lines)
            or excerpt.text != text
            or ref.blob_sha256 != blob
            or excerpt.sha256 != hashlib.sha256(text.encode()).hexdigest()
            or ref.excerpt_sha256 != excerpt.sha256
            or not set(range(ref.start_line, ref.end_line + 1)) <= read_lines
        ):
            raise ValueError("Exported citation failed exact pin/range/hash validation.")
        checked += 1
    return checked


def evaluate(plan, settings, *, live, options, existing_project=None):
    context = CURRENT.get()
    runs = context.store
    with runs.connect() as db:
        if db.execute(
            "SELECT 1 FROM runs WHERE json_extract(summary,'$.status') IN "
            "('queued','running') LIMIT 1"
        ).fetchone():
            raise ValueError("Finish existing jobs before running this explicit evaluation.")
    directory = prepare_directory(PROJECT_ROOT / "data/feature-extraction/live" / uuid4().hex)
    receipt = {
        "schema_version": "feature-extraction-evaluation-v2",
        "started_at": timestamp(),
        "sources": source_manifest(PROJECT_ROOT),
        "frontend_build": {
            path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(
                read_regular_file(PROJECT_ROOT, path.relative_to(PROJECT_ROOT).as_posix())
            ).hexdigest()
            for path in (PROJECT_ROOT / "frontend/dist").rglob("*")
            if path.is_file()
        },
        "model": settings.meta_model_id,
        "parsers": "disabled",
        "options": options.model_dump(),
        "preregistered_plan": plan.model_dump(mode="json"),
        "cases": [],
        "gates": {
            "semantic_precision": "pending_manual_review",
            "scoped_recall": "pending_manual_review",
            "evidence_honesty": "pending_manual_review",
            "target_comparison": "pending_live_comparison",
        },
        "release": "opt_in_preview; generic default remains disabled",
    }
    write_json(directory / "receipt.json", receipt)
    projects = ProjectStore(runs, PROJECT_ROOT / "data/projects")
    catalogs = CatalogStore(runs)
    worker = LocalWorker(runs, PROJECT_ROOT / "data/artifacts", settings)
    for case in plan.cases:
        result = {"id": case.id, "status": "acquiring", "model_attempts": 0}
        receipt["cases"].append(result)
        write_json(directory / "receipt.json", receipt)
        print(f"{case.id}: pinning declared commit/root", flush=True)
        try:
            if existing_project:
                selected = projects.get(OWNER, existing_project)
                if (
                    selected.requested.url,
                    selected.requested.commit_sha,
                    selected.requested.project_root,
                    selected.requested.role,
                    selected.support.profile_version,
                ) != (
                    case.url,
                    case.commit_sha,
                    case.project_root,
                    "source",
                    "repository-text-v1",
                ):
                    raise ValueError("Existing pin does not match this declared case.")
            else:
                selected = projects.create(
                    OWNER,
                    ProjectRequest(
                        url=case.url,
                        commit_sha=case.commit_sha,
                        role="source",
                        project_root=case.project_root,
                        analysis_profile="repository-text-v1",
                    ),
                )
            _, repo = projects.open(OWNER, selected.id)
            result.update(
                project=selected.model_dump(mode="json"),
                inventory_policy=selected.inventory_policy,
                inventory_sha256=selected.inventory_sha256,
                storage_bytes=sum(p.stat().st_size for p in repo.storage.rglob("*") if p.is_file()),
            )
            expected = []
            for behavior in case.expected:
                lines = repo.read(behavior.path).splitlines()
                if not 1 <= behavior.start_line <= behavior.end_line <= len(lines):
                    raise ValueError("Preregistered behavior has invalid source lines.")
                excerpt = "\n".join(lines[behavior.start_line - 1 : behavior.end_line]) + "\n"
                expected.append(
                    {
                        **behavior.model_dump(),
                        "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                    }
                )
            result["preregistered_evidence"] = expected
            result["status"] = "inspected"
            write_json(directory / "receipt.json", receipt)
            if not live:
                result["status"] = "inspected_no_inference"
                continue
            view = catalogs.submit(
                OWNER,
                selected,
                CatalogCreate(source_project=selected.id, options=options, use_cache=False),
                "text-evaluation-" + uuid4().hex,
                settings.meta_model_id,
                model_configured=settings.model_configured,
            )
            result.update(status="queued", catalog_id=view.id, run_id=view.run_id, revision=1)
            write_json(directory / "receipt.json", receipt)
            row = runs.claim(worker="text-evaluation", expected_run_id=view.run_id)
            if row is None:
                JobLeases(runs).cancel(view.run_id, OWNER)
                raise ValueError("Evaluation did not own the next queued job; no automatic replay.")
            result["status"] = "running"
            write_json(directory / "receipt.json", receipt)
            print(
                f"{case.id}: running catalog {view.id} with declared shared job limits",
                flush=True,
            )
            worker.process(row)
            saved = catalogs.get(OWNER, view.id, 1)
            features = catalogs.features(OWNER, saved)
            result.update(
                status=saved.state,
                catalog_id=view.id,
                revision=1,
                catalog=saved.model_dump(mode="json"),
                model_attempts=saved.usage.get("requests", 0),
                usage=saved.usage,
                returned_model_ids=saved.returned_model_ids,
                supported_ids=[f.feature_id for f in features if f.evidence_status == "supported"],
            )
            result["checked_references"] = reference_checks(
                projects, selected, (e for f in features for e in f.evidence), saved.coverage_v2
            )
            result["within_declared_budgets"] = (
                saved.usage.get("requests", 0) <= options.max_requests
                and saved.usage.get("input_tokens", 0) <= options.max_input_tokens
                and saved.usage.get("output_tokens", 0) <= options.max_output_tokens
            )
            write_json(
                directory / f"{case.id}-features.json",
                {"features": [f.model_dump(mode="json") for f in features]},
            )
        except (GraftError, OSError, ValueError, HTTPException) as error:
            result.update(
                status="failed",
                error_code=getattr(error, "code", "evaluation_failed"),
                detail=str(error)[:600],
            )
        finally:
            write_json(directory / "receipt.json", receipt)
        print(f"{case.id}: {result['status']}", flush=True)
    receipt["finished_at"] = timestamp()
    write_json(directory / "receipt.json", receipt)
    print(directory / "receipt.json", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly run paid Meta jobs; never implied by inspection.",
    )
    parser.add_argument("--case", help="Run only one preregistered case.")
    parser.add_argument(
        "--existing-project", help="Reuse an exact declared pin; always starts a new catalog."
    )
    parser.add_argument("--max-input-tokens", type=int, default=400_000)
    args = parser.parse_args()
    plan = EvaluationPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    if args.case:
        plan.cases = [case for case in plan.cases if case.id == args.case]
        if not plan.cases:
            parser.error("Unknown preregistered case.")
    if args.existing_project and len(plan.cases) != 1:
        parser.error("Reuse a pin only with one declared --case.")
    settings = Settings()
    if settings.auth_mode != "local" or settings.deployment_mode != "local":
        parser.error("This milestone permits local public analysis only.")
    if args.live and not settings.model_configured:
        parser.error("Configure MODEL_API_KEY before explicit live evaluation.")
    with cli_execution(PROJECT_ROOT):
        evaluate(
            plan,
            settings,
            live=args.live,
            options=DiscoveryOptions(max_input_tokens=args.max_input_tokens),
            existing_project=args.existing_project,
        )


if __name__ == "__main__":
    main()
