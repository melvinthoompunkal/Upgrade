"""Protect every API surface, including excerpts and downloads, before lookup."""

import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth.routes import valid_csrf
from app.auth.store import LOCAL_PRINCIPAL, SESSION_COOKIE
from app.jobs.control import ExecutionContext, execution_context

PUBLIC = {"/api/session", "/api/auth/github/start", "/api/auth/github/callback"}


def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        settings = request.app.state.settings
        request.state.principal = LOCAL_PRINCIPAL
        if request.url.path == "/api/webhooks/github":
            # The webhook route authenticates raw-body HMAC, independent of cookies/CSRF.
            return private_response(await call_next(request))
        if settings.auth_mode == "github":
            request.state.principal = await asyncio.to_thread(
                request.app.state.auth.principal, request.cookies.get(SESSION_COOKIE, "")
            )
            if request.url.path.startswith("/api/") and request.url.path not in PUBLIC:
                if request.state.principal is None:
                    return private_response(
                        JSONResponse(
                            {"detail": "Sign in with GitHub to continue."}, status_code=401
                        )
                    )
                if request.method not in {"GET", "HEAD", "OPTIONS"} and (
                    request.headers.get("origin") != settings.app_base_url
                    or request.headers.get("sec-fetch-site") == "cross-site"
                    or not valid_csrf(request)
                ):
                    return private_response(
                        JSONResponse(
                            {"detail": "Reload Upgrade before trying this action."}, status_code=403
                        )
                    )
        principal = request.state.principal
        with execution_context(ExecutionContext(
            principal.owner_id if principal else "anonymous", request.app.state.runs,
        )):
            response = await call_next(request)
        return private_response(response)
