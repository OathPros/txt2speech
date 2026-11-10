import base64
import html
import ipaddress
import unittest
from email.message import Message
from urllib.error import URLError
from unittest.mock import patch

import web_sources
from web_sources import (
    WebSourceError,
    extract_readable_text,
    fetch_web_sources,
    format_source_context,
    search_web_sources,
)


class FakeResponse:
    def __init__(self, body, url="https://example.com/article", content_type="text/html"):
        self.body = body
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url

    def read(self, _limit):
        return self.body


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


class QueueOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class WebSourceTests(unittest.TestCase):
    def test_extracts_readable_html_and_skips_script_and_navigation(self):
        title, text = extract_readable_text(
            "<html><title>Quick guide</title><nav>Menu</nav>"
            "<article><h1>Welcome</h1><p>Useful information.</p>"
            "<script>ignore this</script></article></html>"
        )
        self.assertEqual(title, "Quick guide")
        self.assertEqual(text, "Welcome Useful information.")
        self.assertNotIn("Menu", text)
        self.assertNotIn("ignore this", text)

    def test_fetches_reviewable_public_https_excerpt(self):
        opener = FakeOpener(
            FakeResponse(
                b"<html><title>Guide</title><article><p>Use the local studio.</p></article></html>"
            )
        )
        with patch.object(
            web_sources,
            "_resolved_addresses",
            return_value={ipaddress.ip_address("93.184.216.34")},
        ):
            sources = fetch_web_sources("https://example.com/article", opener=opener)
        self.assertEqual(sources[0].title, "Guide")
        self.assertEqual(sources[0].text, "Use the local studio.")
        self.assertEqual(opener.requests[0][1], web_sources.REQUEST_TIMEOUT_SECONDS)
        self.assertIn("Use the local studio.", format_source_context(sources))

    def test_rejects_non_https_and_private_addresses(self):
        with self.assertRaisesRegex(WebSourceError, "public HTTPS"):
            fetch_web_sources("http://example.com")
        with patch.object(
            web_sources,
            "_resolved_addresses",
            return_value={ipaddress.ip_address("127.0.0.1")},
        ):
            with self.assertRaisesRegex(WebSourceError, "Private and local"):
                fetch_web_sources("https://localhost")

    def test_limits_source_count_and_rejects_non_text_pages(self):
        with self.assertRaisesRegex(WebSourceError, "no more than 3"):
            fetch_web_sources("\n".join(f"https://example.com/{i}" for i in range(4)))
        opener = FakeOpener(FakeResponse(b"data", content_type="application/pdf"))
        with patch.object(
            web_sources,
            "_resolved_addresses",
            return_value={ipaddress.ip_address("93.184.216.34")},
        ):
            with self.assertRaisesRegex(WebSourceError, "not an HTML"):
                fetch_web_sources("https://example.com/document", opener=opener)

    def test_search_fetches_and_returns_reviewable_public_sources(self):
        targets = [
            "https://example.net/unavailable",
            "https://example.com/first",
            "https://example.org/second",
        ]
        result_links = []
        for target in targets:
            encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
            result_links.append(
                '<li class="b_algo"><h2><a href="'
                + html.escape(f"https://www.bing.com/ck/a?u=a1{encoded}")
                + '">Result</a></h2></li>'
            )
        search_response = FakeResponse(
            "".join(result_links).encode(),
            url="https://www.bing.com/search?q=cat+poetry",
        )
        page_responses = [URLError("site unavailable")]
        page_responses.extend(
            FakeResponse(
                f"<html><title>Source {index}</title><article><p>Verified excerpt {index - 1}.</p></article></html>".encode(),
                url=target,
            )
            for index, target in enumerate(targets[1:], start=2)
        )
        pages = QueueOpener(page_responses)
        with patch.object(
            web_sources,
            "_resolved_addresses",
            return_value={ipaddress.ip_address("93.184.216.34")},
        ):
            result = search_web_sources(
                "cat poetry",
                search_opener=FakeOpener(search_response),
                page_opener=pages,
            )

        self.assertEqual(len(result.sources), 2)
        self.assertEqual(result.sources[0].url, targets[1])
        self.assertEqual(result.sources[0].text, "Verified excerpt 1.")
        self.assertEqual(result.skipped_results, 1)
        self.assertEqual(len(pages.requests), 3)

    def test_search_rejects_empty_and_oversized_queries(self):
        with self.assertRaisesRegex(WebSourceError, "query first"):
            search_web_sources(" ")
        with self.assertRaisesRegex(WebSourceError, "300 characters"):
            search_web_sources("x" * 301)


if __name__ == "__main__":
    unittest.main()
