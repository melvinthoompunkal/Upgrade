"""Retrieve pinned repositories and record the first live, code-backed Meta retry trace."""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from app.config import Settings
from app.errors import GraftError
from app.models.meta_client import MetaClient
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


def write_json(path: Path, data: dict) -> str:
    content = json.dumps(data, indent=2, ensure_ascii=True).encode("utf-8")
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


async def record_source_trace(args: argparse.Namespace) -> tuple[dict, Path]:
    run_id = f"source-trace-{uuid4().hex}"
    directory = PROJECT_ROOT / "data" / "artifacts" / run_id
    directory.mkdir(parents=True)
    report_path = directory / "report.json"
    report = {
        "status": "failed",
        "check": "pinned_snapshots_and_meta_source_trace",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "Phase 3 source trace; feature adaptation and final green tests are later work.",
    }
    client, tracer, evidence = None, None, None
    try:
        report["controller_hashes"] = file_hashes(
            PROJECT_ROOT,
            tuple(
                p.relative_to(PROJECT_ROOT).as_posix()
                for p in sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
            ),
        )
        settings = Settings()
        # Check configuration before retrieval; no secrets enter Git or target processes.
        client = MetaClient(settings)
        async with client:
            manager = RepositoryManager(directory / "repositories")
            source = manager.public_source(args.source_url, args.source_sha)
            target, original = manager.local_target(registered_target(PROJECT_ROOT, args.target))
            worktree = manager.worktree(target, run_id)
            report.update(
                source=source.snapshot.model_dump(mode="json"),
                target=target.snapshot.model_dump(mode="json"),
                original_target=original,
                managed_worktree=str(worktree),
                target_path_prefix=target.prefix,
                branch=f"graft/{run_id}",
            )
            evidence = EvidenceSession(source)
            # Make physical path denial and missing inventory checks part of each live receipt.
            rejected = []
            for path in ("../outside", "/etc/passwd", "backend/.env", "missing-file.py"):
                try:
                    source.read(path)
                except (GraftError, ValueError):
                    rejected.append(path)
                else:
                    raise RuntimeError("Invalid repository read was accepted.")
            report["rejected_reads"] = rejected
            tracer = SourceTracer(
                client, evidence, output_tokens=settings.meta_trace_max_completion_tokens
            )
            trace = await tracer.trace(args.goal)
            source.verify()
            target.verify()
            original_unchanged = verify_original(manager.git, original)
            worktree_unchanged = all(
                hashlib.sha256((worktree / f.path).read_bytes()).hexdigest() == f.sha256
                for f in target.inventory.files
            )
            if not original_unchanged or not worktree_unchanged:
                raise RuntimeError("Original target or managed baseline changed during tracing.")
            if manager.git.run("status", "--porcelain", cwd=worktree):
                raise RuntimeError("Managed worktree must remain clean at its pinned base.")
            card = trace.features[0]
            refs = [
                *card.entry_points,
                *card.implementation,
                *card.source_tests,
                *card.license_refs,
                *(ref for dep in card.dependencies for ref in dep.refs),
            ]
            excerpts = [evidence.validate_ref(ref) for ref in refs]
            # Always carry complete upstream notices even when a model cites a shorter excerpt.
            notices = [
                {"path": ref.path, "text": source.read(ref.path)}
                for ref in source.snapshot.license_refs
            ]
            last_call = client.calls[-1]
            data = trace.model_dump(mode="json")
            data_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            envelope = {
                "schema_version": "1",
                "run_id": run_id,
                "artifact_id": "source-trace",
                "agent": "tracer",
                "model_id": last_call.model_id,
                "response_id": last_call.response_id,
                "created_at": report["created_at"],
                "content_sha256": data_hash,
                "data": data,
            }
            artifacts = {
                "source-trace.json": write_json(directory / "source-trace.json", envelope),
                "feature-card.json": write_json(
                    directory / "feature-card.json", card.model_dump(mode="json")
                ),
                "trace-bundle.json": write_json(
                    directory / "trace-bundle.json",
                    {
                        "source": source.snapshot.model_dump(mode="json"),
                        "feature": card.model_dump(mode="json"),
                        "excerpts": excerpts,
                        "notices": notices,
                        "python_evidence": [
                            i.model_dump(mode="json") for i in evidence.indexes.values()
                        ],
                        "coverage": trace.coverage.model_dump(mode="json"),
                        "qualification": (
                            "Static citation checks establish provenance, not semantic correctness "
                            "or executed source tests."
                        ),
                    },
                ),
            }
            for repo in (source, target):
                relative = "repositories/" + repo.snapshot.inventory_artifact_id
                artifacts[relative] = hashlib.sha256(
                    (directory / relative).read_bytes()
                ).hexdigest()
            report.update(
                status="passed",
                source_unchanged=True,
                original_target_unchanged=True,
                worktree_unchanged=True,
                artifact_hashes=artifacts,
                feature_id=card.feature_id,
                evidence_status=card.evidence_status,
                validated_references=len(refs),
                coverage={
                    "examined_files": len(trace.coverage.examined_paths),
                    "unexamined_files": len(trace.coverage.unexamined_areas),
                    "limits_reached": list(trace.coverage.limits_reached),
                },
            )
    except (GraftError, OSError, ValueError, RuntimeError, ValidationError) as exc:
        report["error_code"] = exc.code if isinstance(exc, GraftError) else "trace_failed"
        report["detail"] = (
            str(exc)
            if isinstance(exc, GraftError)
            else "Unable to prepare or validate source trace."
        )
    finally:
        if client is not None:
            report["calls"] = [c.model_dump(mode="json") for c in client.calls]
            report["attempts"] = client.attempts
            report["usage"] = {
                "requests": client.budget.requests,
                "input_tokens": client.budget.input_tokens,
                "output_tokens": client.budget.output_tokens,
            }
        if tracer is not None:
            report["tool_events"] = tracer.events
        if evidence is not None:
            read_log = {
                "source_sha": evidence.repo.snapshot.commit_sha,
                "read_bytes": evidence.read_bytes,
                "windows": evidence.windows,
            }
            read_hash = write_json(directory / "evidence-reads.json", read_log)
            report.setdefault("artifact_hashes", {})["evidence-reads.json"] = read_hash
        write_json(report_path, report)
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=SCRAPY_URL)
    parser.add_argument("--source-sha", default=SCRAPY_SHA)
    parser.add_argument("--target", default="crawler-demo")
    parser.add_argument(
        "--goal",
        default="Adapt bounded HTTP status retries into the existing HTTPX GET fetch boundary.",
    )
    args = parser.parse_args()
    report, path = asyncio.run(record_source_trace(args))
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(path),
                **({"detail": report["detail"]} if "detail" in report else {}),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
