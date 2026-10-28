"""Select one evidence-backed source feature for the registered pinned target (Stage 4A)."""

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from app.config import Settings
from app.errors import EvidenceError, GraftError
from app.fit_matcher import MATCH_PROMPT_VERSION, SOURCE_POLICY_VERSION, FitMatcher
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.models.schema import strict_schema
from app.pinned_baseline import record_pinned_baseline
from app.selection_artifacts import ArtifactStore, feature_refs, read_log
from app.selection_baseline import SelectionBaseline
from app.selection_contracts import AssessmentBatch, TargetProfileProposal
from app.target_profiler import PROFILE_PROMPT_VERSION, TargetProfiler, inspect_target
from app.tools.evidence import EvidenceSession
from app.tools.repositories import (
    SCRAPY_SHA,
    SCRAPY_URL,
    RepositoryManager,
    registered_target,
    verify_original,
)
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import file_hashes
from app.tracer import SourceTracer
from app.workflow_inputs import SelectionInputs

REQUIRED_OUTPUTS = (
    "source-trace.json",
    "source-catalog.json",
    "target-profile.json",
    "baseline-binding.json",
    "matcher-assessment.json",
    "fit-result.json",
    "evidence-reads-source.json",
    "evidence-reads-target.json",
    "report.md",
)


def preserve(manager, source, target, original, worktree) -> dict:
    results = {}
    if source is not None:
        source.verify()
        results["source_unchanged"] = True
    if target is not None:
        target.verify()
        results["original_target_unchanged"] = verify_original(manager.git, original)
        if worktree is not None:
            results["worktree_unchanged"] = file_hashes(
                worktree, tuple(f.path for f in target.inventory.files)
            ) == {f.path: f.sha256 for f in target.inventory.files} and not manager.git.run(
                "status", "--porcelain", cwd=worktree
            )
        if not all(results.values()):
            raise EvidenceError("Original target or managed worktree changed during selection.")
    return results


