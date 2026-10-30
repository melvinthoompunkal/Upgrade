"""Hold a host-wide read lock throughout each HTTP response; operators take write ownership."""

from starlette.responses import JSONResponse

from app.execution import ProcessLock


class MaintenanceMiddleware:
    def __init__(self, app, *, root):
        self.app, self.root = app, root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        lock = ProcessLock(self.root / "maintenance.lock", shared=True)
        if not lock.acquire():
            response = JSONResponse(
                {"detail": "Service maintenance is in progress. Retry shortly."},
                503,
                headers={"Retry-After": "30", "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            lock.close()
