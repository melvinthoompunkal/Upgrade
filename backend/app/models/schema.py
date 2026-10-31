"""Fail locally for invalid response names and unsupported Meta strict schema shapes."""

import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.errors import ConfigurationError


def validation_summary(schema: type[BaseModel], error: ValidationError) -> str:
    """Report schema-owned field names and error types, never provider text or values."""
    fields: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema.model_json_schema())
    issues = []
    for issue in error.errors(include_input=False, include_context=False, include_url=False)[:5]:
        location = ".".join(
            str(part) if isinstance(part, int) else part if part in fields else "<field>"
            for part in issue["loc"]
        )
        issues.append(f"{location or '<root>'} ({issue['type']})")
    return "; ".join(issues)


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    document = schema.model_json_schema()

    def check(node: Any) -> None:
        if isinstance(node, dict):
            if "default" in node or "prefixItems" in node:
                raise ConfigurationError(
                    "Meta strict schemas cannot contain defaults or fixed tuples."
                )
            if node.get("type") == "object":
                properties = node.get("properties", {})
                if node.get("additionalProperties") is not False or set(
                    node.get("required", [])
                ) != set(properties):
                    raise ConfigurationError(
                        "Meta strict objects must forbid extra properties and require every field."
                    )
                # Pydantic omits required for an empty argument object.
                node["required"] = list(properties)
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(document)
    return document


def response_format(schema: type[BaseModel]) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", schema.__name__):
        raise ConfigurationError(
            "Meta response-format names require 1..64 letters, digits, underscores or dashes."
        )
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)},
    }
