"""User-authorized installation state and an untrusted setup callback."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.auth.repositories import require_connection
from app.auth.snapshots import access_error
from app.errors import GraftError

router = APIRouter(prefix="/api/github")


class InstallationView(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: int = Field(gt=0)
    account: dict[str, str]
    repository_selection: str
    suspended: bool
    contents_readable: bool


class InstallationPage(BaseModel):
    items: list[InstallationView]
    next_page: int | None


def page(request, owner: str, number: int) -> InstallationPage:
    state = request.app.state
    token = state.auth.access_token(owner, state.github)
    body = state.github.request(
        "GET", f"/user/installations?per_page=100&page={number}", token=token
    )
    values = body.get("installations")
    if not isinstance(values, list) or len(values) > 100:
        raise HTTPException(409, "GitHub returned an invalid installation page.")
    try:
        items = []
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("permissions"), dict):
                raise ValueError
            if state.settings.github_app_id and item.get("app_id") != state.settings.github_app_id:
                continue
            login = item["account"]["login"]
            if not isinstance(login, str) or not login or len(login) > 100:
                raise ValueError
            items.append(
                InstallationView(
                    id=item["id"],
                    account={"login": login},
                    repository_selection=item["repository_selection"],
                    suspended=item.get("suspended_at") is not None,
                    contents_readable=item["permissions"].get("contents") in {"read", "write"},
                )
            )
    except (ValidationError, ValueError, TypeError, KeyError):
        raise HTTPException(409, "GitHub returned invalid installation metadata.") from None
    return InstallationPage(
        items=items, next_page=number + 1 if len(values) == 100 and number < 10 else None
    )


@router.get("/installations", response_model=InstallationPage)
def installations(request: Request, page_number: int = Query(1, ge=1, le=10)):
    owner = require_connection(request)
    try:
        return page(request, owner, page_number)
    except GraftError as error:
        raise access_error(error) from None


@router.get("/setup")
def setup(request: Request, installation_id: int = Query(gt=0)):
    owner = require_connection(request)
    try:
        for number in range(1, 11):
            result = page(request, owner, number)
            if any(item.id == installation_id for item in result.items):
                return RedirectResponse("/repositories", status_code=303)
            if result.next_page is None:
                break
    except GraftError as error:
        raise access_error(error) from None
    raise HTTPException(404, "Installation access is unavailable. Sync repositories on return.")
