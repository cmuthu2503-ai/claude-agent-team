"""KB-PL — URL → clean text adapter for the personal knowledge library.

The front door for ingesting an external web article by URL. Wraps the
existing ``WebScrapeTool`` (firecrawl) — the same tool agents use to read a
page — and adapts its JSON tool-result into the ``(text, title, metadata)``
shape the ingestion pipeline wants. Keeping this thin and behind one function
means a local extractor (Trafilatura) can drop in later as an alternate
``fetch_article`` impl without touching the routes (PRD Q-FETCH / Phase 2).

Soft-fail posture matches the rest of the KB: a fetch that returns no usable
content raises ``ArticleFetchError`` with a human-readable reason, which the
route turns into a 4xx/502 — never a silent empty ingest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


class ArticleFetchError(RuntimeError):
    """Raised when a URL can't be fetched or yields no usable article text."""


@dataclass
class FetchedArticle:
    text: str
    title: str
    url: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _coerce_scrape_result(raw: str, url: str) -> FetchedArticle:
    """Parse the WebScrapeTool JSON result into a FetchedArticle.

    ``WebScrapeTool.execute`` returns either a JSON object
    ``{url, title, markdown}`` on success, or a plain ``Error: ...`` /
    ``Scrape returned no content...`` string on failure. We treat anything
    that isn't valid JSON with non-empty markdown as a fetch error.
    """
    if not raw or raw.startswith(("Error:", "Scrape returned no content")):
        raise ArticleFetchError(raw or "empty response from scraper")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ArticleFetchError(f"unparseable scraper response: {e}") from e

    markdown = (data.get("markdown") or "").strip()
    if not markdown:
        raise ArticleFetchError("scraper returned no article content")

    resolved_url = data.get("url") or url
    title = (data.get("title") or "").strip() or _title_from_url(resolved_url)
    return FetchedArticle(
        text=markdown,
        title=title,
        url=resolved_url,
        metadata={"fetched_via": "firecrawl", "source_url": resolved_url},
    )


def _title_from_url(url: str) -> str:
    """Last-resort title when the page exposes none: the final path segment."""
    tail = url.rstrip("/").rsplit("/", 1)[-1] or url
    return tail.replace("-", " ").replace("_", " ")[:200] or url


async def fetch_article(url: str, *, scraper: Any | None = None) -> FetchedArticle:
    """Fetch ``url`` and return clean article text + title + metadata.

    ``scraper`` is injected for tests (any object with an async
    ``execute({"url": ...}) -> str``). In production it defaults to the
    firecrawl-backed ``WebScrapeTool``.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ArticleFetchError(
            f"URL must start with http:// or https:// (got: {url!r})"
        )

    if scraper is None:
        from src.tools.firecrawl_tools import WebScrapeTool

        scraper = WebScrapeTool()

    logger.info("kb_article_fetch_started", url=url)
    raw = await scraper.execute({"url": url})
    article = _coerce_scrape_result(raw, url)
    logger.info(
        "kb_article_fetch_completed",
        url=article.url, title=article.title, chars=len(article.text),
    )
    return article


__all__ = ["fetch_article", "FetchedArticle", "ArticleFetchError"]
