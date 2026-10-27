# Graft Meta

Adapt one small feature from a source repository into a target project, with Muse Spark planning and code-backed red-to-green evidence. The approved design is in [tech_breakdown.md](tech_breakdown.md).

Phase one implements the **first coding milestone in section 11**: the backend and Meta integration foundation. It includes settings, strict evidence/snapshot/plan contracts, `/health`, an asynchronous Meta adapter, and a CLI that checks structured output and a real read-only tool round trip. Repo ingestion, crawler execution, agents, and the UI follow in later milestones.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). From the repository root:

```powershell
uv sync --project backend --locked
Copy-Item backend/.env.example backend/.env
```

Enter your Meta key in `backend/.env` locally, or provide `MODEL_API_KEY` through your environment. Environment variables take precedence. The `.env` file, virtual environments, caches, research clones, and runtime artifacts are ignored by Git. The example file contains no credentials.

The adapter targets `https://api.meta.ai/v1` and defaults to `muse-spark-1.3` in Standard mode. `META_MODEL_ID` may explicitly select documented Spark 1.2/1.1 if account access requires it. There is no automatic model/provider fallback. The SDK retries transient failures at most twice per request and applies a configurable timeout.

## Run the backend

```powershell
uv run --project backend --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open [health](http://127.0.0.1:8000/health) or [API docs](http://127.0.0.1:8000/docs). `model_configured` reports whether a key is present; it does **not** claim the key works or the model has been verified. The server can start without a key.

## Verify live Meta access

```powershell
uv run --project backend --locked graft-meta-smoke
```

This command makes three small model requests: validate a `RunPlan` using a synthetic inventory, request the harmless `read_probe` tool, and validate acknowledgement of a fresh token supplied only by that tool. No repositories, user files, or shell commands are accessed. It uses `tool_choice="auto"`, which Meta supports; if the model does not call the tool, the check fails explicitly.

A successful command prints JSON with `status: "passed"`, the validated plan, and each call's returned model ID, response ID, token usage, stage, and elapsed time. A missing key, API error, malformed/truncated output, unsupported tool, or incorrect token exits with status 1. Provider error bodies and credentials are excluded from diagnostic output. This is a connectivity/contract check, not the final feature implementation demo.

**Current verification boundary:** offline tests can validate request construction and failure behavior. Live model access remains unverified until this command succeeds with your configured key.

## Development checks

```powershell
uv run --project backend --locked pytest backend/tests
uv run --project backend --locked ruff check backend
uv run --project backend --locked ruff format --check backend
```

Tests use HTTPX's in-memory mock transport; they make no external model requests and need no API key. They check schema/tool request compatibility, full tool message replay, token consumption, bounded/validated tool execution, rejection of invalid evidence paths and outputs, and credential-safe errors.

## Integration references

- [Meta quickstart](https://dev.meta.ai/docs/quickstart)
- [Chat Completions parameters](https://dev.meta.ai/docs/protocols/chat-completions)
- [Structured output and strict schema constraints](https://dev.meta.ai/docs/structured-output)
- [Tool calling and supported tool choice](https://dev.meta.ai/docs/tool-calling)

The current foundation is implemented independently. It does not copy code from the Graft research clone. Preserve upstream licenses/notices when reusing source in subsequent milestones.
