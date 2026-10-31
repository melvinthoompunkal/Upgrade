"""Checkpointed catalog discovery. Each continuation owns a new budget and revision."""

from pathlib import Path

from app.api.storage import now
from app.catalog_discovery import (
    CatalogAnalyzer,
    discovery_paths,
    matching_candidate,
    merge_candidate,
)
from app.catalog_store import CATALOG_LIMIT, TERMINAL, source_identity
from app.errors import BudgetError, EvidenceError, GraftError
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.project_adapters import python_facts
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, prepare_directory, read_log
from app.tools.evidence import EvidenceSession


class DiscoveryRun:
    def __init__(self, catalogs, owner, view, analyzer, *, access_epoch=None):
        self.catalogs, self.owner, self.view = catalogs, owner, view
        self.analyzer = analyzer
        self.access_epoch = access_epoch
        self.features = {f.feature_id: f for f in catalogs.features(owner, view)}
        self.previous_usage = dict(view.cumulative_usage)
        self.new_count = 0
        self.removed_ids = set()

    def save(self):
        budget = self.analyzer.client.budget
        self.view.usage = {
            "requests": budget.requests,
            "input_tokens": budget.input_tokens,
            "output_tokens": budget.output_tokens,
        }
        self.view.cumulative_usage = {
            key: value + self.previous_usage.get(key, 0) for key, value in self.view.usage.items()
        }
        self.view.returned_model_ids = sorted({c.model_id for c in self.analyzer.client.calls})
        reads = {w["path"] for w in self.analyzer.evidence.windows}
        self.view.examined_paths = sorted(set(self.view.examined_paths) | reads)
        self.catalogs.save(
            self.owner,
            self.view,
            self.features.values(),
            removed_ids=self.removed_ids,
            access_epoch=self.access_epoch,
        )
        self.removed_ids.clear()

    def add(self, feature):
        for existing in self.features.values():
            if matching_candidate(existing, feature):
                self.features[existing.feature_id] = merge_candidate(existing, feature)
                return existing.feature_id
        if (
            self.new_count >= self.view.options.max_candidates
            or len(self.features) >= CATALOG_LIMIT
        ):
            self.view.limit_reasons.append(
                "catalog_entry_limit"
                if len(self.features) >= CATALOG_LIMIT
                else "job_candidate_limit"
            )
            raise BudgetError("Discovery candidate-record limit reached.")
        self.features[feature.feature_id] = feature
        self.new_count += 1
        return feature.feature_id

    async def discover(self, progress):
        paths = self.view.remaining_paths[: self.view.options.max_files]
        for offset in range(0, len(paths), 8):
            batch_paths = paths[offset : offset + 8]
            receipt = {
                "revision": self.view.revision,
                "stage": "discovery",
                "paths": batch_paths,
                "state": "running",
                "candidate_ids": [],
            }
            self.view.batches.append(receipt)
            progress("analysis", "Discovering capabilities from a bounded source packet.")
            try:
                hints = await self.analyzer.discover(batch_paths)
                if self.analyzer.truncated_paths:
                    self.view.limit_reasons.append("bounded_file_heads")
                    receipt["unread_file_bodies"] = self.analyzer.truncated_paths
                if self.analyzer.truncated_indexes:
                    self.view.limit_reasons.append("packet_symbol_limit")
                    receipt["truncated_symbol_indexes"] = self.analyzer.truncated_indexes
                identities, candidates = {}, []
                for hint in hints.candidates:
                    try:
                        feature = self.analyzer.candidate(hint, self.view.identity)
                        feature_id = self.add(feature)
                        identities.setdefault(hint.behavior_key, set()).add(feature_id)
                        candidates.append((hint, feature_id))
                    except EvidenceError as error:
                        receipt.setdefault("rejected_hints", []).append(error.code)
                        self.view.limit_reasons.append("invalid_hint_evidence")
                for hint, child in candidates:
                    parents = identities.get(hint.parent_key, set())
                    parent = next(iter(parents)) if len(parents) == 1 else None
                    if child and parent and child != parent:
                        # Reject cycles, including a two-way model-suggested relationship.
                        ancestor = parent
                        seen = {child}
                        while ancestor and ancestor not in seen:
                            seen.add(ancestor)
                            ancestor = self.features[ancestor].parent_feature_id
                        if ancestor is None:
                            self.features[child].parent_feature_id = parent
                receipt.update(
                    state="completed", candidate_ids=sorted({item[1] for item in candidates})
                )
                self.view.processed_paths = sorted(
                    set(self.view.processed_paths) | set(batch_paths)
                )
                self.view.remaining_paths = [
                    path for path in self.view.remaining_paths if path not in batch_paths
                ]
            except GraftError as error:
                receipt.update(state="failed", failure_code=error.code)
                self.view.failure_code = error.code
                self.save()
                # Leave this packet unprocessed; retry requires an explicit continuation.
                break
            self.save()
            if self.new_count >= self.view.options.max_candidates:
                self.view.limit_reasons.append("job_candidate_limit")
                break

    async def corroborate(self, requested_ids, progress):
        if budget_exhausted(self.analyzer.client.budget):
            self.view.limit_reasons.append("model_budget")
            return
        shortlist = (
            requested_ids
            or [
                f.feature_id
                for f in sorted(
                    self.features.values(),
                    key=lambda f: (
                        -any(r.path.endswith(".py") for r in f.discovery_refs),
                        f.feature_id,
                    ),
                )
                if f.trace_state != "validated"
            ][: self.view.options.trace_limit]
        )
        if len(shortlist) > self.view.options.trace_limit:
            raise EvidenceError("Requested traces exceed the declared trace budget.")
        for feature_id in shortlist:
            if feature_id not in self.features:
                continue  # An earlier trace in this job resolved this duplicate.
            feature = self.features[feature_id]
            receipt = {
                "revision": self.view.revision,
                "stage": "corroboration",
                "candidate_ids": [feature_id],
                "state": "running",
            }
            self.view.batches.append(receipt)
            progress("analysis", "Corroborating a capability against pinned code and source tests.")
            try:
                traced = await self.analyzer.trace(feature)
                self.accept_trace(traced)
                receipt["state"] = "completed"
            except GraftError as error:
                self.features[feature_id] = feature.model_copy(
                    update={
                        "trace_state": "failed",
                        "failure_code": error.code,
                        "evidence_status": "unverified",
                        "card": None,
                        "missing_evidence": [
                            "Code corroboration failed; this hint is unverified.",
                            str(error)[:600],
                        ],
                    }
                )
                receipt.update(state="failed", failure_code=error.code, detail=str(error)[:600])
            self.save()
            if budget_exhausted(self.analyzer.client.budget):
                self.view.limit_reasons.append("model_budget")
                break

    def accept_trace(self, traced):
        for existing in list(self.features.values()):
            if existing.feature_id == traced.feature_id or not matching_candidate(existing, traced):
                continue
            merged = merge_candidate(existing, traced)
            if existing.trace_state != "validated" and traced.card:
                merged = merged.model_copy(
                    update={
                        "card": traced.card.model_copy(
                            update={"feature_id": "f" + existing.feature_id}
                        ),
                        "evidence_status": traced.evidence_status,
                        "trace_state": "validated",
                        "missing_evidence": traced.missing_evidence,
                        "failure_code": None,
                    }
                )
            self.features[existing.feature_id] = merged
            del self.features[traced.feature_id]
            self.removed_ids.add(traced.feature_id)
            for feature in self.features.values():
                if feature.parent_feature_id == traced.feature_id:
                    feature.parent_feature_id = (
                        existing.feature_id if feature.feature_id != existing.feature_id else None
                    )
            return
        self.features[traced.feature_id] = traced


