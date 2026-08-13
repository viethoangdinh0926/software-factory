from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from architect_agent.config import get_settings
from architect_agent.context_budget import maybe_compact_business_spec
from architect_agent.llm import get_chat_model

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = _JSON_RE.search(text)
    if not match:
        raise ValueError(f"Expected JSON object in model response: {text[:400]}")
    return json.loads(match.group(0))


def _invoke_json(system: str, user: str) -> dict[str, Any]:
    model = get_chat_model()
    response = model.invoke(
        [SystemMessage(content=system), HumanMessage(content=user)],
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return _parse_json(str(content))


def _search_web(query: str, *, max_results: int = 6) -> list[dict[str, str]]:
    """Search the public web for alternative products / approaches."""
    settings = get_settings()
    if settings.llm_provider == "stub" or not settings.market_research_web_enabled:
        return _stub_results(query)

    try:
        from ddgs import DDGS

        rows: list[dict[str, str]] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                rows.append(
                    {
                        "title": str(item.get("title") or "").strip(),
                        "url": str(item.get("href") or item.get("link") or "").strip(),
                        "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                    }
                )
        return rows
    except Exception:  # noqa: BLE001
        logger.exception("Web search failed for query=%r; falling back to stub results", query)
        return _stub_results(query)


def _stub_results(query: str) -> list[dict[str, str]]:
    q = query.strip() or "software"
    return [
        {
            "title": f"Open-source alternative related to: {q[:60]}",
            "url": "https://example.com/open-source-alternative",
            "snippet": (
                "Popular OSS option covering core workflows with plugins/extensions. "
                "Strong community; limited enterprise polish."
            ),
        },
        {
            "title": f"SaaS category leader for: {q[:60]}",
            "url": "https://example.com/saas-leader",
            "snippet": (
                "Managed commercial product with fast time-to-value, usage-based pricing, "
                "and weaker deep customization."
            ),
        },
        {
            "title": f"Build-vs-buy analysis notes: {q[:60]}",
            "url": "https://example.com/build-vs-buy",
            "snippet": (
                "Guidance: buy when undifferentiated; build when workflow, data model, or "
                "compliance requirements are a durable advantage."
            ),
        },
    ]


def _plan_search_queries(business_spec: str) -> list[str]:
    try:
        planned = _invoke_json(
            system=(
                "You plan web searches to find popular existing alternatives to a software idea.\n"
                "Return JSON: {\"queries\": string[]} with 2-4 short search queries "
                "(product category + alternatives / competitors / open source)."
            ),
            user=f"Business specification:\n\n{business_spec}\n",
        )
        queries = [str(q).strip() for q in (planned.get("queries") or []) if str(q).strip()]
        if queries:
            return queries[:4]
    except Exception:  # noqa: BLE001
        logger.exception("Failed to plan market-research queries")
    return [
        "alternatives to custom software for this workflow",
        "open source tools competing with this product idea",
        "SaaS products in this category build vs buy",
    ]


def generate_market_evaluation_report(business_spec: str) -> dict[str, Any]:
    """
    Research popular alternatives and produce a markdown evaluation report.

    Returns keys: report_markdown, grade, summary, sources
    """
    spec = maybe_compact_business_spec(business_spec)
    queries = _plan_search_queries(spec)

    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for query in queries:
        for row in _search_web(query, max_results=5):
            url = row.get("url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            sources.append({**row, "query": query})
            if len(sources) >= 12:
                break
        if len(sources) >= 12:
            break

    sources_blob = "\n\n".join(
        (
            f"- Title: {s.get('title')}\n"
            f"  URL: {s.get('url')}\n"
            f"  Query: {s.get('query')}\n"
            f"  Snippet: {s.get('snippet')}"
        )
        for s in sources
    ) or "(No web sources available — reason from general market knowledge and mark uncertainty.)"

    evaluation = _invoke_json(
        system=(
            "You are the Architect agent's market evaluator.\n"
            "Compare the user's approved business specification against popular existing "
            "alternatives discovered from web search results.\n"
            "Be practical and opinionated. Cite source titles/URLs when used.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "grade": "A" | "B" | "C" | "D" | "F",\n'
            '  "grade_rationale": string,\n'
            '  "summary": string,\n'
            '  "report_markdown": string\n'
            "}\n\n"
            "report_markdown MUST be a complete markdown document with these sections:\n"
            "# Market Evaluation Report\n"
            "## Idea grade\n"
            "## Executive summary\n"
            "## Popular alternatives found\n"
            "## Comparison vs your spec\n"
            "## When you should use an existing alternative\n"
            "## When you should build your own\n"
            "## Risks if you build\n"
            "## Recommended next step\n"
            "## Sources\n"
        ),
        user=(
            f"Approved business specification:\n\n{spec}\n\n"
            f"Web search findings:\n\n{sources_blob}\n"
        ),
    )

    report = str(evaluation.get("report_markdown") or "").strip()
    grade = str(evaluation.get("grade") or "C").strip().upper()[:1] or "C"
    summary = str(evaluation.get("summary") or evaluation.get("grade_rationale") or "").strip()
    if not report:
        report = (
            "# Market Evaluation Report\n\n"
            f"## Idea grade\n\n**{grade}** — {summary or 'Insufficient data.'}\n\n"
            "## Popular alternatives found\n\n"
            + "\n".join(
                f"- [{s.get('title') or 'Source'}]({s.get('url')}) — {s.get('snippet')}"
                for s in sources
            )
            + "\n"
        )
    return {
        "report_markdown": report,
        "grade": grade,
        "summary": summary
        or f"Market evaluation complete. Idea grade: {grade}.",
        "sources": sources,
    }
