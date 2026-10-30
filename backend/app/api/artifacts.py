"""Manifest-only artifact access and a read-backed evidence index. No live repo reads."""

import hashlib
import json
import re
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from app.adaptation_contracts import AdaptationResult
from app.api.contracts import (
    Artifact,
    Problem,
    ReferenceDetail,
    RunDetail,
    RunSummary,
    TestSuite,
)
from app.api.storage import RunStore, now
from app.catalog_contracts import CatalogJob
from app.contracts import Ref, RepoSnapshot
from app.errors import GraftError
from app.evidence_contracts import Coverage, FeatureCard
from app.project_contracts import ProjectAnalysis
from app.relevance_contracts import RelevanceJob
from app.selection_contracts import AssessmentBatch, FitResult, TargetProfile
from app.tools.runner_artifacts import read_regular_file

PREFIXES = {
    "fit-select": "fit_select",
    "source-trace": "source_trace",
    "crawler-baseline": "local_baseline",
    "pinned-baseline": "pinned_baseline",
    "meta-smoke": "meta_smoke",
    "project-analysis": "project_analysis",
}
TITLES = {
    "fit_select": "Feature selection",
    "source_trace": "Source trace",
    "local_baseline": "Working-file baseline",
    "pinned_baseline": "Committed target baseline",
    "meta_smoke": "Meta connectivity check",
    "project_analysis": "Public project analysis",
    "catalog_discovery": "Source feature discovery",
    "relevance_assessment": "Feature relevance assessment",
    "verified_adaptation": "Verified adaptation",
}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def public_text(text: str) -> str:
    # Controller artifacts contain host paths in command vectors and original-target state.
    return re.sub(r"[A-Za-z]:[\\/][^\r\n\"']+", "[local path]", text)


def public_json(value):
    if isinstance(value, dict):
        return {
            key: public_json(item)
            for key, item in value.items()
            if key not in {"root", "managed_worktree", "command", "dirty_state_sha256"}
        }
    if isinstance(value, list):
        return [public_json(item) for item in value]
    return public_text(value) if isinstance(value, str) else value


def summary_from_report(run_id: str, kind: str, report: dict, *, historical=True) -> RunSummary:
    created = report.get("created_at") or now()
    return RunSummary(
        id=run_id,
        kind=kind,
        title=report.get("goal") or TITLES[kind],
        status="finished",
        stage="complete",
        outcome=report.get("status", "failed"),
        created_at=created,
        updated_at=created,
        historical=historical,
        analyzed_feature_count=report.get("analyzed_feature_count"),
        eligible_feature_count=report.get("eligible_feature_count"),
        error=Problem(
            code=report["error_code"], message=public_text(report.get("detail", "Run failed."))
        )
        if report.get("error_code")
        else None,
    )


def import_history(store: RunStore, root: Path) -> None:
    if not root.is_dir():
        return
    for directory in root.iterdir():
        match = re.fullmatch(
            r"(fit-select|source-trace|crawler-baseline|pinned-baseline|meta-smoke|project-analysis)-[a-f0-9]{32}",
            directory.name,
        )
        if not match or not directory.is_dir():
            continue
        try:
            raw = read_regular_file(directory, "report.json")
            report = json.loads(raw)
            summary = summary_from_report(directory.name, PREFIXES[match[1]], report)
            store.import_report(summary, directory.name + "/report.json", digest(raw))
        except (OSError, ValueError, GraftError, KeyError, TypeError):
            continue


