"""Firecrawl-backed web search and scrape tools.

These tools give every agent live web access during the tool-use loop, fixing
the staleness problem inherent to any model with a training cutoff.

Two tools:
  - web_search: search the web, return scraped markdown of top N results
  - web_scrape: deep-read a single known URL, return clean markdown

Both tools soft-fail: if Firecrawl is unreachable or returns an error, the
tool returns the error string in the tool result and the agent continues.

Every call is logged with structlog so usage can be monitored.
"""

import json
import os
from typing import Any

import structlog

logger = structlog.get_logger()

# Truncate each search result's markdown to this many chars to control token usage.
# The agent can call web_scrape on any URL it wants to read in full.
SEARCH_RESULT_MARKDOWN_LIMIT = 3000

# Default and max number of search results per web_search call.
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 10

# Per-call timeout (Firecrawl /search with scraping can take 15-30s on real pages)
FIRECRAWL_TIMEOUT_SECONDS = 60


def _get_firecrawl_client() -> Any | None:
    """Lazy import + construct the Firecrawl client. Returns None if unavailable."""
    from src.utils.secrets import read_secret
    api_key = read_secret("firecrawl_api_key", "FIRECRAWL_API_KEY")
    if not api_key or api_key.startswith("fc-xxxxx"):
        return None
    try:
        from firecrawl import FirecrawlApp
        return FirecrawlApp(api_key=api_key)
    except Exception as e:
        logger.warning("firecrawl_import_failed", error=str(e))
        return None


def _truncate_markdown(text: str, limit: int = SEARCH_RESULT_MARKDOWN_LIMIT) -> str:
    """Truncate markdown content with a clear marker so the agent knows it can deep-read."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[... truncated — call web_scrape on this URL for full content]"


class WebSearchTool:
    """Web search via Firecrawl /search endpoint with content scraping."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "web_search",
            "description": (
                "Search the web for current, up-to-date information. Returns the top "
                "N search results with their title, URL, and scraped page content "
                "(as markdown). Use this when you need information newer than your "
                "training data, such as recent news, current pricing, latest software "
                "versions, market trends, or anything time-sensitive. Do NOT use for "
                "general knowledge questions you can answer from training."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query. Be specific and include date hints when relevant (e.g., 'latest GPT-5 features 2025').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Number of results to return (default {DEFAULT_SEARCH_LIMIT}, max {MAX_SEARCH_LIMIT}).",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_LIMIT,
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(self, params: dict) -> str:
        query = (params.get("query") or "").strip()
        if not query:
            return "Error: web_search requires a non-empty 'query' parameter."

        limit = int(params.get("limit") or DEFAULT_SEARCH_LIMIT)
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))

        client = _get_firecrawl_client()
        if not client:
            return "Error: FIRECRAWL_API_KEY is not configured. Web search is unavailable."

        logger.info("firecrawl_search_started", query=query, limit=limit)

        try:
            # firecrawl-py v2 search signature
            response = client.search(
                query=query,
                limit=limit,
                scrape_options={"formats": ["markdown"], "only_main_content": True},
            )
        except Exception as e:
            logger.warning("firecrawl_search_failed", query=query, error=str(e))
            return f"Error: Firecrawl search failed: {e}"

        results = _normalize_search_response(response)
        if not results:
            logger.info("firecrawl_search_empty", query=query)
            return f"No results found for query: {query}"

        formatted: list[dict[str, str]] = []
        for r in results[:limit]:
            formatted.append({
                "url": r.get("url", ""),
                "title": r.get("title", "")[:200],
                "markdown": _truncate_markdown(r.get("markdown", "") or r.get("description", "")),
            })

        logger.info(
            "firecrawl_search_completed",
            query=query,
            results=len(formatted),
            total_chars=sum(len(r["markdown"]) for r in formatted),
        )

        # Return as JSON the agent can parse easily
        return json.dumps({
            "query": query,
            "result_count": len(formatted),
            "results": formatted,
        }, indent=2)


