"""Tests for ycai.crawler. Network-free — every test uses an httpx MockTransport."""

from __future__ import annotations

import asyncio

import httpx

from ycai.crawler import (
    DEFAULT_PAGE_BYTES,
    USER_AGENT,
    _extract_links,
    _extract_text,
    _rank_links,
    crawl_company,
)

# ----- HTML helpers --------------------------------------------------------------


def test_extract_text_strips_scripts_styles_and_tags() -> None:
    html = """
    <html><head>
      <script>alert("evil")</script>
      <style>body{color:red}</style>
    </head><body>
      <h1>Acme AI</h1>
      <p>We build agents.</p>
      <script>tracking()</script>
    </body></html>
    """
    text = _extract_text(html)
    assert "alert" not in text
    assert "color:red" not in text
    assert "Acme AI" in text
    assert "We build agents." in text


def test_extract_links_only_same_host() -> None:
    html = """
    <a href="/about">About</a>
    <a href="https://acme.example/pricing">Pricing</a>
    <a href="https://github.com/acme/repo">GitHub</a>
    <a href="mailto:hi@acme.example">contact</a>
    <a href="javascript:void(0)">js</a>
    <a href="#features">jump</a>
    """
    links = _extract_links(html, base_url="https://acme.example/")
    assert "https://acme.example/about" in links
    assert "https://acme.example/pricing" in links
    assert "https://github.com/acme/repo" not in links  # off-host
    assert all("mailto" not in u for u in links)
    assert all("javascript" not in u for u in links)


def test_extract_links_dedupes_and_strips_fragments() -> None:
    html = """
    <a href="/about">A</a>
    <a href="/about#team">B</a>
    <a href="/about">C</a>
    """
    links = _extract_links(html, base_url="https://acme.example/")
    assert links == ["https://acme.example/about"]


def test_rank_links_prefers_signal_paths() -> None:
    urls = [
        "https://acme.example/blog/2024/12",
        "https://acme.example/pricing",
        "https://acme.example/contact",
        "https://acme.example/security",
    ]
    ranked = _rank_links(urls)
    # /pricing should outrank /security per the _SIGNAL_PATHS ordering.
    assert ranked[0].endswith("/pricing")
    assert ranked[1].endswith("/security")


# ----- crawl_company -------------------------------------------------------------


