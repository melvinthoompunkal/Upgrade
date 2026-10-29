"""Generic, code-backed project structure and optional native offline baseline workflow."""

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.api.storage import now
from app.contracts import Ref
from app.errors import EvidenceError, GraftError, UnsupportedTargetError
from app.evidence_contracts import Coverage
from app.project_adapters import python_facts
from app.project_contracts import ProjectAnalysis, ProjectAnalysisRequest, SymbolEvidence
from app.projects import ProjectStore
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, prepare_directory, read_log
from app.tools.evidence import EvidenceSession
from app.tools.runner import PROJECT_ROOT, prepared_image, run_suite
from app.tools.runner_artifacts import file_hashes

MAX_SYMBOLS = 80


def analyze_project(selected, repo, store: ArtifactStore) -> ProjectAnalysis:
    if selected.support.capability == "browse_index":
        raise UnsupportedTargetError("Project preflight does not support evidence analysis.")
    role = selected.requested.role
    evidence = EvidenceSession(repo)
    indexes, errors = python_facts(repo)
    if errors:
        raise UnsupportedTargetError("Project no longer passes Python analysis preflight.")
    definitions, tests, limits = [], [], []
    for notice in repo.snapshot.license_refs:
        try:
            for start in range(1, notice.end_line + 1, 200):
                evidence.read_range(notice.path, start, min(start + 199, notice.end_line))
        except EvidenceError:
            limits.append(f"{role}: notice read-window limit; complete notice exported separately")
    for path, index in indexes.items():
        is_test = path.startswith("tests/") or Path(path).name.startswith("test_")
        destination = tests if is_test else definitions
        for symbol in index.symbols:
            if symbol.kind not in {"class", "function", "async_function"}:
                continue
            if len(destination) >= MAX_SYMBOLS:
                limits.append(f"{role}: 80 {'test' if is_test else 'production'} definitions")
                break
            # A bounded definition head identifies the API without claiming full body coverage.
            end = min(symbol.end_line, symbol.start_line + 19)
            try:
                evidence.read_range(path, symbol.start_line, end)
            except EvidenceError:
                limits.append(f"{role}: evidence read-byte/window limit")
                continue
            ref = Ref(
                repo_id=repo.snapshot.repo_id,
                commit_sha=repo.snapshot.commit_sha,
                path=path,
                start_line=symbol.start_line,
                end_line=end,
                symbol=symbol.name,
            )
            evidence.validate_ref(ref)
            destination.append(
                SymbolEvidence(
                    name=symbol.name,
                    kind=symbol.kind,
                    ref=ref,
                    repository_path=f"{repo.prefix}/{path}" if repo.prefix else path,
                )
            )
    examined = sorted({window["path"] for window in evidence.windows})
    analysis = ProjectAnalysis(
        project=selected,
        entry_points=definitions,
        source_tests=tests,
        imports={path: list(index.imports) for path, index in indexes.items()},
        coverage=Coverage(
            examined_paths=examined,
            excluded_paths=repo.inventory.excluded_paths,
            unexamined_areas=sorted({f.path for f in repo.inventory.files} - set(examined)),
            limits_reached=[
                *repo.inventory.limits_reached,
                *sorted(set(limits)),
                "Definition heads only; unread function bodies remain unexamined.",
            ],
        ),
        limits_reached=sorted(set(limits)),
    )
    store.json(f"{role}-evidence-reads.json", read_log(evidence, []))
    store.json(f"{role}-project-analysis.json", analysis.model_dump(mode="json"))
    store.json(f"{role}-inventory.json", repo.inventory.model_dump(mode="json"))
    store.json(
        f"notices/{role}-notices.json",
        {
            "policy": selected.support.notice_policy,
            "adaptation_supported": False,
            "reason": selected.support.adaptation_reason,
            "notices": [
                {"path": ref.path, "text": repo.read(ref.path)}
                for ref in repo.snapshot.license_refs
            ],
        },
    )
    return analysis