class WebScrapeTool:
    """Deep-read a single URL via Firecrawl /scrape endpoint."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "web_scrape",
            "description": (
                "Fetch and parse a single URL, returning its full content as clean "
                "markdown. Use this when you already have a specific URL you want to "
                "read in full (e.g., a result from web_search that looked promising, "
                "a vendor's documentation page, a competitor's pricing page). Handles "
                "JavaScript-rendered pages and complex layouts."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to scrape (must include http:// or https://).",
                    },
                },
                "required": ["url"],
            },
        }

    async def execute(self, params: dict) -> str:
        url = (params.get("url") or "").strip()
        if not url:
            return "Error: web_scrape requires a non-empty 'url' parameter."
        if not url.startswith(("http://", "https://")):
            return f"Error: web_scrape URL must start with http:// or https:// (got: {url})"

        client = _get_firecrawl_client()
        if not client:
            return "Error: FIRECRAWL_API_KEY is not configured. Web scraping is unavailable."

        logger.info("firecrawl_scrape_started", url=url)

        try:
            response = client.scrape(
                url=url,
                formats=["markdown"],
                only_main_content=True,
            )
        except Exception as e:
            logger.warning("firecrawl_scrape_failed", url=url, error=str(e))
            return f"Error: Firecrawl scrape failed for {url}: {e}"

        result = _normalize_scrape_response(response)
        if not result.get("markdown"):
            logger.info("firecrawl_scrape_empty", url=url)
            return f"Scrape returned no content for: {url}"

        logger.info(
            "firecrawl_scrape_completed",
            url=url,
            markdown_chars=len(result["markdown"]),
        )

        return json.dumps({
            "url": result.get("url", url),
            "title": result.get("title", ""),
            "markdown": result["markdown"],
        }, indent=2)


# ── Response normalization helpers ─────────────────────────
# firecrawl-py v2 returns pydantic models:
#   - SearchData (for /search) has `.web`, `.news`, `.images` list attributes
#   - Document (for /scrape or items inside SearchData.web with scrape_options) has
#     `.markdown`, `.html`, `.metadata` (DocumentMetadata)
#   - DocumentMetadata has `.url`, `.title`, `.description`, `.sourceURL`, etc.
# These helpers also handle the v1 dict-style response for forward compatibility.


def _normalize_search_response(response: Any) -> list[dict[str, Any]]:
    """Extract a list of {url, title, markdown} dicts from any Firecrawl search response."""
    if response is None:
        return []
    # v2 pydantic: SearchData.web
    web = getattr(response, "web", None)
    if web:
        return [_item_to_dict(item) for item in web if item is not None]
    # v1 dict with "data" key
    if isinstance(response, dict):
        data = response.get("data") or response.get("web") or []
        return [_item_to_dict(item) for item in data if item is not None]
    # v2 pydantic dict fallback
    if hasattr(response, "model_dump"):
        dump = response.model_dump()
        for key in ("web", "data", "results"):
            if dump.get(key):
                return [_item_to_dict(item) for item in dump[key]]
    return []


def _normalize_scrape_response(response: Any) -> dict[str, Any]:
    """Extract {url, title, markdown} from a Firecrawl scrape response (Document)."""
    if response is None:
        return {}
    # v2 pydantic Document at top level
    if hasattr(response, "markdown") or hasattr(response, "metadata"):
        return _item_to_dict(response)
    # v1 dict response with "data" key
    if isinstance(response, dict):
        if "data" in response and response["data"]:
            return _item_to_dict(response["data"])
        return _item_to_dict(response)
    return {}


def _extract_metadata_fields(meta: Any) -> dict[str, str]:
    """Pull common fields out of a Firecrawl DocumentMetadata, handling pydantic or dict."""
    if meta is None:
        return {}
    if isinstance(meta, dict):
        md = meta
    elif hasattr(meta, "model_dump"):
        md = meta.model_dump()
    else:
        md = {
            "url": getattr(meta, "url", None),
            "sourceURL": getattr(meta, "sourceURL", None),
            "title": getattr(meta, "title", None),
            "description": getattr(meta, "description", None),
            "ogTitle": getattr(meta, "ogTitle", None),
        }
    return {
        "url": md.get("url") or md.get("sourceURL") or md.get("source_url") or md.get("ogUrl") or "",
        "title": md.get("title") or md.get("ogTitle") or "",
        "description": md.get("description") or md.get("ogDescription") or "",
    }


def _item_to_dict(item: Any) -> dict[str, Any]:
    """Convert a Firecrawl result item (dict or pydantic) into a flat {url, title, markdown}."""
    if item is None:
        return {}
    # Dict-style item
    if isinstance(item, dict):
        meta = _extract_metadata_fields(item.get("metadata"))
        return {
            "url": item.get("url") or meta["url"],
            "title": item.get("title") or meta["title"],
            "description": item.get("description") or meta["description"],
            "markdown": item.get("markdown") or "",
        }
    # Pydantic-style item (firecrawl-py v2 Document or SearchResultWeb)
    meta = _extract_metadata_fields(getattr(item, "metadata", None))
    return {
        "url": getattr(item, "url", None) or meta["url"],
        "title": getattr(item, "title", None) or meta["title"],
        "description": getattr(item, "description", None) or meta["description"],
        "markdown": getattr(item, "markdown", None) or "",
    }
