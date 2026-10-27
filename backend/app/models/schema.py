"""Fail locally for schemas outside the object/array subset used by our Meta contracts."""

from typing import Any

from pydantic import BaseModel

from app.errors import ConfigurationError


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
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)},
    }
