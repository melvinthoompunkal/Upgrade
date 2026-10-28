"""Session DTOs contain application identity/CSRF state, never provider credentials."""

import base64
import hashlib
import secrets
from typing import Literal
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.store import AUTH_COOKIE, SESSION_COOKIE, SESSION_SECONDS
from app.errors import GraftError

router = APIRouter(prefix="/api")


class SessionUser(BaseModel):
    id: str
    login: str
    name: str | None


class SessionView(BaseModel):
    mode: Literal["local", "github"]
    authenticated: bool
    user: SessionUser | None
    csrf_token: str | None
    connection: Literal["local", "signed_out", "connected", "reconnect_required"]
    install_url: str | None


def destination(value: str) -> str:
    decoded = unquote(value)
    parsed = urlsplit(decoded)
    if (
        parsed.scheme
        or parsed.netloc
        or not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(c) < 32 for c in decoded)
        or parsed.path not in {"/", "/runs", "/system", "/repositories"}
        and not (
            parsed.path.startswith("/runs/")
            and all(part and part not in {".", ".."} for part in parsed.path.split("/")[1:])
        )
    ):
        raise HTTPException(422, "Choose a destination within Upgrade.")
    return value


def cookie(response: Response, request: Request, name: str, value: str, seconds: int):
    response.set_cookie(
        name,
        value,
        max_age=seconds,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_base_url.startswith("https://"),
        path="/",
    )


@router.get("/session", response_model=SessionView)
def session(request: Request):
    mode = request.app.state.settings.auth_mode
    user = request.state.principal
    local = mode == "local"
    return SessionView(
        mode=mode,
        authenticated=local or user is not None,
        user=SessionUser(id=user.owner_id, login=user.login, name=user.name)
        if user and not local
        else None,
        csrf_token=user.csrf if user and not local else None,
        connection="local"
        if local
        else "signed_out"
        if not user
        else ("connected" if user.connected else "reconnect_required"),
        install_url=(
            "https://github.com/apps/"
            + request.app.state.settings.github_app_slug
            + "/installations/new"
        )
        if not local
        else None,
    )


@router.get("/auth/github/start")
def start(request: Request, return_to: str = Query("/", max_length=600)):
    if request.app.state.settings.auth_mode != "github":
        raise HTTPException(409, "Configure GitHub mode before signing in.")
    state, browser, verifier = request.app.state.auth.begin(destination(return_to))
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    response = RedirectResponse(
        "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": request.app.state.settings.github_client_id,
                "redirect_uri": request.app.state.settings.app_base_url
                + "/api/auth/github/callback",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "offline_access",
            }
        ),
        status_code=302,
    )
    cookie(response, request, AUTH_COOKIE, browser, 600)
    return response


@router.get("/auth/github/callback")
def callback(
    request: Request,
    state: str = Query("", max_length=200),
    code: str = Query("", max_length=512),
    error: str = Query("", max_length=200),
):
    if request.app.state.settings.auth_mode != "github":
        raise HTTPException(409, "GitHub sign-in is disabled.")
    transaction = request.app.state.auth.consume(state, request.cookies.get(AUTH_COOKIE, ""))
    if not transaction or error or not code:
        raise HTTPException(400, "Sign-in was denied, expired or invalid. Start sign-in again.")
    verifier, return_to = transaction
    try:
        tokens = request.app.state.github.exchange(code, verifier)
        user = request.app.state.github.profile(tokens.access_token.get_secret_value())
        session_id = request.app.state.auth.sign_in(
            user, tokens, request.cookies.get(SESSION_COOKIE, "")
        )
    except GraftError:
        raise HTTPException(400, "GitHub sign-in failed. Start sign-in again.") from None
    response = RedirectResponse(return_to, status_code=303)
    cookie(response, request, SESSION_COOKIE, session_id, SESSION_SECONDS)
    response.delete_cookie(AUTH_COOKIE, path="/")
    return response


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    if request.app.state.settings.auth_mode == "github":
        request.app.state.auth.logout(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed_out"}


@router.post("/auth/disconnect")
def disconnect(request: Request):
    if request.app.state.settings.auth_mode != "github":
        raise HTTPException(409, "No GitHub account is connected in local mode.")
    request.app.state.auth.disconnect(request.state.principal.owner_id)
    return {"status": "disconnected"}


def valid_csrf(request: Request) -> bool:
    return bool(request.state.principal) and secrets.compare_digest(
        request.headers.get("x-csrf-token", "").encode(), request.state.principal.csrf.encode()
    )
