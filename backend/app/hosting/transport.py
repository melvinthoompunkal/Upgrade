"""Require the canonical HTTPS authority after trusted proxy processing by Uvicorn."""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class HostedTransportMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, origin: str):
        super().__init__(app)
        self.authority = urlsplit(origin).netloc

    async def dispatch(self, request, call_next):
        if request.url.scheme != "https" or request.headers.get("host") != self.authority:
            return JSONResponse({"detail": "Use the configured HTTPS application origin."}, 400)
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response