class RunArtifacts:
    def __init__(self, root: Path, row):
        self.root = root
        self.row = row
        self.summary = RunSummary.model_validate_json(row["summary"])
        self.report = {}
        self.directory = None
        self.hashes = {}
        self.cache = {}
        self.warnings = []
        if not row["report"]:
            return
        try:
            raw = read_regular_file(root, row["report"])
            if digest(raw) != row["digest"]:
                raise ValueError("Changed receipt")
            self.report = json.loads(raw)
            self.directory = root / Path(row["report"]).parent
            self.hashes = dict(self.report.get("artifact_hashes", {}))
            self.hashes["report.json"] = row["digest"]
            binding = self.report.get("baseline_binding")
            if binding:
                self.hashes[binding["artifact_id"]] = binding["sha256"]
                bound = self.load(binding["artifact_id"]) or {}
                for capture in bound.get("captures", []):
                    suite = capture.get("suite")
                    if suite in {"isolation", "baseline", "acceptance"}:
                        name = f"test-captures/{suite}/capture.json"
                        if capture.get("artifact_id") == name:
                            self.hashes[name] = capture["sha256"]
            for capture in self.report.get("captures", []):
                suite = capture.get("suite")
                if suite not in {
                    "isolation",
                    "baseline",
                    "acceptance",
                    "project_baseline",
                    "project_unittest",
                }:
                    continue
                for name, sha in capture.get("artifact_hashes", {}).items():
                    self.hashes[f"test-captures/{suite}/{name}"] = sha
        except (OSError, ValueError, TypeError, KeyError, GraftError):
            self.report = {}
            self.hashes = {}
            self.warnings.append(
                "The recorded report is missing or changed. Its results cannot be displayed."
            )

    def raw(self, name: str) -> bytes:
        if name in self.cache:
            return self.cache[name]
        if self.directory is None or name not in self.hashes:
            raise HTTPException(404, "Artifact is not registered in this run.")
        # Only published reports, evidence, notices, inventories, and captures are exported.
        if not (
            ("/" not in name and name.endswith((".json", ".md")))
            or name.startswith(("trace-bundles/", "notices/", "test-captures/", "repositories/"))
            or re.fullmatch(r"assessments/[a-f0-9]{32}\.json", name)
            or re.fullmatch(r"rounds/round-[0-3](?:\.patch|-(?:proposal|mapping)\.json)", name)
            or name == "acceptance/test_feature.py"
        ) or any(part in {".git", ".env", "objects", "snapshot"} for part in Path(name).parts):
            raise HTTPException(404, "Artifact is not exportable.")
        try:
            raw = read_regular_file(self.directory, name)
            if digest(raw) != self.hashes[name]:
                raise ValueError
        except (OSError, ValueError, GraftError):
            raise HTTPException(
                409, "Artifact is missing, redirected, or has changed since publication."
            ) from None
        self.cache[name] = raw
        return raw

    def load(self, name: str):
        try:
            data = json.loads(self.raw(name))
            return data.get("data", data) if isinstance(data, dict) else data
        except (HTTPException, ValueError, TypeError):
            if name in self.hashes:
                message = f"{name} is unavailable or failed integrity validation."
                if message not in self.warnings:
                    self.warnings.append(message)
            return None

    def published(self, name: str) -> bytes:
        raw = self.raw(name)
        # Patch hashes bind the exact tested bytes; path redaction would corrupt the diff.
        if name.endswith(".patch") or name == "acceptance/test_feature.py":
            return raw
        if name.endswith(".json"):
            return json.dumps(public_json(json.loads(raw)), indent=2).encode()
        return public_text(raw.decode("utf-8", errors="replace")).encode()

    @staticmethod
    def artifact_id(name: str) -> str:
        return digest(name.encode())[:24]

    def name_for(self, artifact_id: str) -> str:
        for name in self.hashes:
            if self.artifact_id(name) == artifact_id:
                return name
        raise HTTPException(404, "Artifact not found.")

    def descriptor(self, name: str) -> Artifact:
        if name not in self.hashes:
            raise HTTPException(404, "Artifact is not registered in this run.")
        try:
            raw = self.published(name)
            size, sha, available = len(raw), digest(raw), True
        except (HTTPException, ValueError, UnicodeError):
            size, sha, available = 0, self.hashes[name], False
        return Artifact(
            id=self.artifact_id(name),
            label=name,
            kind="test capture"
            if name.startswith("test-captures/")
            else "source notice"
            if name.startswith("notices/")
            else "trace bundle"
            if "bundle" in name
            else "report"
            if name.startswith("report.")
            else "evidence",
            size=size,
            sha256=sha,
            available=available,
        )

    def descriptors(self) -> list[Artifact]:
        return [self.descriptor(name) for name in sorted(self.hashes)]

    def references(self) -> dict[str, ReferenceDetail]:
        result = {}
        windows = []
        for name in self.hashes:
            if "evidence-reads" in name and name.endswith(".json"):
                reads = self.load(name)
                if isinstance(reads, dict):
                    for window in reads.get("windows", []):
                        windows.append(
                            {
                                **window,
                                "repo_id": reads.get("repo_id", "source"),
                                "commit_sha": reads.get("commit_sha", reads.get("source_sha")),
                            }
                        )
        excerpts = []
        for name in self.hashes:
            if name.startswith("trace-bundles/") or name == "trace-bundle.json":
                bundle = self.load(name)
                if isinstance(bundle, dict):
                    excerpts.extend(bundle.get("excerpts", []))

        def visit(value, category="implementation"):
            if isinstance(value, list):
                for item in value:
                    visit(item, category)
            elif isinstance(value, dict):
                if {"repo_id", "commit_sha", "path", "start_line", "end_line"} <= value.keys():
                    try:
                        ref = Ref.model_validate(value)
                    except ValidationError:
                        return
                    ref_id = digest(ref.model_dump_json().encode())[:24]
                    if ref_id not in result:
                        text = None
                        for excerpt in excerpts:
                            if excerpt.get("ref") == ref.model_dump(mode="json") and digest(
                                excerpt["text"].encode()
                            ) == excerpt.get("sha256"):
                                text = excerpt["text"]
                                break
                        if text is None:
                            for window in windows:
                                if (
                                    window.get("repo_id") == ref.repo_id
                                    and window.get("commit_sha") == ref.commit_sha
                                    and window.get("path") == ref.path
                                    and window.get("start_line", 0) <= ref.start_line
                                    and window.get("end_line", 0) >= ref.end_line
                                    and isinstance(window.get("text"), str)
                                    and digest(window["text"].encode()) == window.get("sha256")
                                ):
                                    lines = window["text"].splitlines(keepends=True)
                                    text = "".join(
                                        lines[
                                            ref.start_line - window["start_line"] : ref.end_line
                                            - window["start_line"]
                                            + 1
                                        ]
                                    )
                                    break
                        result[ref_id] = ReferenceDetail(
                            id=ref_id,
                            ref=ref,
                            category=category.replace("_", " "),
                            excerpt_available=text is not None,
                            text=text,
                            sha256=digest(text.encode()) if text is not None else None,
                        )
                    return
                for key, item in value.items():
                    visit(
                        item,
                        key
                        if key
                        in {
                            "source_tests",
                            "license_refs",
                            "dependencies",
                            "integration_points",
                            "implementation",
                            "entry_points",
                        }
                        else category,
                    )

        for name in (
            "source-trace.json",
            "feature-card.json",
            "target-profile.json",
            "fit-result.json",
            "matcher-assessment.json",
            "source-project-analysis.json",
            "target-project-analysis.json",
            *(name for name in self.hashes if name.startswith("assessments/")),
        ):
            data = self.load(name)
            if data is not None:
                visit(data)
        return result

    def detail(self) -> RunDetail:
        values = self.summary.model_dump()
        report = self.report
        if report.get("adaptation"):
            try:
                values["adaptation"] = AdaptationResult.model_validate(report["adaptation"])
            except ValidationError:
                self.warnings.append("The adaptation result could not be validated.")
        values["catalog_id"] = report.get("catalog_id")
        values["catalog_revision"] = report.get("catalog_revision")
        values["relevance_assessment_id"] = report.get("assessment_id")
        if self.summary.kind == "relevance_assessment" and self.row["request"]:
            try:
                job = RelevanceJob.model_validate_json(self.row["request"])
                values["relevance_assessment_id"] = job.assessment_id
            except ValidationError:
                self.warnings.append("The relevance job binding could not be validated.")
        if self.summary.kind == "catalog_discovery" and self.row["request"]:
            try:
                job = CatalogJob.model_validate_json(self.row["request"])
                values.update(catalog_id=job.catalog_id, catalog_revision=job.revision)
            except ValidationError:
                self.warnings.append("The catalog job binding could not be validated.")
        values["project_analyses"] = []
        for role in ("source", "target"):
            data = self.load(f"{role}-project-analysis.json")
            if data is not None:
                try:
                    values["project_analyses"].append(ProjectAnalysis.model_validate(data))
                except ValidationError:
                    self.warnings.append(f"The {role} project analysis could not be validated.")
        self.warnings.extend(
            public_text(warning[:2000])
            for warning in report.get("warnings", [])[:20]
            if isinstance(warning, str)
        )
        values.update(
            goal=report.get("goal", ""),
            target_path_prefix=report.get("target_path_prefix"),
            original_target_unchanged=report.get("original_target_unchanged"),
            retry_diagnostic_status=report.get("retry_diagnostic_status"),
            usage=report.get("usage", {}),
            limits=report.get("limits", {}),
        )
        for key in ("source", "target"):
            if isinstance(report.get(key), dict):
                try:
                    values[key] = RepoSnapshot.model_validate(report[key])
                except ValidationError:
                    self.warnings.append(f"The {key} snapshot could not be validated.")
        for name, key, model in (
            ("target-profile.json", "profile", TargetProfile),
            ("fit-result.json", "fit", FitResult),
            ("matcher-assessment.json", "assessment", AssessmentBatch),
        ):
            data = self.load(name)
            if data is not None:
                try:
                    if key == "assessment":
                        # Older receipts are readable; an absent contract proves no readiness.
                        for item in data.get("assessments", []):
                            item.get("adaptation", {}).setdefault("behavior_contract", None)
                    values[key] = model.model_validate(data)
                except ValidationError:
                    self.warnings.append(f"{name} does not match its domain contract.")
        trace = self.load("source-trace.json")
        card = self.load("feature-card.json")
        if trace is None and card is not None:
            trace = {
                "features": [card],
                "coverage": (self.load("trace-bundle.json") or {}).get("coverage"),
            }
        if trace:
            try:
                values["features"] = [
                    FeatureCard.model_validate(item) for item in trace.get("features", [])
                ]
                if trace.get("coverage"):
                    values["coverage"] = Coverage.model_validate(trace["coverage"])
            except (ValidationError, TypeError):
                self.warnings.append("The source trace could not be fully validated.")
        captures = report.get("captures", [])
        baseline = self.load("baseline-report.json")
        if baseline:
            captures = baseline.get("captures", [])
        suites = []
        for capture in captures:
            try:
                suite = TestSuite.model_validate(
                    {
                        **public_json(capture),
                        "expected_diagnostic": (
                            capture.get("suite") == "acceptance"
                            and report.get("retry_diagnostic_status") == "expected_red"
                        )
                        or (
                            capture.get("suite") == "project_acceptance"
                            and capture.get("phase") == "before"
                            and capture.get("status") == "failed"
                            and report.get("eligible_feature_count") == 1
                        ),
                    }
                )
                suites.append(suite)
            except ValidationError:
                self.warnings.append("A test capture could not be validated.")
        # Older local fixture receipts predate the retry_diagnostic_status field.
        if self.summary.kind == "local_baseline" and report.get("status") == "passed":
            for suite in suites:
                suite.expected_diagnostic = suite.suite == "acceptance"
        values.update(
            tests=suites,
            artifacts=self.descriptors(),
            references=[
                item.model_dump(exclude={"text", "sha256"}) for item in self.references().values()
            ],
            calls=report.get("calls", []),
            warnings=self.warnings,
        )
        return RunDetail.model_validate(values)
