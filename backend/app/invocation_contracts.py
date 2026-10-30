"""Declarative F11 calls: JSON APIs and bounded, offline HTTPX exchanges."""

import json
import math
from typing import Literal

from pydantic import Field, model_validator

from app.contracts import Contract, FrozenList

Identifier = r"^[a-zA-Z_]\w*$"


def json_value(raw: str):
    def reject(value):
        raise ValueError("Non-finite JSON constants are unsupported.")

    try:
        value = json.loads(raw, parse_constant=reject)
    except RecursionError:
        raise ValueError("JSON exceeds the nesting limit.") from None
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ValueError("JSON exceeds the nesting limit.")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON numbers are unsupported.")
        if isinstance(item, (list, dict)):
            pending.extend(
                (child, depth + 1) for child in (item.values() if isinstance(item, dict) else item)
            )
    return value


class HTTPExchange(Contract):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    url: str = Field(pattern=r"^https?://[^\s]+$", max_length=1000)
    request_json: str | None = Field(max_length=8000)
    status_code: int = Field(ge=100, le=599)
    response_json: str = Field(max_length=8000)
    headers_json: str = Field(max_length=2000)

    @model_validator(mode="after")
    def valid_payloads(self):
        if self.request_json is not None:
            json_value(self.request_json)
        json_value(self.response_json)
        headers = json_value(self.headers_json)
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and not any(c in k + v for c in "\r\n\x00")
            for k, v in headers.items()
        ):
            raise ValueError("HTTP headers must be a JSON string map without control characters.")
        return self


class HTTPScenario(Contract):
    client_parameter: str = Field(pattern=Identifier, max_length=100)
    exchanges: FrozenList[HTTPExchange] = Field(min_length=1, max_length=12)


class Invocation(Contract):
    kind: Literal["function", "method", "new_function", "new_method"]
    class_name: str | None = Field(pattern=Identifier, max_length=100)
    constructor_args_json: str = Field(max_length=8000)
    constructor_kwargs_json: str = Field(max_length=8000)
    await_result: bool
    http: HTTPScenario | None

    @model_validator(mode="after")
    def valid_constructor(self):
        args, kwargs = (
            json_value(self.constructor_args_json),
            json_value(self.constructor_kwargs_json),
        )
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("Constructor arguments must be a JSON array/object.")
        if (self.class_name is not None) != self.kind.endswith("method"):
            raise ValueError("Only method calls require an existing class.")
        if self.class_name is None and (args or kwargs):
            raise ValueError("Function calls cannot supply constructor arguments.")
        if self.class_name and self.class_name.startswith("__"):
            raise ValueError("Dunder classes are unsupported.")
        return self