def budget_exhausted(budget) -> bool:
    try:
        budget.check()
        return budget.requests >= budget.max_requests
    except BudgetError:
        return True


async def record_catalog(
    job,
    catalogs,
    projects,
    owner,
    artifact_root: Path,
    settings,
    progress=lambda *_: None,
    *,
    client=None,
    access_epoch=None,
):
    view = catalogs.get(owner, job.catalog_id, job.revision)
    if view.state in TERMINAL:
        raise EvidenceError("This catalog revision is frozen. Automatic replay is forbidden.")
    directory = prepare_directory(artifact_root / f"catalog-discovery-{view.run_id}")
    artifacts = ArtifactStore(directory, {})
    report = {
        "status": "failed",
        "created_at": now(),
        "catalog_id": view.id,
        "catalog_revision": view.revision,
        "usage": {},
        "scope": "Source-only discovery; relevance, eligibility and adaptation are unassessed.",
    }
    budget = ModelBudget(
        **view.options.model_dump(
            include={
                "max_requests",
                "max_input_tokens",
                "max_output_tokens",
                "max_seconds",
            }
        )
    )
    owned_client = client is None
    evidence, run = None, None
    try:
        selected, repo = projects.open(owner, view.source_project)
        if selected.requested.role != "source" or selected.support.capability == "browse_index":
            raise EvidenceError("Source project no longer supports catalog analysis.")
        if source_identity(selected, view.scope, settings.meta_model_id) != view.identity:
            raise EvidenceError("Catalog inputs or analyzer versions changed after admission.")
        report["source"] = repo.snapshot.model_dump(mode="json")
        paths = discovery_paths(repo, view.scope)
        if not paths:
            raise EvidenceError("No supported discovery files exist in the declared scope.")
        view.remaining_paths = [path for path in paths if path not in view.processed_paths]
        view.excluded_paths = list(repo.inventory.excluded_paths)
        indexes, errors = python_facts(repo)
        if errors:
            raise EvidenceError("Source no longer passes bounded Python analysis preflight.")
        evidence = EvidenceSession(repo, indexes=indexes)
        if client is None:
            client = MetaClient(settings, budget=budget)
        else:
            client.budget = budget
        analyzer = CatalogAnalyzer(client, evidence)
        run = DiscoveryRun(catalogs, owner, view, analyzer, access_epoch=access_epoch)
        view.state = "running"
        run.save()
        artifacts.json("source-inventory.json", repo.inventory.model_dump(mode="json"))
        artifacts.json(
            "notices/source-notices.json",
            {
                "policy": selected.support.notice_policy,
                "adaptation_supported": False,
                "notices": [
                    {"path": ref.path, "text": repo.read(ref.path)}
                    for ref in repo.snapshot.license_refs
                ],
            },
        )
        await run.discover(progress)
        await run.corroborate(catalogs.trace_ids(owner, view), progress)
        repo.verify()
        if view.remaining_paths:
            view.limit_reasons.append("job_file_limit" if not view.failure_code else "failed_batch")
        if any(f.trace_state == "untraced" for f in run.features.values()):
            view.limit_reasons.append("deep_trace_shortlist")
        if any(f.trace_state == "failed" for f in run.features.values()):
            view.limit_reasons.append("failed_feature_trace")
        if len(run.features) >= CATALOG_LIMIT:
            view.limit_reasons.append("catalog_entry_limit")
        view.limit_reasons = sorted(set(view.limit_reasons) | set(repo.inventory.limits_reached))
        view.unexamined_areas = sorted(
            {f.path for f in repo.inventory.files} - set(view.examined_paths)
        )
        view.continuation_available = len(run.features) < CATALOG_LIMIT and bool(
            view.remaining_paths or any(f.trace_state != "validated" for f in run.features.values())
        )
        view.state = "partial" if view.limit_reasons else "complete_for_scope"
        if view.failure_code and not run.features and not view.processed_paths:
            view.state = "failed"
        run.save()
        report["status"] = view.state
    except (GraftError, OSError, ValueError) as error:
        view.state = "partial" if view.counts.discovered else "failed"
        view.failure_code = error.code if isinstance(error, GraftError) else "catalog_failed"
        view.continuation_available = view.counts.discovered < CATALOG_LIMIT
        report.update(
            error_code=view.failure_code, detail="Source discovery stopped; saved batches remain."
        )
        # Access loss must not publish any newly derived content.
        catalogs.save(owner, view, access_epoch=access_epoch)
    finally:
        if client is not None:
            report.update(
                calls=[call.model_dump(mode="json") for call in client.calls],
                attempts=client.attempts,
            )
            if owned_client:
                await client.__aexit__()
        if evidence:
            try:
                artifacts.json(
                    "source-evidence-reads.json",
                    read_log(evidence, run.analyzer.tools.events if run else []),
                )
            except (GraftError, OSError, ValueError, TypeError):
                report.setdefault("warnings", []).append(
                    "The evidence read log could not be saved."
                )
    report.update(
        catalog=view.model_dump(mode="json"),
        usage=view.usage,
        analyzed_feature_count=view.counts.deeply_traced,
    )
    try:
        artifacts.json("catalog-revision.json", view.model_dump(mode="json"))
    except (GraftError, OSError, ValueError, TypeError):
        report.setdefault("warnings", []).append("The optional catalog sidecar could not be saved.")
    report["artifact_hashes"] = artifacts.verified_hashes()
    publish_json_report(
        artifacts,
        report,
        (
            "source-inventory.json",
            "notices/source-notices.json",
            "source-evidence-reads.json",
        ),
    )
    return report, directory / "report.json"
