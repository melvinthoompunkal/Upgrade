"""Run preregistered live semantic outcomes with normal owned jobs and no target execution."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.api.worker import LocalWorker
from app.catalog_store import CatalogStore
from app.config import Settings
from app.execution import cli_execution
from app.jobs.control import CURRENT
from app.jobs.leases import JobLeases
from app.jobs.resources import Resources
from app.project_contracts import ProjectRequest
from app.projects import ProjectStore
from app.relevance_contracts import AssessmentCreate, AssessmentOptions
from app.relevance_store import RelevanceStore
from app.selection_artifacts import prepare_directory
from app.tools.runner import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))
from scripts.release_validation import source_manifest, timestamp, write_json  # noqa: E402
from scripts.validate_text_analysis import reference_checks  # noqa: E402

OWNER = "local-workspace"


def comparison_target(projects, request, existing_id=None):
    if existing_id is None:
        return projects.create(OWNER, request)
    selected = projects.get(OWNER, existing_id)
    if (
        selected.private
        or selected.repository_snapshot
        or selected.requested.model_dump() != request.model_dump()
    ):
        raise ValueError("Existing target does not match the declared public pin/profile/role.")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--case", help="Run only one preregistered case as a new explicit job.")
    parser.add_argument("--max-requests", type=int, choices=range(1, 9), default=8)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly run paid Meta comparison jobs."
    )
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if args.case:
        plan = {**plan, "cases": [case for case in plan["cases"] if case["id"] == args.case]}
        if not plan["cases"]:
            parser.error("Unknown preregistered comparison case.")
    if not args.live:
        print(json.dumps({"status": "not_run", "cases": len(plan["cases"])}))
        return
    settings = Settings()
    if (
        settings.auth_mode != "local"
        or settings.deployment_mode != "local"
        or not settings.model_configured
    ):
        parser.error("Configured local public Meta analysis is required.")
    with cli_execution(PROJECT_ROOT):
        runs = CURRENT.get().store
        projects = ProjectStore(runs, PROJECT_ROOT / "data/projects")
        catalogs, assessments = CatalogStore(runs), RelevanceStore(runs)
        catalog = catalogs.get(OWNER, plan["source_catalog"], plan["source_revision"])
        catalogs.feature(OWNER, catalog.id, catalog.revision, plan["feature_id"])
        source = projects.get(OWNER, catalog.source_project)
        with runs.connect() as db:
            if db.execute(
                "SELECT 1 FROM runs WHERE json_extract(summary,'$.status') IN "
                "('queued','running') LIMIT 1"
            ).fetchone():
                parser.error("Finish queued/running jobs before this explicit validation.")
        directory = prepare_directory(
            PROJECT_ROOT / "data/feature-extraction/comparison" / uuid4().hex
        )
        receipt = {
            "schema_version": "text-comparison-evaluation-v2",
            "started_at": timestamp(),
            "sources": source_manifest(PROJECT_ROOT),
            "preregistered_plan": plan,
            "cases": [],
            "target_execution": "not_run",
            "storage_limits": {
                "job_artifact_bytes": settings.job_artifact_bytes,
                "owner_bytes": settings.storage_owner_bytes,
                "global_bytes": settings.storage_global_bytes,
            },
        }
        worker = LocalWorker(runs, PROJECT_ROOT / "data/artifacts", settings)
        options = AssessmentOptions(
            feature_limit=1,
            max_requests=args.max_requests,
            max_input_tokens=400_000,
            max_output_tokens=12_000,
            max_seconds=300,
        )
        receipt["options"] = options.model_dump()
        targets = {}
        for case in plan["cases"]:
            result = {"id": case["id"], "expected": case["expected"], "status": "acquiring"}
            receipt["cases"].append(result)
            write_json(directory / "receipt.json", receipt)
            try:
                request = ProjectRequest(
                    url=case["url"],
                    commit_sha=case["commit_sha"],
                    role="target",
                    project_root=case["project_root"],
                    analysis_profile="repository-text-v1",
                )
                existing_id = case.get("existing_target_project")
                key = request.model_dump_json() + (existing_id or "")
                if key not in targets:
                    targets[key] = comparison_target(projects, request, existing_id)
                target = targets[key]
                result.update(
                    target=target.model_dump(mode="json"), target_reused=bool(existing_id)
                )
                view = assessments.submit(
                    OWNER,
                    catalog,
                    target,
                    AssessmentCreate(
                        catalog_id=catalog.id,
                        catalog_revision=catalog.revision,
                        target_project=target.id,
                        goal=case["goal"],
                        feature_ids=[plan["feature_id"]],
                        options=options,
                    ),
                    "text-comparison-" + uuid4().hex,
                    settings.meta_model_id,
                )
                result.update(
                    status="queued", assessment_id=view.id, target=target.model_dump(mode="json")
                )
                write_json(directory / "receipt.json", receipt)
                row = runs.claim(worker="text-comparison-evaluation", expected_run_id=view.run_id)
                if row is None:
                    JobLeases(runs).cancel(view.run_id, OWNER)
                    raise ValueError("Other work owns the next queue slot; no inference replay.")
                print(f"{case['id']}: assessing pinned target", flush=True)
                worker.process(row)
                saved = assessments.get(OWNER, view.id)
                feature = assessments.feature(
                    OWNER, view.id, catalog.id, catalog.revision, plan["feature_id"]
                )
                matched = (
                    feature.relevance_status == "less_relevant"
                    if case["expected"] == "less_relevant"
                    else feature.target_status == case["expected"]
                    and feature.relevance_status != "assessment_failed"
                )
                with runs.usage.connect() as db:
                    unknown = db.execute(
                        "SELECT amount FROM job_metrics WHERE run_id=? AND owner_id=? "
                        "AND resource='model_unknown_usage'",
                        (view.run_id, OWNER),
                    ).fetchone()
                unknown_attempts = unknown[0] if unknown else 0
                result.update(
                    status=saved.state,
                    actual=feature.model_dump(mode="json"),
                    matched=matched,
                    usage=saved.usage,
                    returned_model_ids=saved.returned_model_ids,
                    unknown_usage_attempts=unknown_attempts,
                    within_declared_budgets=(
                        saved.usage.get("requests", 0) <= options.max_requests
                        and saved.usage.get("input_tokens", 0) <= options.max_input_tokens
                        and saved.usage.get("output_tokens", 0) <= options.max_output_tokens
                    )
                    if not unknown_attempts
                    else None,
                    implementation_unassessed=feature.fit is None
                    and feature.fit_status == "unassessed",
                )
                result["checked_references"] = sum(
                    reference_checks(
                        projects,
                        selected,
                        (e for e in feature.evidence if e.ref.role == role),
                        saved.coverage_v2[role],
                    )
                    for role, selected in (("source", source), ("target", target))
                )
            except Exception as error:
                result.update(status="failed", detail=str(error)[:600])
            finally:
                Resources(runs).settle_artifacts(PROJECT_ROOT / "data/artifacts")
                write_json(directory / "receipt.json", receipt)
        receipt["finished_at"] = timestamp()
        write_json(directory / "receipt.json", receipt)
        print(directory / "receipt.json", flush=True)


if __name__ == "__main__":
    main()
