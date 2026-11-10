from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


MAX_SOURCES = 3
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_PAGE_CHARS = 8_000
MAX_REFERENCE_CHARS = 2_500
REQUEST_TIMEOUT_SECONDS = 12
_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "nav", "footer"}
_BLOCK_TAGS = {
    "article", "br", "div", "h1", "h2", "h3", "h4", "li", "p", "section",
}


class WebSourceError(ValueError):
    pass


@dataclass(frozen=True)
class WebSource:
    url: str
    title: str
    text: str


class _ReadableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._title_depth = 0
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._title_depth += 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title" and self._title_depth:
            self._title_depth -= 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        elif self._skip_depth == 0:
            self.text_parts.append(data)


def _resolved_addresses(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return {ipaddress.ip_address(host.split("%", 1)[0])}
    except ValueError:
        pass
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise WebSourceError(f"Could not resolve source host '{host}': {error}") from error
    addresses = {
        ipaddress.ip_address(record[4][0].split("%", 1)[0])
        for record in records
    }
    if not addresses:
        raise WebSourceError(f"Could not resolve source host '{host}'.")
    return addresses


def validate_source_url(url: str) -> str:
    value = url.strip()
    if not value or len(value) > 2048:
        raise WebSourceError("Enter a valid HTTPS source URL.")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as error:
        raise WebSourceError("Enter a valid HTTPS source URL.") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WebSourceError("Web sources must use a public HTTPS URL.")
    addresses = _resolved_addresses(parsed.hostname)
    if any(not address.is_global for address in addresses):
        raise WebSourceError("Private and local network addresses cannot be used as web sources.")
    return value


def extract_readable_text(markup: str) -> tuple[str, str]:
    parser = _ReadableTextParser()
    parser.feed(markup)
    parser.close()
    title = " ".join(" ".join(parser.title_parts).split())
    text = " ".join(" ".join(parser.text_parts).split())
    return title, text[:MAX_PAGE_CHARS]


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        validate_source_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch_web_sources(
    urls_text: str,
    *,
    opener: Any | None = None,
) -> tuple[WebSource, ...]:
    urls = list(dict.fromkeys(line.strip() for line in urls_text.splitlines() if line.strip()))
    if not urls:
        raise WebSourceError("Paste one or more HTTPS source URLs first.")
    if len(urls) > MAX_SOURCES:
        raise WebSourceError(f"Add no more than {MAX_SOURCES} source URLs at a time.")
    validated = [validate_source_url(url) for url in urls]
    request_opener = opener or urllib.request.build_opener(_PublicRedirectHandler())
    sources: list[WebSource] = []
    for url in validated:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html, text/plain;q=0.9",
                "User-Agent": "Text2Speech/1.2",
            },
        )
        try:
            with request_opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                final_url = validate_source_url(response.geturl())
                content_type = response.headers.get_content_type().lower()
                if content_type not in {"text/html", "text/plain"}:
                    raise WebSourceError(
                        f"Source {final_url} is not an HTML or plain-text page."
                    )
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_PAGE_BYTES:
                    raise WebSourceError(f"Source {final_url} is larger than 2 MB.")
                payload = response.read(MAX_PAGE_BYTES + 1)
        except WebSourceError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
            raise WebSourceError(f"Could not fetch source {url}: {error}") from error
        if len(payload) > MAX_PAGE_BYTES:
            raise WebSourceError(f"Source {url} is larger than 2 MB.")
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            body = payload.decode(charset, errors="replace")
        except LookupError:
            body = payload.decode("utf-8", errors="replace")
        if content_type == "text/html":
            title, text = extract_readable_text(body)
        else:
            title, text = "", " ".join(body.split())[:MAX_PAGE_CHARS]
        if not text:
            raise WebSourceError(f"Source {url} did not contain readable text.")
        sources.append(WebSource(final_url, title or final_url, text))
    return tuple(sources)


def format_source_context(
    sources: tuple[WebSource, ...],
    max_chars: int = MAX_REFERENCE_CHARS,
) -> str:
    if max_chars < 1:
        return ""
    pieces: list[str] = []
    remaining = max_chars
    for source in sources:
        header = f"Title: {source.title}\nURL: {source.url}\nExcerpt: "
        if remaining <= len(header):
            break
        excerpt = source.text[: remaining - len(header)]
        piece = header + excerpt
        pieces.append(piece)
        remaining -= len(piece) + 2
        if remaining <= 0:
            break
    return "\n\n".join(pieces)
