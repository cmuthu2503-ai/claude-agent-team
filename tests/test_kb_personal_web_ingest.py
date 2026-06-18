"""KB-PL — URL→text adapter (web_ingest) unit tests.

No network: a fake scraper stands in for WebScrapeTool, returning the same
JSON-string contract its ``execute`` produces. Pins the parse, the error
handling, and the title fallback.
"""

from __future__ import annotations

import json

import pytest

from src.knowledge.web_ingest import (
    ArticleFetchError,
    FetchedArticle,
    fetch_article,
)


class _FakeScraper:
    def __init__(self, result: str) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def execute(self, params: dict) -> str:
        self.calls.append(params)
        return self._result


async def test_fetch_article_parses_scraper_json():
    raw = json.dumps({
        "url": "https://example.com/article",
        "title": "Agentic AI in Banking",
        "markdown": "# Agentic AI in Banking\n\nMulti-agent systems for underwriting.",
    })
    scraper = _FakeScraper(raw)
    art = await fetch_article("https://example.com/article", scraper=scraper)
    assert isinstance(art, FetchedArticle)
    assert art.title == "Agentic AI in Banking"
    assert "underwriting" in art.text
    assert art.url == "https://example.com/article"
    assert art.metadata["fetched_via"] == "firecrawl"
    assert scraper.calls == [{"url": "https://example.com/article"}]


async def test_fetch_article_rejects_bad_scheme():
    with pytest.raises(ArticleFetchError):
        await fetch_article("ftp://nope", scraper=_FakeScraper("{}"))


async def test_fetch_article_raises_on_error_string():
    scraper = _FakeScraper("Error: Firecrawl scrape failed: 403")
    with pytest.raises(ArticleFetchError):
        await fetch_article("https://example.com/x", scraper=scraper)


async def test_fetch_article_raises_on_empty_markdown():
    scraper = _FakeScraper(json.dumps({"url": "https://e.com", "markdown": ""}))
    with pytest.raises(ArticleFetchError):
        await fetch_article("https://e.com", scraper=scraper)


async def test_fetch_article_title_fallback_from_url():
    raw = json.dumps({
        "url": "https://example.com/the-loan-underwriting-post",
        "title": "",
        "markdown": "Body text that is long enough to count as content.",
    })
    art = await fetch_article(
        "https://example.com/the-loan-underwriting-post", scraper=_FakeScraper(raw)
    )
    assert "loan underwriting post" in art.title
