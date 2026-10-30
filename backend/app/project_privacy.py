"""F11 release fence. A consent record alone never enables private analysis."""

from fastapi import HTTPException


def require_project_analysis(selected):
    if selected.private or selected.repository_snapshot:
        raise HTTPException(
            409,
            "Private snapshot analysis is disabled pending live consent/access and "
            "organization-policy validation. Disclosure or consent does not enable it.",
        )
