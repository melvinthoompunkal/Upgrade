"""Explicit, budgeted relevance jobs. Browsing never enters this module."""

from app.api.storage import now
from app.catalog_store import CatalogStore, source_identity
from app.errors import EvidenceError, GraftError
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.project_adapters import python_facts
from app.relevance_analysis import assess_feature, target_context
from app.relevance_contracts import FeatureAssessment
from app.relevance_store import TERMINAL, assessment_identity
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, prepare_directory, read_log
from app.tools.evidence import EvidenceSession


async def record_relevance(
    job,
    assessments,
    projects,
    owner,
    artifact_root,
    settings,
    progress=lambda *_: None,
    *,
    client=None,
    access_epoch=None,
):
    view = assessments.get(owner, job.assessment_id)
    if view.state in TERMINAL:
        raise EvidenceError("This assessment is frozen. Automatic replay is forbidden.")
    directory = prepare_directory(artifact_root / f"relevance-assessment-{view.run_id}")
    artifacts = ArtifactStore(directory, {})
    budget = ModelBudget(**view.options.model_dump(exclude={"feature_limit"}))
    previous_usage = dict(view.cumulative_usage)
    owned_client = client is None
    sessions = {}
    report = {
        "status": "failed",
        "created_at": now(),
        "goal": view.goal,
        "catalog_id": view.catalog_id,
        "catalog_revision": view.catalog_revision,
        "assessment_id": view.id,
        "scope": view.qualification,
    }

    def checkpoint(feature=None):
        view.usage = {
            "requests": budget.requests,
            "input_tokens": budget.input_tokens,
            "output_tokens": budget.output_tokens,
        }
        view.cumulative_usage = {k: v + previous_usage.get(k, 0) for k, v in view.usage.items()}
        view.returned_model_ids = sorted({call.model_id for call in client.calls}) if client else []
        assessments.save(owner, view, feature, access_epoch=access_epoch)

    try:
        catalogs = CatalogStore(assessments.runs)
        catalog = catalogs.get(owner, view.catalog_id, view.catalog_revision)
        source_pin, source_repo = projects.open(owner, catalog.source_project)
        if (
            source_identity(source_pin, catalog.scope, catalog.identity["requested_model"])
            != catalog.identity
        ):
            raise EvidenceError("Source pin or catalog analyzer versions changed after discovery.")
        target_pin, target_repo = (
            projects.open(owner, view.target_project) if view.target_project else (None, None)
        )
        if (
            assessment_identity(catalog, target_pin, view.goal, settings.meta_model_id)
            != view.identity
        ):
            raise EvidenceError("Assessment inputs or policy versions changed after admission.")
        for role, pin, repo in (
            ("source", source_pin, source_repo),
            ("target", target_pin, target_repo),
        ):
            if repo is None:
                continue
            if pin.support.capability == "browse_index":
                raise EvidenceError("Project no longer supports Python evidence analysis.")
            indexes, errors = python_facts(repo)
            if errors:
                raise EvidenceError("Project failed current Python analysis preflight.")
            sessions[role] = EvidenceSession(repo, indexes=indexes)
            report[role] = repo.snapshot.model_dump(mode="json")
            artifacts.json(f"{role}-inventory.json", repo.inventory.model_dump(mode="json"))
            artifacts.json(
                f"notices/{role}-notices.json",
                {
                    "policy": pin.support.notice_policy,
                    "adaptation_supported": False,
                    "notices": [
                        {"path": r.path, "text": repo.read(r.path)}
                        for r in repo.snapshot.license_refs
                    ],
                },
            )
        if client is None:
            client = MetaClient(settings, budget=budget)
        else:
            client.budget = budget
        view.state = "running"
        checkpoint()
        target = sessions.get("target")
        context = target_context(target, view.goal)
        for feature_id in view.shortlist:
            try:
                budget.check()
                feature = catalogs.feature(owner, catalog.id, catalog.revision, feature_id)
                progress(
                    "selection", "Assessing a disclosed feature against the pinned goal and target."
                )
                result = await assess_feature(
                    client,
                    sessions["source"],
                    target,
                    feature,
                    view,
                    context,
                    execution_supported=bool(target_pin and target_pin.support.execution_supported),
                )
            except GraftError as error:
                result = FeatureAssessment(
                    feature_id=feature_id,
                    relevance_status="assessment_failed",
                    failure_code=error.code,
                    reason=(
                        f"Assessment failed: {str(error)[:450]} "
                        "No relevance or eligibility was inferred."
                    ),
                )
            checkpoint(result)
            try:
                artifacts.json(f"assessments/{feature_id}.json", result.model_dump(mode="json"))
            except (GraftError, OSError, ValueError, TypeError):
                report.setdefault("warnings", []).append(
                    "An assessment evidence sidecar could not be saved."
                )
            if budget.requests >= budget.max_requests or result.failure_code == "budget_exhausted":
                view.limit_reasons.append("model_budget")
                break
        for session in sessions.values():
            session.repo.verify()
        if view.counts.unassessed:
            view.limit_reasons.append("deep_assessment_shortlist")
        if view.counts.assessment_failed:
            view.limit_reasons.append("failed_feature_assessment")
        view.state = "partial" if view.limit_reasons else "complete"
        if not view.counts.deeply_assessed and view.counts.assessment_failed:
            view.state = "failed"
    except (GraftError, OSError, ValueError) as error:
        view.state = "failed"
        view.failure_code = error.code if isinstance(error, GraftError) else "assessment_failed"
        report.update(
            error_code=view.failure_code, detail="Assessment stopped; saved checkpoints remain."
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
            report.setdefault("warnings", []).append(
                f"The {role} evidence read log could not be saved."
            )
    checkpoint()
    report.update(
        status=view.state,
        relevance=view.model_dump(mode="json"),
        usage=view.usage,
        analyzed_feature_count=view.counts.deeply_assessed,
        eligible_feature_count=view.counts.eligible,
    )
    report["artifact_hashes"] = artifacts.verified_hashes()
    required = tuple(
        path
        for role in ("source", "target")
        if role == "source" or view.target_project
        for path in (
            f"{role}-inventory.json",
            f"{role}-evidence-reads.json",
            f"notices/{role}-notices.json",
        )
    )
    publish_json_report(artifacts, report, required)
    return report, directory / "report.json"
