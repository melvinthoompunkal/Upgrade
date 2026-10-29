"""One-fetch askpass channel. Tokens live in memory, never in argv, files or env."""

import json
import os
import secrets
import socketserver
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from app.errors import RepositoryError
from app.tools.runner_artifacts import physical_root

HELPER = """import json, os, socket, sys
prompt = sys.argv[1].lower()
if "username" in prompt:
    print("x-access-token")
elif "password" in prompt:
    with socket.create_connection(("127.0.0.1", int(os.environ["UPGRADE_BROKER_PORT"])), 5) as s:
        s.sendall((os.environ["UPGRADE_BROKER_NONCE"] + "\\n").encode())
        with s.makefile("rb") as f:
            print(json.loads(f.readline(65536))["token"])
else:
    sys.exit(1)
"""


@contextmanager
def git_credential_channel(root: Path, token: str):
    physical_root(root)
    nonce = secrets.token_urlsafe(32)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(2)
            try:
                supplied = self.rfile.readline(128).strip().decode("ascii")
                if secrets.compare_digest(supplied, nonce):
                    self.wfile.write(json.dumps({"token": token}).encode() + b"\n")
            except (OSError, UnicodeError):
                pass

    # Only loopback, an unguessable per-fetch nonce, and a short lifetime. Nothing is
    # placed in os.environ; only the owned Git child receives channel coordinates.
    with tempfile.TemporaryDirectory(prefix="credential-", dir=root) as temp:
        directory = Path(temp)
        helper = directory / "askpass.py"
        helper.write_text(HELPER, encoding="utf-8")
        launcher = directory / ("askpass.cmd" if os.name == "nt" else "askpass")
        if os.name == "nt":
            if any(c in str(sys.executable) + str(helper) for c in '\r\n%"'):
                raise RepositoryError("Unsupported credential helper path.")
            launcher.write_text(f'@echo off\n"{sys.executable}" "{helper}" %*\n')
        else:
            import shlex

            launcher.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper))} "$@"\n'
            )
            launcher.chmod(0o700)
        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield {
                    "GIT_ASKPASS": str(launcher),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "UPGRADE_BROKER_PORT": str(server.server_address[1]),
                    "UPGRADE_BROKER_NONCE": nonce,
                }
            finally:
                server.shutdown()
                thread.join(timeout=3)
