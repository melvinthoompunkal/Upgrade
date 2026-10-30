"""F7: one locked feature, frozen red tests, cumulative scope and at most three repairs."""

import asyncio
from uuid import uuid4

from fastapi import HTTPException

from app.adaptation_acceptance import acceptance_state, render_acceptance, validate_acceptance
from app.adaptation_contracts import AdaptationResult
from app.adaptation_implementation import implement_and_verify, production_packet
from app.adaptation_inputs import adaptation_inputs
from app.adaptation_models import plan_adaptation, prepare_acceptance
from app.adaptation_policy import (
    LIMITS,
    read_ref,
    sha256,
    source_notices,
    validate_plan,
)
from app.adaptation_runner import AdaptationRunner
from app.api.storage import now
from app.catalog_store import source_identity
from app.errors import EvidenceError, GraftError
from app.jobs.control import ExecutionStopped
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.relevance_store import assessment_identity
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, feature_refs, prepare_directory, read_log
from app.source_use import SOURCE_USE_PROFILES
from app.tools.evidence import EvidenceSession
from app.tools.runner import IMAGE_INPUTS, PROJECT_ROOT, prepared_image
from app.tools.runner_artifacts import file_hashes, read_regular_file

CONTROLLERS = (
    *(
        f"backend/app/{name}.py"
        for name in (
            "adaptation_contracts",
            "invocation_contracts",
            "invocation_runtime",
            "runtime_profiles",
            "source_use",
            "adaptation_acceptance",
            "adaptation_policy",
            "adaptation_models",
            "adaptation_inputs",
            "adaptation_implementation",
            "adaptation_runner",
            "adaptation_workflow",
            "capture_metadata",
            "project_adapters",
            "project_support",
            "tools/runner",
            "tools/runner_artifacts",
        )
    ),
    *(f"sandbox/{path}" for path in IMAGE_INPUTS),
)


