"""Fixed Git argument vectors, independent storage, and a credential-free Git environment."""

import os
import subprocess
from pathlib import Path

from app.errors import RepositoryError


class Git:
    def __init__(self, control: Path):
        control.mkdir(parents=True, exist_ok=True)
        self.hooks = control / "empty-hooks"
        self.hooks.mkdir(exist_ok=True)
        self.env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "COMSPEC",
            }
        }
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_LFS_SKIP_SMUDGE="1",
            GIT_OPTIONAL_LOCKS="0",
            GIT_AUTHOR_NAME="Graft",
            GIT_COMMITTER_NAME="Graft",
            GIT_AUTHOR_EMAIL="graft@localhost",
            GIT_COMMITTER_EMAIL="graft@localhost",
        )

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        input: bytes | None = None,
        max_bytes: int = 8 * 1024 * 1024,
        timeout: float = 120,
    ) -> bytes:
        command = [
            "git",
            "--no-pager",
            "--literal-pathspecs",
            "-c",
            f"core.hooksPath={self.hooks}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "submodule.recurse=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "protocol.file.allow=always",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.symlinks=false",
            *args,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=self.env,
                input=input,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RepositoryError("Git operation unavailable or exceeded its timeout.") from None
        if result.returncode or len(result.stdout) > max_bytes:
            raise RepositoryError("Git operation failed or exceeded its output limit.")
        return result.stdout
