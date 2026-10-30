"""Machine-readable capability matrix. Advertised boundaries do not assert live certification."""

from pydantic import BaseModel

from app.runtime_profiles import PROFILE
from app.source_use import SOURCE_USE_PROFILES


class SupportMatrix(BaseModel):
    schema_version: str = "repository-support-v2"
    runtime_profile: str = PROFILE["id"]
    runtime_packages: dict[str, str] = PROFILE["runtime_packages"]
    native_adapters: list[str] = PROFILE["native_adapters"]
    acceptance_adapters: list[str] = PROFILE["acceptance_adapters"]
    source_use_profiles: dict[str, str] = SOURCE_USE_PROFILES
    ref_selection: list[str] = ["full_sha", "branch", "lightweight_tag", "annotated_tag"]
    private_preflight_enabled: bool = False
    private_analysis_enabled: bool = False
    private_release_gates: list[str] = [
        "live consent/access/revocation validation",
        "applicable organization policy validation",
    ]
    unsupported: list[str] = [
        "arbitrary dependencies or repository build/install scripts",
        "async fixtures, Trio and event-loop-sharing native tests",
        "streaming HTTP, real network and ASGI server acceptance",
        "new entry-point classes/modules, decorated methods/classes and inherited new APIs",
        "properties and dunder APIs",
        "non-JSON call arguments/results and unreviewed license variants",
        "private analysis, hosted execution and PR publishing",
    ]
