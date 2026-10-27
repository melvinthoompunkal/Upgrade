"""Frozen pre-implementation retry contract. Four cases deliberately fail on the baseline.

Policy: GET retries for HTTP 503 only in this initial fixture, at most three total attempts.
Broader source-backed status selection belongs to the tracing milestone.
"""

import httpx
import pytest

from crawler import Crawler


@pytest.mark.parametrize("statuses", [(503, 200), (503, 503, 200)], ids=["one-503", "two-503s"])
def test_transient_503_recovers(statuses: tuple[int, ...]) -> None:
    attempts: list[int] = []

    def respond(_: httpx.Request) -> httpx.Response:
        status = statuses[min(len(attempts), len(statuses) - 1)]
        attempts.append(status)
        return httpx.Response(status, text="recovered body")

    page = None
    observed_status = None
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        try:
            page = Crawler(client).fetch("https://example.test/")
        except httpx.HTTPStatusError as error:
            observed_status = error.response.status_code
    assert page is not None, f"retry recovery missing: observed HTTP {observed_status}"
    assert page.body == "recovered body"
    assert attempts == list(statuses)


def test_persistent_503_stops_after_three_attempts() -> None:
    attempts: list[int] = []

    def respond(_: httpx.Request) -> httpx.Response:
        attempts.append(503)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            Crawler(client).fetch("https://example.test/")
    assert error.value.response.status_code == 503
    assert len(attempts) == 3, "retry exhaustion must make exactly three total attempts"


def test_success_is_attempted_once() -> None:
    attempts: list[int] = []

    def respond(_: httpx.Request) -> httpx.Response:
        attempts.append(200)
        return httpx.Response(200, text="ready")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert Crawler(client).fetch("https://example.test/").body == "ready"
    assert attempts == [200]


def test_permanent_404_is_attempted_once() -> None:
    attempts: list[int] = []

    def respond(_: httpx.Request) -> httpx.Response:
        attempts.append(404)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            Crawler(client).fetch("https://example.test/missing")
    assert error.value.response.status_code == 404
    assert attempts == [404]


def test_recovered_body_is_parsed_for_links() -> None:
    attempts: list[int] = []

    def respond(_: httpx.Request) -> httpx.Response:
        status = 503 if not attempts else 200
        attempts.append(status)
        return httpx.Response(status, text='<a href="/next">next</a>')

    page = None
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        try:
            page = Crawler(client).fetch("https://example.test/")
        except httpx.HTTPStatusError:
            pass
    assert page is not None, "retry recovery must precede body parsing"
    assert page.links == ("https://example.test/next",)
    assert attempts == [503, 200]


def test_successful_traversal_is_unchanged() -> None:
    visited: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        visited.append(request.url.path)
        body = '<a href="/next">next</a><a href="https://elsewhere.test/">offsite</a>'
        return httpx.Response(200, text=body if request.url.path == "/" else "finished")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        pages = Crawler(client).crawl("https://example.test/")
    assert visited == ["/", "/next"]
    assert pages[-1].body == "finished"
