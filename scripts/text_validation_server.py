"""Serve an existing FE saved-data seed with writes and inference disabled."""

import argparse
import json
from pathlib import Path

import uvicorn
from starlette.responses import JSONResponse

from app.config import Settings
from app.main import create_app
from app.tools.runner import PROJECT_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5188)
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding="utf-8"))
    root = Path(context["data_root"]).resolve()
    if not root.is_relative_to(PROJECT_ROOT / "data"):
        parser.error("Saved validation data must remain inside the workspace data directory.")
    app = create_app(
        Settings(_env_file=None, model_api_key=None, auth_mode="local"),
        data_root=root,
        start_worker=False,
    )

    @app.middleware("http")
    async def read_only(request, call_next):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "Read-only validation server."}, status_code=405)
        return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