def human_report(report: dict, profile, result) -> str:
    lines = [
        "# Stage 4A feature selection",
        "",
        f"Outcome: **{report['status']}**.",
        "",
        "Analyzed source features: **1**. Optional broader comparison (4B) is pending.",
        "",
        "Selection assesses a proposed adaptation. No target patch or post-implementation "
        "verification was performed.",
        "",
    ]
    if profile is not None:
        lines.extend(
            [
                f"Target: `{profile.target_sha}`. Ordinary baseline passed.",
                "",
                "Capabilities: " + "; ".join(c.behavior for c in profile.capabilities),
                "",
                "Gaps: " + ("; ".join(g.description for g in profile.gaps) or "None supported."),
                "",
                "Coverage is bounded static analysis plus captured tests. "
                "Examined paths can contain unread lines; source tests were not executed.",
                "",
            ]
        )
    if result is not None:
        lines.extend(
            [
                f"Selected: `{result.selected_feature_id}`."
                if result.selected_feature_id
                else "No safe fit: " + result.selection_reason,
                "",
                "| Candidate | Gap | Isolation | Deps | Stack | Tests | Total | Eligible |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for candidate in result.candidates:
            c = candidate.criteria
            lines.append(
                f"| {candidate.feature_id} | {c.gap_fit.rating} | {c.isolation.rating} | "
                f"{c.dependency_cost.rating} | {c.stack_compatibility.rating} | "
                f"{c.testability.rating} | {candidate.total} | {candidate.eligible} |"
            )
        for rejection in result.rejected:
            lines.extend(
                ["", f"Rejected `{rejection.feature_id}`: " + "; ".join(rejection.reasons)]
            )
    if "detail" in report:
        lines.extend(["", f"Failure ({report['error_code']}): {report['detail']}"])
    lines.extend(
        [
            "",
            "Artifacts: " + ", ".join(f"[{p}]({p})" for p in sorted(report["artifact_hashes"])),
            "",
        ]
    )
    return "\n".join(lines)


async def record_fit_selection(
    args: SelectionInputs, *, progress: Callable[[str, str], None] = lambda *_: None
) -> tuple[dict, Path]:
    run_id = f"fit-select-{uuid4().hex}"
    directory = PROJECT_ROOT / "data" / "artifacts" / run_id
    directory.mkdir(parents=True)
    report_path = directory / "report.json"
    report = {
        "schema_version": "1",
        "run_id": run_id,
        "status": "failed",
        "stage": "created",
        "created_at": datetime.now(UTC).isoformat(),
        "goal": args.goal,
        "scope": "Stage 4A: one bounded retry candidate, sequential shared-budget analysis.",
        "analyzed_feature_count": 0,
        "actual_patch_scope_checked": False,
        "post_implementation_verified": False,
        "rubric_version": "fit-v1",
        "source_use_policy_version": SOURCE_POLICY_VERSION,
        "prompt_versions": {
            "source": "retry-trace-v1",
            "target": PROFILE_PROMPT_VERSION,
            "matcher": MATCH_PROMPT_VERSION,
        },
        "schema_version_selection": "1",
        "original_target_unchanged": None,
        "worktree_unchanged": None,
    }
    store = ArtifactStore(directory, {"run_id": run_id})
    budget = ModelBudget()
    client = manager = source = target = original = worktree = None
    source_evidence = target_evidence = tracer = profiler = matcher = baseline = None
    profile = result = None
    try:
        strict_schema(TargetProfileProposal)
        strict_schema(AssessmentBatch)
        settings = Settings()
        client = MetaClient(settings, budget=budget)
        report["controller_hashes"] = file_hashes(
            PROJECT_ROOT,
            tuple(
                p.relative_to(PROJECT_ROOT).as_posix()
                for p in sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
            ),
        )
        async with client:
            manager = await asyncio.to_thread(RepositoryManager, directory / "repositories")
            target, original = await asyncio.to_thread(
                manager.local_target, registered_target(PROJECT_ROOT, args.target)
            )
            worktree = await asyncio.to_thread(manager.worktree, target, run_id)
            target_evidence = EvidenceSession(target)
            report.update(
                target=target.snapshot.model_dump(mode="json"),
                original_target=original,
                target_path_prefix=target.prefix,
                managed_worktree=str(worktree),
                stage="inputs_pinned",
            )
            inspect_target(target_evidence)  # Unsupported inputs stop before Docker or inference.
            progress(
                "checks", "Running isolation, committed baseline, and retry diagnostic suites."
            )
            baseline_report, baseline_path = await asyncio.to_thread(
                record_pinned_baseline, target, original, directory, args.image_receipt
            )
            baseline_path.replace(directory / "baseline-report.json")
            store.adopt("baseline-report.json")
            store.adopt("baseline-binding.json")
            for capture in baseline_report["captures"]:
                suite = capture["suite"]
                for name in ("capture.json", *capture["artifact_hashes"]):
                    artifact_id = f"test-captures/{suite}/{name}"
                    if (directory / artifact_id).is_file():
                        store.adopt(artifact_id)
            if baseline_report["status"] != "passed":
                error = EvidenceError(baseline_report.get("detail", "Pinned baseline failed."))
                error.code = baseline_report.get("error_code", "baseline_failed")
                raise error
            baseline = SelectionBaseline.from_run(directory, target, args.image_receipt)
            report.update(
                stage="baseline_passed",
                retry_diagnostic_status=baseline_report["retry_diagnostic_status"],
            )
            source = await asyncio.to_thread(
                manager.public_source, args.source_url, args.source_sha
            )
            report["source"] = source.snapshot.model_dump(mode="json")
            store.binding.update(
                source_sha=source.snapshot.commit_sha,
                target_sha=target.snapshot.commit_sha,
                target_repo_id=target.snapshot.repo_id,
                target_path_prefix=target.prefix,
            )
            for repo in (source, target):
                store.adopt("repositories/" + repo.snapshot.inventory_artifact_id)
            source_evidence = EvidenceSession(source)
            progress("analysis", "Tracing source behavior using pinned code and test references.")
            tracer = SourceTracer(
                client, source_evidence, output_tokens=settings.meta_trace_max_completion_tokens
            )
            trace = await tracer.trace(args.goal)
            report.update(stage="source_traced", analyzed_feature_count=len(trace.features))
            store.derived("source-trace.json", trace.model_dump(mode="json"), call=client.calls[-1])
            store.derived(
                "source-catalog.json", [c.model_dump(mode="json") for c in trace.features]
            )
            notices = [
                {"path": r.path, "text": source.read(r.path)} for r in source.snapshot.license_refs
            ]
            for notice in notices:
                store.publish("notices/" + notice["path"], notice["text"].encode())
            for card in trace.features:
                store.derived(
                    f"trace-bundles/{card.feature_id}.json",
                    {
                        "source": source.snapshot.model_dump(mode="json"),
                        "feature": card.model_dump(mode="json"),
                        "excerpts": [source_evidence.validate_ref(r) for r in feature_refs(card)],
                        "notices": notices,
                        "coverage": trace.coverage.model_dump(mode="json"),
                        "python_evidence": [
                            i.model_dump(mode="json") for i in source_evidence.indexes.values()
                        ],
                        "qualification": "Bounded static trace; source tests were not executed.",
                    },
                )
            profiler = TargetProfiler(client, target_evidence, baseline)
            progress(
                "analysis", "Profiling the target architecture, capabilities, and observed gaps."
            )
            proposal = await profiler.propose(args.goal)
            reads = store.derived(
                "evidence-reads-target.json", read_log(target_evidence, profiler.events)
            )
            profile = profiler.finalize(proposal, store.adopt("baseline-binding.json"), reads)
            report["stage"] = "target_profiled"
            profile_call = client.calls[-1]
            store.derived("target-profile.json", profile.model_dump(mode="json"), call=profile_call)
            matcher = FitMatcher(client, source_evidence, target_evidence, trace, profile, baseline)
            progress(
                "selection", "Assessing fit; the controller will compute scores and policy gates."
            )
            batch = await matcher.assess(args.goal)
            report["stage"] = "assessed"
            # A clarification may add target reads; freeze the final read log/profile together.
            reads = store.derived(
                "evidence-reads-target.json",
                read_log(target_evidence, [*profiler.events, *matcher.events]),
            )
            profile = profiler.finalize(proposal, store.adopt("baseline-binding.json"), reads)
            matcher.profile = profile
            store.derived("target-profile.json", profile.model_dump(mode="json"), call=profile_call)
            store.derived(
                "matcher-assessment.json", batch.model_dump(mode="json"), call=client.calls[-1]
            )
            result, gates = matcher.select(batch)
            store.derived("gate-evidence.json", gates)
            store.derived("fit-result.json", result.model_dump(mode="json"))
            report.update(
                stage="policy_validated",
                selected_feature_id=result.selected_feature_id,
                eligible_feature_count=sum(c.eligible for c in result.candidates),
            )
        report["status"] = "selected" if result.selected_feature_id else "no_safe_fit"
    except (GraftError, OSError, ValueError, KeyError, RuntimeError, ValidationError) as exc:
        report.update(
            status="failed",
            error_code=exc.code if isinstance(exc, GraftError) else "selection_failed",
            detail=str(exc)
            if isinstance(exc, GraftError)
            else "Unable to prepare, analyze, or publish selection evidence.",
        )
    finally:
        report.update(
            calls=[c.model_dump(mode="json") for c in client.calls] if client else [],
            attempts=client.attempts if client else [],
            usage={
                "requests": budget.requests,
                "input_tokens": budget.input_tokens,
                "output_tokens": budget.output_tokens,
            },
            limits={
                "requests": budget.max_requests,
                "input_tokens": budget.max_input_tokens,
                "output_tokens": budget.max_output_tokens,
                "seconds": budget.max_seconds,
            },
        )
        try:
            report.update(
                await asyncio.to_thread(preserve, manager, source, target, original, worktree)
            )
            if baseline is not None:
                baseline.verify(target)
            if profiler is not None and profiler.proposal is not None:
                store.derived(
                    "target-profile-proposal.json",
                    {
                        "qualification": "Model proposal; validation is recorded separately.",
                        "proposal": profiler.proposal.model_dump(mode="json"),
                    },
                )
            for name, evidence, owner in (
                ("source", source_evidence, tracer),
                ("target", target_evidence, profiler),
            ):
                if evidence is not None:
                    # Keep the target read log frozen with its profile digest.
                    artifact_id = f"evidence-reads-{name}.json"
                    events = list(owner.events) if owner else []
                    if matcher is not None:
                        events += [
                            event
                            for event in matcher.events
                            if event["tool"].startswith(name + "_")
                        ]
                    if name == "target" and artifact_id in store.hashes:
                        artifact_id = "evidence-reads-target-final.json"
                    store.derived(artifact_id, read_log(evidence, events))
            store.verify()
            if (
                "controller_hashes" in report
                and file_hashes(PROJECT_ROOT, tuple(report["controller_hashes"]))
                != report["controller_hashes"]
            ):
                raise EvidenceError("Selection controller changed during the run.")
        except (GraftError, OSError, ValueError, KeyError):
            report.update(
                status="failed",
                error_code="invalid_evidence",
                detail="Final preservation or artifact integrity verification failed.",
            )
        # Only advertise files that still match their completed published digest.
        report["artifact_hashes"] = store.verified_hashes()
        store.publish("report.md", human_report(report, profile, result).encode())
        report["artifact_hashes"]["report.md"] = store.hashes["report.md"]
        report["unavailable_artifacts"] = [
            p for p in REQUIRED_OUTPUTS if p not in report["artifact_hashes"]
        ]
        report["stage_completed"] = report["stage"]
        store.json("report.json", report)
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=SCRAPY_URL)
    parser.add_argument("--source-sha", default=SCRAPY_SHA)
    parser.add_argument("--target", default="crawler-demo")
    parser.add_argument("--goal", default="Improve resilience to transient HTTP failures")
    parser.add_argument(
        "--image-receipt", type=Path, default=PROJECT_ROOT / "data" / "runner-image.json"
    )
    report, path = asyncio.run(record_fit_selection(SelectionInputs(**vars(parser.parse_args()))))
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(path),
                "analyzed_feature_count": report["analyzed_feature_count"],
                "selected_feature_id": report.get("selected_feature_id"),
                **(
                    {"error_code": report["error_code"], "detail": report["detail"]}
                    if "error_code" in report
                    else {}
                ),
            },
            indent=2,
        )
    )
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
