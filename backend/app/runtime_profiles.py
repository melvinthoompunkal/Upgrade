"""One reviewed preparation manifest shared by inspection, receipts and build validation."""

import json
import re
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def runtime_profile():
    return json.loads((SANDBOX / "runtime-profile.json").read_text(encoding="utf-8"))


PROFILE = runtime_profile()
RUNTIME_PACKAGES = PROFILE["runtime_packages"]
TEST_PACKAGES = {**RUNTIME_PACKAGES, **PROFILE["test_packages"]}
RUNTIME_REQUIREMENTS = {f"{name}=={version}" for name, version in RUNTIME_PACKAGES.items()}


def validate_preparation():
    """A profile cannot advertise packages absent from the hash-locked build context."""
    locked = dict(
        re.findall(
            r"^([\w-]+)==([^\s]+)",
            (SANDBOX / "requirements.lock").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    if any(locked.get(name) != version for name, version in TEST_PACKAGES.items()):
        raise ValueError("Runtime profile and prepared dependency lock disagree.")
    return PROFILE
