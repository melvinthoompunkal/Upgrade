"""Fixed pytest commands in a prepared, credential-free Docker image; no host test fallback."""

import json
import re
import subprocess
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread
from time import perf_counter
from uuid import uuid4

from app.jobs.containers import forget_container, register_container
from app.jobs.control import checkpoint
from app.jobs.processes import run_process
from app.tools.runner_artifacts import (
    MAX_ARTIFACT_BYTES,
    RunnerError,
    TestCapture,
    classify_result,
    file_hashes,
    parse_junit,
    read_regular_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SANDBOX = PROJECT_ROOT / "sandbox"
IMAGE_INPUTS = (
    "Dockerfile.python-demo",
    "requirements.lock",
    "pytest.ini",
    "test_isolation.py",
    "unittest_runner.py",
    ".dockerignore",
)
SUITES = {
    "isolation": "/opt/runner/test_isolation.py",
    "baseline": "/snapshot/tests/test_crawler.py",
    "acceptance": "/acceptance/test_retries.py",
    "project_baseline": "/snapshot/tests",
    "project_unittest": "/snapshot/tests",
}
CONTAINER_TIMEOUT_SECONDS = 60


def docker(*arguments: str) -> str:
    try:
        result = run_process(
            ["docker", *arguments],
            timeout=20,
            max_bytes=2 * 1024 * 1024,
            cancellable=arguments[0] not in {"rm", "kill", "inspect"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RunnerError("Docker Desktop is unavailable or did not respond in time.") from None
    if result.returncode:
        raise RunnerError("Docker operation failed. Check Docker Desktop and the prepared image.")
    return result.stdout.decode("utf-8", errors="replace").strip()


def prepared_image(receipt_path: Path) -> str:
    try:
        receipt = json.loads(read_regular_file(receipt_path.parent, receipt_path.name))
        image_id = receipt["image_id"]
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError
        if receipt["input_hashes"] != file_hashes(SANDBOX, IMAGE_INPUTS):
            raise RunnerError("Runner build inputs changed. Rebuild the prepared image.")
    except (OSError, KeyError, ValueError, TypeError):
        raise RunnerError("Build the runner first with scripts/build_runner.py.") from None
    actual_id = docker("image", "inspect", "--format", "{{.Id}}", image_id)
    if actual_id != image_id:
        raise RunnerError("Prepared runner image identity did not match its build receipt.")
    return image_id


def _stop_container(name: str) -> None:
    """Handle both already-exited containers and startup racing the first kill request."""
    for _ in range(2):
        try:
            docker("kill", name)
            return
        except RunnerError:
            if docker("inspect", "--format", "{{.State.Running}}", name) == "false":
                return
    raise RunnerError("Could not stop the owned runner container.")


def suite_command(suite: str, image_id: str) -> tuple[str, ...]:
    if suite == "project_unittest":
        return "--entrypoint", "python", image_id, "/opt/runner/unittest_runner.py"
    native = suite == "project_baseline"
    return (
        image_id,
        "-c",
        "/snapshot/pyproject.toml" if native else "/opt/runner/pytest.ini",
        *(() if native else ("--noconftest",)),
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_timeout",
        "--timeout=10",
        "--timeout-method=signal",
        "--timeout-disable-debugger-detection",
        "--junitxml=/results/junit.xml",
        "--basetemp=/tmp/pytest",
        "-q",
        SUITES[suite],
    )


def container_command(
    *, name: str, suite: str, image_id: str, snapshot: Path, acceptance: Path, results: Path
) -> tuple[str, ...]:
    if suite not in SUITES:
        raise RunnerError("Unknown registered test suite.")
    mounts: list[str] = []
    for source, target, readonly in [
        (snapshot, "/snapshot", True),
        (acceptance, "/acceptance", True),
        (results, "/results", False),
    ]:
        resolved = str(source.resolve(strict=True))
        if "," in resolved or "\n" in resolved:
            raise RunnerError("Docker mount paths must not contain commas or newlines.")
        mounts.extend(
            [
                "--mount",
                f"type=bind,source={resolved},target={target}" + (",readonly" if readonly else ""),
            ]
        )
    project_suite = suite in {"project_baseline", "project_unittest"}
    return (
        "docker",
        "create",
        "--name",
        name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--cpus=2",
        "--memory=1g",
        "--memory-swap=1g",
        "--pids-limit=64",
        "--ulimit",
        "fsize=8388608:8388608",
        "--log-driver=none",
        "--init",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "--env",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "--env",
        "PYTHONPATH=/snapshot/src:/snapshot" if project_suite else "PYTHONPATH=/snapshot/src",
        *mounts,
        *suite_command(suite, image_id),
    )


def _attach(name: str, directory: Path, timeout: float) -> tuple[int, str]:
    # Establish log ownership before Docker can start. A failed open must never race startup.
    filename = "stdout.txt"
    try:
        with ExitStack() as stack:
            outputs = {}
            for filename in ("stdout.txt", "stderr.txt"):
                try:
                    outputs[filename] = stack.enter_context((directory / filename).open("wb"))
                except OSError:
                    return -1, f"Failed to capture {filename}: log open I/O error."
            return _capture_streams(name, timeout, outputs)
    except OSError:
        return -1, "Failed to finalize stdout/stderr: log close I/O error."


def _capture_streams(name: str, timeout: float, outputs: dict) -> tuple[int, str]:
    """Bound both captured streams while draining pipes; terminate the container on timeout."""
    overflow = Event()
    failed = Event()
    errors: SimpleQueue[str] = SimpleQueue()
    with subprocess.Popen(
        ["docker", "start", "--attach", name], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:

        def drain(stream, filename: str) -> None:
            written = 0
            try:
                output = outputs[filename]
                while chunk := stream.read(8192):
                    remaining = max(0, MAX_ARTIFACT_BYTES - written)
                    output.write(chunk[:remaining])
                    written += len(chunk)
                    if written > MAX_ARTIFACT_BYTES:
                        overflow.set()
                        break
                output.flush()
            except Exception:
                # Raw I/O exceptions may contain host paths; retain only the stream identity.
                errors.put(f"Failed to capture {filename}: stream read/write I/O error.")
                failed.set()

        threads = [
            Thread(target=drain, args=(stream, filename), daemon=True)
            for stream, filename in [(process.stdout, "stdout.txt"), (process.stderr, "stderr.txt")]
        ]
        for thread in threads:
            thread.start()
        diagnostic = ""
        try:
            deadline = perf_counter() + timeout
            while True:
                checkpoint()
                if overflow.is_set() or failed.is_set():
                    _stop_container(name)
                    process.kill()
                    process.wait(timeout=10)
                    break
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                try:
                    process.wait(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired:
            diagnostic = "Container exceeded the wall-clock timeout."
            _stop_container(name)
            process.kill()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                _stop_container(name)
                process.kill()
                process.wait(timeout=10)
            if overflow.is_set() or failed.is_set():
                _stop_container(name)
            for thread in threads:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in threads):
                diagnostic = "Stream capture did not complete within its drain timeout."
            if not errors.empty():
                diagnostic = errors.get()
        if overflow.is_set():
            diagnostic = "Container output exceeded the capture limit."
        return process.returncode, diagnostic


def run_suite(
    *,
    suite: str,
    image_id: str,
    snapshot: Path,
    acceptance: Path,
    directory: Path,
    timeout: float = CONTAINER_TIMEOUT_SECONDS,
) -> TestCapture:
    directory.mkdir(parents=True, exist_ok=False)
    results = directory / "results"
    results.mkdir(mode=0o777)
    results.chmod(0o777)
    name = f"graft-test-{uuid4().hex}"
    checkpoint()
    command = container_command(
        name=name,
        suite=suite,
        image_id=image_id,
        snapshot=snapshot,
        acceptance=acceptance,
        results=results,
    )
    resource = register_container(name)
    started = perf_counter()
    cases = ()
    status = "error"
    exit_code = -1
    diagnostic = ""
    artifacts = {}
    try:
        docker(*command[1:])
        exit_code, diagnostic = _attach(name, directory, timeout)
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        exit_code = state["ExitCode"]
        if diagnostic:
            status = "timeout" if "wall-clock" in diagnostic else "error"
        elif state.get("OOMKilled") or state.get("Error"):
            diagnostic = "Container ran out of memory or failed to execute."
        else:
            cases = parse_junit(
                read_regular_file(results, "junit.xml"), qualified_names=suite == "project_baseline"
            )
            status = classify_result(exit_code, cases)
    except (RunnerError, OSError, ValueError, KeyError) as error:
        diagnostic = str(error) if isinstance(error, RunnerError) else "Invalid runner capture."
        status = "error"
    finally:
        # This generated name belongs exclusively to this invocation, including failed starts.
        try:
            docker("rm", "--force", name)
        except RunnerError:
            # A canceled/failed create may never have produced a container. Confirm absence
            # using only this generated exact name; an unavailable daemon remains a failure.
            remaining = docker("ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
            if remaining:
                raise RunnerError(
                    f"Could not remove runner container {name}; remove it manually."
                ) from None
        forget_container(resource)
    # Keep bounded logs useful even when parsing the test report failed.
    for path in ("stdout.txt", "stderr.txt", "results/junit.xml"):
        if (directory / path).exists():
            try:
                artifacts.update(file_hashes(directory, (path,)))
            except (RunnerError, OSError, ValueError):
                status = "error"
                diagnostic = diagnostic or "Runner artifact is invalid or exceeds the size limit."
    if not {"stdout.txt", "stderr.txt"} <= artifacts.keys():
        status = "error"
        diagnostic = diagnostic or "Runner capture is incomplete: missing stdout/stderr evidence."
    capture = TestCapture(
        suite,
        status,
        exit_code,
        round((perf_counter() - started) * 1000),
        command,
        image_id,
        cases,
        artifacts,
        diagnostic,
    )
    (directory / "capture.json").write_text(json.dumps(asdict(capture), indent=2), encoding="utf-8")
    return capture
