"""Trusted runner checks, baked into the image; never imported by host pytest."""

import os
from pathlib import Path

import pytest


def test_identity_capabilities_and_privilege_escalation() -> None:
    assert os.getuid() == 65532
    status = Path("/proc/self/status").read_text()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    assert int(fields["CapEff"].strip(), 16) == 0
    assert fields["NoNewPrivs"].strip() == "1"


def test_only_loopback_network_exists() -> None:
    assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}


@pytest.mark.parametrize(
    "path",
    ["/opt/runner/write-probe", "/snapshot/write-probe", "/acceptance/write-probe"],
)
def test_inputs_and_root_filesystem_are_read_only(path: str) -> None:
    with pytest.raises(OSError):
        Path(path).write_text("must fail")


def test_host_credentials_git_and_socket_are_absent() -> None:
    assert not {"MODEL_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY"} & os.environ.keys()
    for path in [
        "/snapshot/.git",
        "/snapshot/.env",
        "/root/.aws",
        "/root/.ssh",
        "/var/run/docker.sock",
        "/source",
        "/host",
    ]:
        # stat can itself be forbidden for /root; either outcome means it is inaccessible.
        try:
            assert not Path(path).exists()
        except PermissionError:
            pass
