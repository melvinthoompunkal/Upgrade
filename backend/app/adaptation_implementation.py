"""Production-only implementation and repair under cumulative frozen scope limits."""

import asyncio

from app.adaptation_acceptance import acceptance_state
from app.adaptation_models import propose_patch
from app.adaptation_policy import LIMITS, sha256, validate_patch
from app.errors import EvidenceError
from app.selection_artifacts import prepare_directory


def production_packet(plan, current):
    packet = [
        {
            "path": p,
            "sha256": sha256(current[p]) if p in current else None,
            "text": current[p].decode() if p in current else None,
        }
        for p in plan.production_paths
    ]
    if sum(len((item["text"] or "").encode()) for item in packet) > 80_000:
        raise EvidenceError("Relevant target files exceed the bounded implementation packet.")
    return packet


def failure_packet(*captures):
    return [
        {
            "suite": c.suite,
            "status": c.status,
            "failures": [
                {
                    "name": case.name,
                    "outcome": case.outcome,
                    "message": case.message[:600],
                    "detail": case.detail[-1200:],
                }
                for case in c.cases
                if case.outcome != "passed"
            ][:8],
        }
        for c in captures
    ]


async def implement_and_verify(
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
    max_repairs,
    progress,
    red,
    baseline,
):
    artifacts, budget = runner.artifacts, runner.budget
    current = dict(original)
    native = baseline.suite
    failures = failure_packet(red)
    for round_index in range(1 + max_repairs):
        budget.check()
        result.repair_count = round_index
        progress(
            "repair" if round_index else "implementation",
            f"Preparing production {'repair' if round_index else 'implementation'}.",
        )
        proposal = await propose_patch(
            client,
            {
                **contract_packet,
                "target_files": production_packet(plan, current),
                "acceptance": spec.model_dump(mode="json"),
                "failures": failures,
                "round": round_index,
                "limits": LIMITS,
            },
            repair=bool(round_index),
        )
        label = f"round-{round_index}"
        artifacts.json(f"rounds/{label}-proposal.json", proposal.model_dump(mode="json"))
        entry = {"round": round_index, "status": "rejected", "verified": False}
        result.rounds.append(entry)
        try:
            candidate, patch, mapping, lines, added = validate_patch(
                proposal, plan, original, current, notice
            )
        except EvidenceError as error:
            entry["failure_code"] = error.code
            entry["reason"] = str(error)
            failures = [{"policy_rejection": str(error)}]
            continue
        current = candidate
        patch_id = f"rounds/{label}.patch"
        artifacts.publish(patch_id, patch)
        artifacts.json(f"rounds/{label}-mapping.json", mapping)
        result.patch_artifact, result.patch_sha256 = patch_id, sha256(patch)
        result.changed_files, result.changed_lines, result.new_files = (
            len(mapping),
            lines,
            added,
        )
        inputs = {p: sha256(raw) for p, raw in current.items()}
        patched = prepare_directory(artifacts.directory / label)
        for path, raw in original.items():
            destination = patched / path
            prepare_directory(destination.parent)
            destination.write_bytes(raw)
        # Apply the published diff to a fresh base, then test those exact resulting bytes.
        # An independent Git directory avoids discovering or modifying any parent checkout.
        git_dir = artifacts.directory / f"{label}-git"
        target.git.run("init", "--bare", "--template=", str(git_dir))
        target.git.run(
            "--git-dir",
            str(git_dir),
            "--work-tree",
            str(patched),
            "apply",
            "--whitespace=nowarn",
            "--",
            str(artifacts.directory / patch_id),
            cwd=patched,
        )
        runner.check(patched, inputs)
        progress("verification", "Running unchanged acceptance and native regression suites.")
        green = await asyncio.to_thread(runner.run, "project_acceptance", label, patched, inputs)
        regression = await asyncio.to_thread(runner.run, native, label, patched, inputs)
        if {c.name for c in regression.cases} != {c.name for c in baseline.cases}:
            raise EvidenceError("Regression case identities changed after implementation.")
        state = acceptance_state(green, spec, require_assertion_red=False)
        result.protected_tests_unchanged = True
        entry.update(
            status="tested",
            acceptance=state,
            regression=regression.status,
            patch_sha256=sha256(patch),
            changed_lines=lines,
        )
        runner.check(artifacts.directory / "original", runner.frozen["target_input_hashes"])
        source.verify()
        target.verify()
        if state == "green" and regression.status == "passed":
            result.verdict, entry["verified"] = "verified", True
            break
        failures = failure_packet(green, regression)