def baseline(selected, repo, directory: Path, store: ArtifactStore) -> dict:
    if not selected.support.execution_supported:
        raise UnsupportedTargetError("No reviewed offline test adapter supports this target.")
    image = prepared_image(PROJECT_ROOT / "data/runner-image.json")
    paths = tuple(entry.path for entry in repo.inventory.files)
    exported = repo.export(paths, directory / "snapshot")
    acceptance = prepare_directory(directory / "acceptance")
    captures = []
    native_suite = (
        "project_unittest"
        if selected.support.adapter_version == "python-unittest-v1"
        else "project_baseline"
    )
    for suite in ("isolation", native_suite):
        capture = run_suite(
            suite=suite,
            image_id=image,
            snapshot=directory / "snapshot",
            acceptance=acceptance,
            directory=directory / "test-captures" / suite,
        )
        captures.append(asdict(capture))
        store.adopt(f"test-captures/{suite}/capture.json")
        for path in capture.artifact_hashes:
            store.adopt(f"test-captures/{suite}/{path}")
        if capture.status != "passed":
            break
    if file_hashes(directory / "snapshot", paths) != exported:
        raise EvidenceError("Exported target changed during the baseline.")
    repo.verify()
    binding = {
        "schema_version": "project-baseline-v1",
        "project_id": selected.id,
        "commit_sha": repo.snapshot.commit_sha,
        "project_root": repo.prefix,
        "inventory_sha256": selected.inventory_sha256,
        "manifest_sha256": selected.support.manifest_sha256,
        "adapter_version": selected.support.adapter_version,
        "image_id": image,
        "input_hashes": exported,
        "captures": [
            {
                "artifact_id": f"test-captures/{c['suite']}/capture.json",
                "sha256": store.hashes[f"test-captures/{c['suite']}/capture.json"],
            }
            for c in captures
        ],
    }
    store.json("project-baseline-binding.json", binding)
    return {
        "captures": captures,
        "status": "passed"
        if len(captures) == 2 and all(c["status"] == "passed" for c in captures)
        else "baseline_failed",
    }


def record_project_analysis(
    request: ProjectAnalysisRequest,
    projects: ProjectStore,
    owner: str,
    artifact_root: Path,
    progress=lambda *_: None,
) -> tuple[dict, Path]:
    directory = artifact_root / f"project-analysis-{uuid4().hex}"
    prepare_directory(directory)
    store = ArtifactStore(directory, {})
    report = {
        "status": "failed",
        "goal": request.goal,
        "created_at": now(),
        "usage": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        "scope": "Static project structure; optional original native tests. No feature matching.",
        "controller_hashes": file_hashes(
            PROJECT_ROOT,
            (
                "backend/app/project_analysis.py",
                "backend/app/project_adapters.py",
                "backend/app/project_support.py",
                "backend/app/project_contracts.py",
                "backend/app/projects.py",
                "backend/app/tools/runner.py",
                "backend/app/tools/runner_artifacts.py",
                "sandbox/unittest_runner.py",
            ),
        ),
    }
    try:
        projects.admit(owner, request)
        for role, project_id in (
            ("source", request.source_project),
            ("target", request.target_project),
        ):
            if project_id is None:
                continue
            progress(
                "analysis", f"Validating pinned {role} project and indexing Python definitions."
            )
            selected, repo = projects.open(owner, project_id)
            report[role] = repo.snapshot.model_dump(mode="json")
            analyze_project(selected, repo, store)
            repo.verify()
            if role == "target":
                report["target_path_prefix"] = repo.prefix
                if request.run_baseline:
                    progress("checks", "Running the target's native suite in the offline runner.")
                    report.update(baseline(selected, repo, directory, store))
        if report.get("status") != "baseline_failed":
            report["status"] = "passed"
    except (GraftError, OSError, ValueError, HTTPException) as error:
        report.update(
            status="failed",
            error_code=error.code if isinstance(error, GraftError) else "project_analysis_failed",
            detail=str(error)
            if isinstance(error, GraftError)
            else "Unable to inspect or test the pinned project.",
        )
    report["artifact_hashes"] = store.verified_hashes()
    publish_json_report(store, report)
    return report, directory / "report.json"
