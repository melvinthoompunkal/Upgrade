"""Trusted acceptance runtime, embedded verbatim in frozen sandbox tests."""

import asyncio
import importlib
import json
import sys


def _integration_call(
    module_name,
    function_name,
    args,
    kwargs,
    invocation,
    marker,
    expected_exception,
    exception_message,
):
    module = importlib.import_module(module_name)
    receiver = module
    if invocation["class_name"]:
        receiver = getattr(module, invocation["class_name"])(
            *json.loads(invocation["constructor_args_json"]),
            **json.loads(invocation["constructor_kwargs_json"]),
        )
    try:
        function = getattr(receiver, function_name)
    except AttributeError:
        if invocation["kind"].startswith("new_"):
            raise AssertionError(marker) from None
        raise

    scenario = invocation["http"]
    exchanges = list(scenario["exchanges"]) if scenario else []
    consumed = 0
    mismatch = False

    def transport(request):
        nonlocal consumed, mismatch
        import httpx

        if consumed >= len(exchanges):
            mismatch = True
            raise AssertionError(marker)
        exchange = exchanges[consumed]
        consumed += 1
        if request.method != exchange["method"] or str(request.url) != exchange["url"]:
            mismatch = True
            raise AssertionError(marker)
        if exchange["request_json"] is not None:
            try:
                body = json.loads(request.content)
            except ValueError:
                mismatch = True
                raise AssertionError(marker) from None
            if body != json.loads(exchange["request_json"]):
                mismatch = True
                raise AssertionError(marker)
        return httpx.Response(
            exchange["status_code"],
            content=exchange["response_json"].encode(),
            headers={"content-type": "application/json", **json.loads(exchange["headers_json"])},
        )

    async def asynchronous():
        if scenario:
            import httpx

            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                kwargs[scenario["client_parameter"]] = client
                return await function(*args, **kwargs)
        return await function(*args, **kwargs)

    try:
        if invocation["await_result"]:
            return asyncio.run(asynchronous())
        if scenario:
            import httpx

            with httpx.Client(transport=httpx.MockTransport(transport)) as client:
                kwargs[scenario["client_parameter"]] = client
                return function(*args, **kwargs)
        return function(*args, **kwargs)
    finally:
        error = sys.exc_info()[1]
        completed = error is None or (
            type(error).__name__ == expected_exception and str(error) == exception_message
        )
        if scenario and completed and (mismatch or consumed != len(exchanges)):
            raise AssertionError(marker)
