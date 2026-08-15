from __future__ import annotations

import logging
import os

from architect_agent.config import get_settings

logger = logging.getLogger(__name__)


def perform_web_search(query: str, *, num_results: int = 5) -> str:
    """Perform web search and return formatted results as a string."""
    settings = get_settings()
    
    # If using stub provider or web search is disabled, return stub results
    if settings.llm_provider == "stub" or not settings.market_research_web_enabled:
        return _stub_search_results(query, num_results)
    
    try:
        # Disable SSL verification if needed by setting environment variable
        original_ssl_verify = os.environ.get("PYTHONHTTPSVERIFY")
        if not settings.ssl_verify:
            os.environ["PYTHONHTTPSVERIFY"] = "0"
        
        from ddgs import DDGS
        
        results: list[dict[str, str]] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=num_results):
                results.append({
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("href") or item.get("link") or "").strip(),
                    "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                })
        
        # Restore original SSL verification setting
        if original_ssl_verify is not None:
            os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
        elif "PYTHONHTTPSVERIFY" in os.environ:
            del os.environ["PYTHONHTTPSVERIFY"]
        
        return _format_search_results(results)
    except Exception:
        logger.exception("Web search failed for query=%r; falling back to stub results", query)
        return _stub_search_results(query, num_results)


def _format_search_results(results: list[dict[str, str]]) -> str:
    """Format search results as a readable string."""
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
    """Return stub search results for testing or when web search is disabled."""
    q = query.strip() or "software system"
    stub_results = [
        {
            "title": f"Popular architecture pattern for {q[:40]}",
            "url": "https://example.com/architecture-pattern",
            "snippet": "Common system design approach with microservices, event-driven architecture, and scalable storage patterns."
        },
        {
            "title": f"Industry best practices for {q[:40]}",
            "url": "https://example.com/best-practices",
            "snippet": "Standard requirements including security, scalability, high availability, and performance considerations."
        },
        {
            "title": f"Open source alternatives for {q[:40]}",
            "url": "https://example.com/open-source",
            "snippet": "Community-driven solutions with customizable features and lower total cost of ownership."
        },
        {
            "title": f"Enterprise solutions for {q[:40]}",
            "url": "https://example.com/enterprise",
            "snippet": "Commercial platforms with managed services, SLA guarantees, and enterprise-grade support."
        },
        {
            "title": f"Technical specifications for {q[:40]}",
            "url": "https://example.com/specifications",
            "snippet": "Detailed technical requirements including API standards, data formats, and integration patterns."
        },
    ]
    
    return _format_search_results(stub_results[:num_results])