def _build_handler(routes: dict[str, tuple[int, str, str]]) -> httpx.MockTransport:
    """Return an httpx MockTransport with route table.

    Each route maps URL -> (status, content_type, body).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in routes:
            return httpx.Response(404, text="not found")
        status, content_type, body = routes[url]
        return httpx.Response(status, headers={"content-type": content_type}, text=body)

    return httpx.MockTransport(handler)


def test_crawl_company_happy_path() -> None:
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
        "https://acme.example/": (200, "text/html", '<a href="/pricing">Price</a> <a href="/about">About</a>'),
        "https://acme.example/pricing": (200, "text/html", "<h1>Pricing</h1><p>$99/mo Pro</p>"),
        "https://acme.example/about": (200, "text/html", "<p>Founded in 2024 by team Acme.</p>"),
    }
    transport = _build_handler(routes)
    client = httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    assert not result.robots_blocked
    assert result.error is None
    urls = [p.url for p in result.pages]
    assert "https://acme.example/" in urls
    # /pricing ranks above /about; both should appear.
    assert any("/pricing" in u for u in urls)
    assert any("/about" in u for u in urls)
    # Sanitization happened.
    pricing = next(p for p in result.pages if "/pricing" in p.url)
    assert "$99/mo" in pricing.text


def test_crawl_company_respects_robots_disallow() -> None:
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n"),
        "https://acme.example/": (200, "text/html", "<p>shouldn't be fetched</p>"),
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    assert result.robots_blocked is True
    assert result.pages == []


def test_crawl_company_respects_per_path_disallow() -> None:
    routes = {
        "https://acme.example/robots.txt": (
            200,
            "text/plain",
            "User-agent: *\nAllow: /\nDisallow: /admin\n",
        ),
        "https://acme.example/": (
            200,
            "text/html",
            '<a href="/admin">admin</a> <a href="/about">about</a>',
        ),
        "https://acme.example/about": (200, "text/html", "About"),
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    fetched = [p.url for p in result.pages]
    assert any("/about" in u for u in fetched)
    assert all("/admin" not in u for u in fetched)


def test_crawl_company_caps_at_max_pages() -> None:
    body_with_many_links = (
        '<a href="/a">a</a><a href="/b">b</a><a href="/c">c</a><a href="/d">d</a>'
        '<a href="/e">e</a><a href="/f">f</a><a href="/g">g</a>'
    )
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
        "https://acme.example/": (200, "text/html", body_with_many_links),
        **{f"https://acme.example/{p}": (200, "text/html", f"page {p}") for p in "abcdefg"},
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client, max_pages=3))
    asyncio.run(client.aclose())
    assert len(result.pages) == 3


def test_crawl_company_handles_homepage_unreachable() -> None:
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    assert result.error == "homepage-unreachable"


def test_crawl_company_handles_invalid_url_gracefully() -> None:
    result = asyncio.run(crawl_company("not-a-url"))
    assert result.error == "invalid-homepage-url"
    assert result.pages == []


def test_crawl_company_refuses_loopback_url() -> None:
    """SSRF guard: a homepage pointing at loopback must be rejected before fetch."""
    result = asyncio.run(crawl_company("http://127.0.0.1:8080/"))
    assert result.error == "unsafe-homepage-url"
    assert result.pages == []


def test_crawl_company_refuses_metadata_endpoint() -> None:
    """SSRF guard: cloud metadata endpoints must be rejected."""
    result = asyncio.run(crawl_company("http://169.254.169.254/latest/meta-data/"))
    assert result.error == "unsafe-homepage-url"
    assert result.pages == []


def test_crawl_company_skips_non_html_content_type() -> None:
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
        "https://acme.example/": (
            200,
            "text/html",
            '<a href="/whitepaper.pdf">PDF</a> <a href="/about">About</a>',
        ),
        "https://acme.example/whitepaper.pdf": (200, "application/pdf", "%PDF-1.4 binary..."),
        "https://acme.example/about": (200, "text/html", "About text"),
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    fetched = [p.url for p in result.pages]
    # The PDF should not appear.
    assert all(not u.endswith(".pdf") for u in fetched)
    assert any("/about" in u for u in fetched)


def test_crawl_company_pii_stripped_from_pages() -> None:
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
        "https://acme.example/": (
            200,
            "text/html",
            "<p>Reach the team at hello@acme.example or call 555-867-5309.</p>",
        ),
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    home = result.pages[0]
    assert "hello@acme.example" not in home.text
    assert "555-867-5309" not in home.text
    assert "[REDACTED_EMAIL]" in home.text or "[REDACTED_PHONE]" in home.text


def test_crawl_company_truncates_to_max_bytes() -> None:
    """The body the crawler hands to the LLM must not exceed max_bytes.

    bytes_fetched can briefly exceed max_bytes by one chunk (MockTransport sends
    the whole body in a single chunk, so the early-break check fires only after
    that chunk is appended). The contract that matters is on ``text``: we only
    decode the first max_bytes bytes, regardless of how much was fetched.
    """
    big_body = "<p>" + ("x" * 100_000) + "</p>"
    routes = {
        "https://acme.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
        "https://acme.example/": (200, "text/html", big_body),
    }
    client = httpx.AsyncClient(transport=_build_handler(routes), headers={"User-Agent": USER_AGENT})
    result = asyncio.run(crawl_company("https://acme.example/", client=client))
    asyncio.run(client.aclose())
    home = result.pages[0]
    assert len(home.text) <= DEFAULT_PAGE_BYTES
