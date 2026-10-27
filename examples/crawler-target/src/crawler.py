"""A bounded, same-origin crawler. HTTP failures currently stop at the first attempt."""

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

import httpx


@dataclass(frozen=True)
class Page:
    url: str
    body: str
    links: tuple[str, ...]


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


class Crawler:
    """The caller owns the injected client; fetch returns a Page or an HTTPX exception."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def fetch(self, url: str) -> Page:
        response = self.client.get(url)
        response.raise_for_status()
        parser = _Links()
        parser.feed(response.text)
        links = tuple(
            dict.fromkeys(urldefrag(urljoin(str(response.url), h))[0] for h in parser.hrefs)
        )
        return Page(url=str(response.url), body=response.text, links=links)

    def crawl(self, start_url: str, *, max_pages: int = 10) -> list[Page]:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        start = httpx.URL(urldefrag(start_url)[0])
        if start.scheme not in {"http", "https"} or not start.host:
            raise ValueError("start_url must be an absolute HTTP(S) URL")
        origin = (start.scheme, start.host, start.port)
        pending = [str(start)]
        seen: set[str] = set()
        pages: list[Page] = []
        while pending and len(pages) < max_pages:
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            page = self.fetch(url)
            pages.append(page)
            for link in page.links:
                parsed = httpx.URL(link)
                if (parsed.scheme, parsed.host, parsed.port) == origin:
                    pending.append(str(parsed))
        return pages
