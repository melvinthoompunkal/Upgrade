"""Bounded subprocess I/O and cancellation of the owned process group."""

import os
import signal
import subprocess
import tempfile
from time import monotonic

from app.jobs.control import checkpoint


def terminate(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.kill()
    process.wait(timeout=10)


def run_process(command, *, timeout, max_bytes, cwd=None, env=None, input=None, cancellable=True):
    """Output is spooled, bounded during execution, and never included in errors."""
    if cancellable:
        checkpoint()
    with (
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
        tempfile.TemporaryFile() as stdin,
    ):
        if input:
            stdin.write(input)
            stdin.seek(0)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=os.name != "nt",
        )
        try:
            deadline = monotonic() + timeout
            while True:
                if cancellable:
                    checkpoint()
                if any(os.fstat(f.fileno()).st_size > max_bytes for f in (stdout, stderr)):
                    raise OSError("Subprocess output limit exceeded.")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    process.wait(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            stdout.seek(0)
            content = stdout.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise OSError("Subprocess output limit exceeded.")
            return subprocess.CompletedProcess(command, process.returncode, content)
        finally:
            terminate(process)
