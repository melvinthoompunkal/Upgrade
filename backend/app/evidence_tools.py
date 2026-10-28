"""Shared, bounded evidence tools and the read-only model conversation protocol."""

import json
from typing import cast

from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from app.contracts import Contract, RepoPath
from app.errors import EvidenceError, GraftError, ModelOutputError
from app.evidence_contracts import ReadRange, SearchSymbols
from app.models.meta_client import MetaClient
from app.models.schema import response_format, strict_schema
from app.tools.evidence import EvidenceSession


class EvidencePath(Contract):
    path: RepoPath
    query: str | None


class EvidenceTools:
    def __init__(self, evidence: EvidenceSession, scope: tuple[str, ...]):
        self.evidence = evidence
        self.scope = scope
        self.events: list[dict] = []
        self.registry = {
            "read_range": (ReadRange, "Read at most 200 inclusive lines of inventoried text."),
            "python_evidence": (
                EvidencePath,
                "Inspect Python AST symbols/imports without execution.",
            ),
            "search_symbols": (SearchSymbols, "Search AST symbols in the declared focus paths."),
        }

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "strict": True,
                    "parameters": strict_schema(schema),
                },
            }
            for name, (schema, description) in self.registry.items()
        ]

    def dispatch(self, name: str, raw: str) -> dict:
        if name not in self.registry or len(raw.encode("utf-8")) > 4096:
            raise EvidenceError(
                "Unsupported evidence tool or oversized arguments; nothing executed."
            )
        try:
            arguments = self.registry[name][0].model_validate_json(raw)
        except ValidationError:
            raise EvidenceError("Evidence tool arguments failed the registered schema.") from None
        if isinstance(arguments, ReadRange):
            return self.evidence.read_range(
                arguments.path, arguments.start_line, arguments.end_line
            )
        if isinstance(arguments, EvidencePath):
            index = self.evidence.index(arguments.path)
            matches = [
                s
                for s in index.symbols
                if arguments.query is None or arguments.query.lower() in s.name.lower()
            ]
            return {
                "path": index.path,
                "symbols": [s.model_dump() for s in matches[:80]],
                "imports": list(index.imports),
                "parse_error": index.parse_error,
                "truncated": len(matches) > 80,
            }
        assert isinstance(arguments, SearchSymbols)
        paths = {f.path for f in self.evidence.repo.inventory.files}
        matches = [
            {"path": path, **symbol.model_dump()}
            for path in self.scope
            if path.endswith(".py") and path in paths
            for symbol in self.evidence.index(path).symbols
            if arguments.query.lower() in symbol.name.lower()
        ]
        return {
            "matches": matches[:80],
            "truncated": len(matches) > 80,
            "scope": list(self.scope),
            "qualification": "Static AST evidence only.",
        }

    def respond(self, message, turn: int, call) -> list[ChatCompletionMessageParam]:
        """Retain the assistant message and matching IDs even for denied tool calls."""
        if len(message.tool_calls) > 8:
            raise ModelOutputError("Too many evidence tool calls in one turn.", call=call)
        responses = [
            cast(ChatCompletionMessageParam, message.model_dump(mode="json", exclude_none=True))
        ]
        seen = set()
        for tool in message.tool_calls:
            if tool.type != "function" or not 1 <= len(tool.id) <= 64 or tool.id in seen:
                raise EvidenceError("Invalid or duplicate evidence tool-call identity.", call=call)
            seen.add(tool.id)
            event = {"turn": turn, "tool": tool.function.name, "call_id": tool.id}
            try:
                result = self.dispatch(tool.function.name, tool.function.arguments)
                event.update(status="passed", arguments=json.loads(tool.function.arguments))
            except GraftError as exc:
                result = {"error_code": exc.code, "detail": str(exc)}
                event["status"] = "rejected"
            self.events.append(event)
            responses.append(
                {"role": "tool", "tool_call_id": tool.id, "content": json.dumps(result)}
            )
        return responses


async def collect_evidence[T: BaseModel](
    client: MetaClient,
    tools: EvidenceTools,
    messages: list[ChatCompletionMessageParam],
    schema: type[T],
    *,
    stage: str,
    turns: int,
    output_tokens: int = 8000,
) -> T:
    definitions, formatting = tools.schemas(), response_format(schema)
    for turn in range(1, turns + 1):
        completion, call = await client.request(
            stage=f"{stage}.{turn}",
            messages=messages,
            reasoning="medium",
            output_tokens=output_tokens,
            tools=definitions,
            tool_choice="auto",
            parallel_tool_calls=False,
            response_format=formatting,
        )
        message = completion.choices[0].message
        if not message.tool_calls:
            return client.parse(completion, call, schema)
        if completion.choices[0].finish_reason != "tool_calls":
            raise ModelOutputError("Invalid evidence tool-call response.", call=call)
        messages.extend(tools.respond(message, turn, call))
    raise EvidenceError(f"{stage} exceeded its {turns}-turn evidence limit.")