async def record_adaptation(
    request,
    projects,
    owner,
    artifact_root,
    settings,
    progress=lambda *_: None,
    *,
    client=None,
    image_receipt=None,
):
    directory = prepare_directory(artifact_root / f"verified-adaptation-{uuid4().hex}")
    artifacts = ArtifactStore(directory, {})
    result = AdaptationResult(feature_id=request.feature_id)
    budget = ModelBudget(**request.options.model_dump(exclude={"max_repairs"}))
    report = {
        "status": "failed",
        "created_at": now(),
        "assessment_id": request.assessment_id,
        "analyzed_feature_count": 1,
        "eligible_feature_count": 0,
        "limits": {**LIMITS, **request.options.model_dump()},
        "scope": "Reviewed Python acceptance and source-use profiles; local offline runner",
    }
    sessions, runner, owned_client = {}, None, client is None
    try:
        view, catalog, feature, source_pin, target_pin = adaptation_inputs(
            request, projects, owner, settings.meta_model_id
        )
        report.update(goal=view.goal, catalog_id=catalog.id, catalog_revision=catalog.revision)
        source_pin, source = projects.open(owner, source_pin.id)
        target_pin, target = projects.open(owner, target_pin.id)
        if source_identity(
            source_pin, catalog.scope, settings.meta_model_id
        ) != catalog.identity or (
            assessment_identity(catalog, target_pin, view.goal, settings.meta_model_id)
            != view.identity
        ):
            raise EvidenceError("Current pinned project profiles differ from the saved assessment.")
        if not target_pin.support.execution_supported:
            raise EvidenceError("Target no longer supports reviewed native execution.")
        for role, repo in (("source", source), ("target", target)):
            sessions[role] = EvidenceSession(repo)
            report[role] = repo.snapshot.model_dump(mode="json")
            artifacts.json(f"{role}-inventory.json", repo.inventory.model_dump(mode="json"))
        report["target_path_prefix"] = target.prefix
        notice = source_notices(source, feature.card)
        artifacts.publish("notices/source-NOTICE.txt", notice.encode())
        artifacts.json(
            "notices/target-notices.json",
            [
                {"path": ref.path, "text": target.read(ref.path)}
                for ref in target.snapshot.license_refs
            ],
        )
        artifacts.json("feature-card.json", feature.card.model_dump(mode="json"))
        source_evidence = [
            read_ref(sessions["source"], ref) for ref in dict.fromkeys(feature_refs(feature.card))
        ]
        if sum(len(item["text"].encode()) for item in source_evidence) > 60_000:
            raise EvidenceError("Selected source evidence exceeds the implementation packet bound.")
        identity = {
            "feature_id": feature.feature_id,
            "assessment_id": view.id,
            "catalog_id": catalog.id,
            "catalog_revision": catalog.revision,
            "source": catalog.identity,
            "target": view.identity["target"],
            "goal": view.goal,
            "execution_profile": target_pin.support.adapter_version,
            "acceptance_profile": "python-reviewed-acceptance-v2",
            "source_use_policy": SOURCE_USE_PROFILES[feature.card.license_spdx],
        }
        artifacts.json("adaptation-identity.json", identity)
        artifacts.binding = identity
        if client is None:
            client = MetaClient(settings, budget=budget)
        else:
            client.budget = budget
        progress("planning", "Planning the selected feature against pinned integration code.")
        packet = {
            "identity": identity,
            "feature": feature.card.model_dump(mode="json"),
            "source_evidence": source_evidence,
            "target_paths": [f.path for f in target.inventory.files],
        }
        plan = await plan_adaptation(client, sessions["target"], packet)
        validate_plan(plan, sessions["source"], sessions["target"])
        artifacts.json("behavior-contract.json", plan.model_dump(mode="json"))
        # Provide relevant existing code to the preparer; no implementation proposal exists yet.
        original = {f.path: target.read(f.path).encode() for f in target.inventory.files}
        current = dict(original)
        contract_packet = {
            "identity": identity,
            "contract": plan.model_dump(mode="json"),
            "source_evidence": source_evidence,
            "target_files": production_packet(plan, current),
        }
        progress("acceptance", "Preparing and freezing independent feature acceptance.")
        spec = await prepare_acceptance(client, contract_packet)
        validate_acceptance(spec, plan, target)
        report["scope"] = f"{spec.schema_version}; {identity['source_use_policy']}; offline runner"
        artifacts.json("acceptance-spec.json", spec.model_dump(mode="json"))
        artifacts.publish("acceptance/test_feature.py", render_acceptance(spec))
        image_receipt = image_receipt or PROJECT_ROOT / "data/runner-image.json"
        image = await asyncio.to_thread(prepared_image, image_receipt)
        controllers = file_hashes(PROJECT_ROOT, CONTROLLERS)
        frozen = {
            "schema_version": "adaptation-freeze-v1",
            "identity": identity,
            "acceptance_profile": spec.schema_version,
            "plan_profile": plan.schema_version,
            "contract_sha256": artifacts.hashes["behavior-contract.json"],
            "spec_sha256": artifacts.hashes["acceptance-spec.json"],
            "notice_sha256": artifacts.hashes["notices/source-NOTICE.txt"],
            "acceptance_hashes": {
                "test_feature.py": artifacts.hashes["acceptance/test_feature.py"]
            },
            "target_input_hashes": {p: sha256(raw) for p, raw in original.items()},
            "image_id": image,
            "image_receipt_sha256": sha256(
                read_regular_file(image_receipt.parent, image_receipt.name)
            ),
            "controller_hashes": controllers,
        }
        artifacts.json("frozen-binding.json", frozen)
        runner = AdaptationRunner(artifacts, image, budget, frozen, controllers, image_receipt)
        snapshot = directory / "original"
        exported = target.export(tuple(original), snapshot)
        if exported != frozen["target_input_hashes"]:
            raise EvidenceError("Original export does not match the pinned target inputs.")
        native = (
            "project_unittest"
            if target_pin.support.adapter_version == ("python-unittest-v1")
            else "project_baseline"
        )
        progress("checks", "Checking isolation, ordinary baseline and frozen assertion red.")
        await asyncio.to_thread(runner.run, "isolation", "before", snapshot, exported)
        baseline = await asyncio.to_thread(runner.run, native, "before", snapshot, exported)
        if baseline.status != "passed":
            report["status"] = "baseline_failed"
        else:
            red = await asyncio.to_thread(
                runner.run, "project_acceptance", "before", snapshot, exported
            )
            if acceptance_state(red, spec) == "green":
                result.verdict, report["status"] = "already_present", "already_present"
                result.protected_tests_unchanged = True
            else:
                report["eligible_feature_count"] = 1
                artifacts.json(
                    "adaptation-gates.json",
                    {
                        "eligible": True,
                        "identity": identity,
                        "target_binding": view.identity["target"],
                        "passing_baseline": True,
                        "frozen_assertion_red": True,
                        "source_use": identity["source_use_policy"],
                        "approved_scope": list(plan.production_paths),
                        "new_runtime_dependencies": [],
                    },
                )
                await implement_and_verify(
                    client,
                    runner,
                    plan,
                    spec,
                    original,
                    contract_packet,
                    notice,
                    source,
                    target,
                    result,
                    request.options.max_repairs,
                    progress,
                    red,
                    baseline,
                )
                report["status"] = "verified" if result.verdict == "verified" else "exhausted"
        runner.check(snapshot, exported)
        source.verify()
        target.verify()
        report["original_target_unchanged"] = True
    except ExecutionStopped:
        raise
    except (GraftError, OSError, ValueError, HTTPException) as error:
        result.verdict = "unverified"
        result.protected_tests_unchanged = False
        for entry in result.rounds:
            entry["verified"] = False
        report.update(
            status="failed",
            error_code=error.code if isinstance(error, GraftError) else "adaptation_failed",
            detail=str(error)
            if isinstance(error, GraftError)
            else "Adaptation stopped; available evidence remains unverified.",
        )
    finally:
        if client:
            report.update(
                calls=[c.model_dump(mode="json") for c in client.calls], attempts=client.attempts
            )
            if owned_client:
                await client.__aexit__()
    for role, session in sessions.items():
        try:
            artifacts.json(f"{role}-evidence-reads.json", read_log(session, []))
        except (GraftError, OSError, ValueError, TypeError):
            report.setdefault("warnings", []).append(f"The {role} read log could not be saved.")
            result.verdict = "unverified"
            report["status"] = "failed"
    report.update(
        adaptation=result.model_dump(mode="json"),
        captures=runner.captures if runner else [],
        usage={
            "requests": budget.requests,
            "input_tokens": budget.input_tokens,
            "output_tokens": budget.output_tokens,
        },
    )
    artifacts.json("adaptation-result.json", result.model_dump(mode="json"))
    publish_json_report(artifacts, report)
    return report, directory / "report.json"
