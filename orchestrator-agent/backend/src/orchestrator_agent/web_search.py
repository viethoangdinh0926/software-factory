from __future__ import annotations

import logging
import os

from orchestrator_agent.config import get_settings

logger = logging.getLogger(__name__)


def perform_web_search(query: str, *, num_results: int = 5) -> tuple[str, bool]:
    """Return (formatted results, used_live_search).

    Falls back to a stub string when the provider is stub, search is disabled,
    or the live lookup raises.
    """
    settings = get_settings()
    if settings.llm_provider == "stub" or not settings.web_search_enabled:
        return _stub_search_results(query, num_results), False

    try:
        original_ssl_verify = os.environ.get("PYTHONHTTPSVERIFY")
        if not settings.ssl_verify:
            os.environ["PYTHONHTTPSVERIFY"] = "0"

        from ddgs import DDGS

        results: list[dict[str, str]] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=num_results):
                results.append(
                    {
                        "title": str(item.get("title") or "").strip(),
                        "url": str(item.get("href") or item.get("link") or "").strip(),
                        "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                    }
                )

        if original_ssl_verify is not None:
            os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
        elif "PYTHONHTTPSVERIFY" in os.environ:
            del os.environ["PYTHONHTTPSVERIFY"]

        return _format_search_results(results), True
    except Exception:
        logger.exception("Web search failed for query=%r; falling back to model knowledge", query)
        return (
            "LIVE_WEB_SEARCH_UNAVAILABLE. Reason from knowledge; do not invent URLs as live results.\n"
            + _stub_search_results(query, num_results)
        ), False


def _format_search_results(results: list[dict[str, str]]) -> str:
    if not results:
        return "No search results found."
    formatted = []
    for i, result in enumerate(results, 1):
        formatted.append(f"{i}. {result.get('title', 'No title')}")
        formatted.append(f"   URL: {result.get('url', 'No URL')}")
        formatted.append(f"   Snippet: {result.get('snippet', 'No snippet')}")
        formatted.append("")
    return "\n".join(formatted)


def _stub_search_results(query: str, num_results: int = 5) -> str:
    q = query.strip() or "software system"
    stub_results = [
        {
            "title": f"Common tech stack for {q[:40]}",
            "url": "https://example.com/tech-stack",
            "snippet": "Popular combinations include a typed HTTP API, Postgres, Redis, and object storage.",
        },
        {
            "title": f"API style survey for {q[:40]}",
            "url": "https://example.com/api-styles",
            "snippet": "REST is the default; gRPC for internal service meshes; GraphQL for BFF aggregation.",
        },
        {
            "title": f"Open source references for {q[:40]}",
            "url": "https://example.com/open-source",
            "snippet": "Community implementations use FastAPI/Spring/Go chi with OpenAPI contracts.",
        },
        {
            "title": f"Scaling notes for {q[:40]}",
            "url": "https://example.com/scale",
            "snippet": "Separate write/read paths; queue transcoding or heavy jobs; CDN for public media.",
        },
        {
            "title": f"Engineering handbook for {q[:40]}",
            "url": "https://example.com/handbook",
            "snippet": "Keep one deployable per bounded context; contract tests at service boundaries.",
        },
    ]
    return _format_search_results(stub_results[:num_results])
