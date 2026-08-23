"""LLD vs HLD topology signals from user text and living specs."""

from __future__ import annotations

import re

_STANDALONE_RES = (
    r"stand[\s-]?alone",
    r"self[\s-]?contained",
    r"local(?:ly)?[\s-]+(?:only|hosted|host|install(?:ation)?|desktop)",
    r"local(?:\s+self[\s-]?contained)?\s+(?:app|application)",
    r"desktop\s+app(?:lication)?",
    r"single[\s-]?(?:machine|device|host|computer|box)",
    r"offline[\s-]?first",
    r"not(?:\s+a)?\s+distributed",
    r"no(?:t)?\s+microservices?",
    r"single[\s-]+os[\s-]+process",
    r"single[\s-]+process",
    r"in-process",
    r"this is lld",
    r"should be lld",
    r"switch to lld",
    r"make this lld",
)

_DISTRIBUTED_RES = (
    r"\bmicroservices?\b",
    r"\bdistributed\b",
    r"\bmulti[\s-]?region\b",
    r"\bthis is hld\b",
    r"\bshould be hld\b",
    r"\bswitch to hld\b",
    r"\bmulti[\s-]?tenant\s+saas\b",
)

# Product / topology cues that mean HLD unless the user asked for stand-alone.
# Conservative or MVP scale does not flip these back to LLD.
_DISTRIBUTED_HINTS = (
    "microservice",
    "distributed",
    "multi-region",
    "multi-tenant",
    "kafka",
    "cdn",
    "api gateway",
    "youtube",
    "netflix",
    "video sharing",
    "video platform",
    "global market",
    "global users",
    "geographically",
)


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    blob = (text or "").strip().lower()
    if not blob:
        return False
    return any(re.search(pattern, blob) for pattern in patterns)


def wants_standalone(text: str) -> bool:
    """True when the user is routing to a local, self-contained, single-process app."""
    blob = (text or "").strip().lower()
    if not blob:
        return False
    cleaned = re.sub(
        r"(?:rather than|instead of|not(?:\s+a)?)\s+"
        r"(?:a\s+)?(?:local|stand[\s-]?alone|self[\s-]?contained|"
        r"single[\s-]+(?:os[\s-]+)?process|in-process)[\w\s-]{0,40}",
        " ",
        blob,
    )
    return _matches(_STANDALONE_RES, cleaned)


def wants_distributed(text: str) -> bool:
    """True when the user is explicitly asking for a distributed / HLD topology."""
    return _matches(_DISTRIBUTED_RES, text)


def looks_distributed(text: str) -> bool:
    """True when the text describes a distributed / HLD topology (not stand-alone)."""
    if wants_standalone(text):
        return False
    if wants_distributed(text):
        return True
    blob = (text or "").lower()
    return any(token in blob for token in _DISTRIBUTED_HINTS)


def spec_locks_standalone(spec: str) -> bool:
    """True when the living spec has an explicit local/stand-alone topology lock."""
    lower = (spec or "").lower()
    if "## deployment topology" not in lower:
        return False
    section = lower.split("## deployment topology", 1)[1]
    section = section.split("## ", 1)[0]
    return wants_standalone(section) or "not distributed" in section


def resolve_design_track(
    llm_track: str,
    *,
    pending: str = "",
    spec: str = "",
    prior: str = "unset",
    context: str = "",
) -> str:
    """Pick lld/hld. Explicit stand-alone language wins over a prior HLD call."""
    llm = (llm_track or "unset").strip().lower()
    if llm not in {"unset", "lld", "hld"}:
        llm = "unset"
    prior_n = (prior or "unset").strip().lower()
    if prior_n not in {"unset", "lld", "hld"}:
        prior_n = "unset"
    evidence = f"{pending}\n{spec}\n{context}"

    if wants_standalone(pending):
        return "lld"
    if wants_distributed(pending):
        return "hld"
    if spec_locks_standalone(spec):
        return "lld"
    if prior_n == "unset" and wants_standalone(spec):
        return "lld"
    # A YouTube-like / global / distributed spec stays HLD even if the model
    # returns lld (common on "looks good" after it already argued HLD in chat).
    if looks_distributed(evidence) and not wants_standalone(pending):
        return "hld"
    if prior_n in {"lld", "hld"}:
        return prior_n
    if llm in {"lld", "hld"}:
        return llm
    return "unset"


def recommend_design_track(
    *,
    pending: str = "",
    spec: str = "",
    prior: str = "unset",
    context: str = "",
) -> str:
    """Pick lld or hld when the model left the track unset. Never returns unset."""
    resolved = resolve_design_track(
        "unset", pending=pending, spec=spec, prior=prior, context=context
    )
    if resolved in {"lld", "hld"}:
        return resolved
    blob = f"{pending}\n{spec}\n{context}".lower()
    if looks_distributed(blob):
        return "hld"
    if wants_standalone(spec) or any(
        token in blob
        for token in ("single os process", "single process", "in-process", "(lld)")
    ) or re.search(r"\bcli\b", blob):
        return "lld"
    if any(token in blob for token in ("saas", "warehouse", "inventory", "platform")):
        return "hld"
    return "lld"


def ensure_classified_topology(spec: str, track: str) -> str:
    """Record the chosen LLD/HLD topology, replacing a still-open placeholder."""
    body = spec or ""
    if track not in {"lld", "hld"}:
        return body
    from architect_agent.design_diagram import extract_spec_section
    from architect_agent.interview_progress import append_spec_bullet

    section = extract_spec_section(body, "Deployment topology").lower()
    if track == "lld":
        if section and "to be classified" not in section and (
            "lld" in section or "single os process" in section or "stand-alone" in section
        ):
            return body
        note = (
            "Local / single OS process (LLD). Not a distributed microservice "
            "topology unless later revised."
        )
    else:
        if section and "to be classified" not in section and (
            "hld" in section or "distributed" in section
        ):
            return body
        note = (
            "Distributed topology (HLD): multiple services/nodes as required by v1. "
            "Not a single-process local app unless later revised."
        )
    return append_spec_bullet(body, "## Deployment topology", note)


def track_reclass_notice(prior: str, new: str) -> str:
    """User-facing note when topology classification changes."""
    if prior == "hld" and new == "lld":
        return (
            "You asked for a **local self-contained stand-alone** design, so this is "
            "**LLD** (one process), not a distributed HLD. I will not keep proposing "
            "microservices, Kafka, CDN, or multi-region topology."
        )
    if prior == "lld" and new == "hld":
        return (
            "You asked for a **distributed** design, so this is **HLD**, not a "
            "single-process LLD."
        )
    return ""


def ensure_standalone_spec(spec: str, pending: str) -> str:
    """Record a locked local topology on the living spec when the user asks for it."""
    body = spec or ""
    if not wants_standalone(pending):
        return body
    lower = body.lower()
    if any(
        token in lower
        for token in ("stand-alone", "standalone", "self-contained", "single os process")
    ):
        return body
    return (
        body.rstrip()
        + "\n\n## Deployment topology\n"
        "- Local self-contained stand-alone application (single OS process / LLD). "
        "Not distributed; no microservices, Kafka, CDN, or multi-region requirement.\n"
    )
