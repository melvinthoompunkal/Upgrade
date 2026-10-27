import httpx
import pytest

from crawler import Crawler


def test_fetch_returns_body_and_resolved_unique_links() -> None:
    body = '<a href="/next#one">one</a><a href="/next#two">two</a>'
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))) as c:
        page = Crawler(c).fetch("https://example.test/")
    assert page.url == "https://example.test/"
    assert page.body == body
    assert page.links == ("https://example.test/next",)


def test_fetch_preserves_httpx_status_errors() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as c:
        with pytest.raises(httpx.HTTPStatusError) as error:
            Crawler(c).fetch("https://example.test/missing")
    assert error.value.response.status_code == 404


def test_fetch_preserves_transport_errors() -> None:
    def disconnect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline fixture", request=request)

    with httpx.Client(transport=httpx.MockTransport(disconnect)) as c:
        with pytest.raises(httpx.ConnectError):
            Crawler(c).fetch("https://example.test/")


def test_crawl_is_breadth_first_same_origin_and_visits_each_url_once() -> None:
    seen: list[str] = []
    routes = {
        "/": '<a href="/b">b</a><a href="/a">a</a><a href="https://other.test/">other</a>',
        "/b": '<a href="/a#fragment">a</a><a href="/">home</a><a href="mailto:x@y">mail</a>',
        "/a": "done",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=routes[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        pages = Crawler(c).crawl("https://example.test/")
    assert seen == ["/", "/b", "/a"]
    assert [p.url for p in pages] == [f"https://example.test{path}" for path in seen]


def test_crawl_obeys_page_limit() -> None:
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text='<a href="/next">next</a>')

    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        assert len(Crawler(c).crawl("https://example.test/", max_pages=1)) == 1
    assert seen == ["/"]


@pytest.mark.parametrize("max_pages", [0, -1])
def test_crawl_rejects_nonpositive_limits(max_pages: int) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as c:
        with pytest.raises(ValueError, match="positive"):
            Crawler(c).crawl("https://example.test/", max_pages=max_pages)


def test_crawl_rejects_non_http_roots() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as c:
        with pytest.raises(ValueError, match="HTTP"):
            Crawler(c).crawl("file:///etc/passwd")
