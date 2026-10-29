"""Same-origin mutation guard shared by local API entry points."""

from fastapi import HTTPException, Request


def require_local_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = {
        f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (8000, 5173)
    }
    allowed.add(request.app.state.settings.app_base_url)
    if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Start actions from the local Upgrade application.")
