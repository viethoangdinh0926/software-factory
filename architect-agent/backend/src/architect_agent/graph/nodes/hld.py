from __future__ import annotations

import re
from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    format_phase_context,
    maybe_compact_business_spec,
    maybe_compact_design_justification,
    refresh_discussion_digest,
)
from architect_agent.graph.nodes.common import (
    HLD_STEP_TITLES,
    answer_before_approve,
    approve_label,
    ensure_step_briefing,
    gate_user_chat,
    invoke_json,
    is_design_approve_step,
)
from architect_agent.design_progress import (
    KEEP_AND_PATCH_RULES,
    keep_or_patch,
    rewind_or_block_skip,
    should_keep_and_patch,
    with_rewind_notice,
)
from architect_agent.design_diagram import (
    apply_diagram_catalogs,
    catalog_covers_diagram,
    diagram_is_concrete,
    ensure_design_diagram,
    extract_spec_section,
    upsert_spec_section,
    with_diagram_walkthrough,
)
from architect_agent.interview_progress import scrub_control_phrases_from_spec
from architect_agent.graph.state import DesignGraphState
from architect_agent.json_util import coerce_artifact_markdown, coerce_diagram_text
from architect_agent.mermaid_sanitize import sanitize_mermaid
from architect_agent.query_intent import (
    FEEDBACK_RESOLUTION_RULES,
    user_message_first_block,
    is_advance_request,
    is_informational_query,
    promote_chat_to_approve,
    resolve_wait_action,
    with_next_prompt,
    with_resolution_close,
)
from architect_agent.workflow import workflow_prompt_block

_STEP_TITLES = HLD_STEP_TITLES

_INFRA_HINTS = (
    "gateway",
    "load balancer",
    "loadbalancer",
    "lb",
    "auth",
    "identity",
    "redis",
    "cache",
    "kafka",
    "rabbit",
    "queue",
    "broker",
    "cdn",
    "postgres",
    "mysql",
    "dynamo",
    "cassandra",
    "s3",
    "blob",
    "object storage",
    "elasticsearch",
    "opensearch",
    "transcod",
)

_SERVICE_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,3}\s*)?(?:Service:\s*)?([A-Z][A-Za-z0-9]+(?:Service)?)\b",
)


def _diagram_node_count(diagram: str) -> int:
    """Rough count of Mermaid node declarations / edge endpoints."""
    if not diagram.strip():
        return 0
    ids = set(re.findall(r"\b([A-Za-z][\w]*)\s*(?:\[|\(|\{)", diagram))
    if len(ids) >= 3:
        return len(ids)
    edge_ids = set(re.findall(r"\b([A-Za-z][\w]*)\b(?=\s*-->)", diagram))
    edge_ids |= set(re.findall(r"-->\s*([A-Za-z][\w]*)\b", diagram))
    return max(len(ids), len(edge_ids))


def _diagram_is_concrete(diagram: str, api_contracts: str) -> bool:
    """Reject shallow concept pipelines; require infra + service-level detail."""
    text = (diagram or "").lower()
    if _diagram_node_count(diagram) < 10:
        return False
    infra_hits = sum(1 for hint in _INFRA_HINTS if hint in text)
    if infra_hits < 3:
        return False
    service_names = re.findall(r"\b([A-Z][A-Za-z]+Service)\b", api_contracts or "")
    if len(service_names) >= 3:
        present = sum(1 for name in service_names if name.lower() in text)
        stems = [n[: -len("Service")].lower() for n in service_names if n.endswith("Service")]
        present += sum(1 for stem in stems if stem and stem in text)
        if present < 2:
            return False
    vague = ("processing pipeline", "user interaction", "storage/metadata")
    if sum(1 for v in vague if v in text) >= 2 and infra_hits < 4:
        return False
    return True


def _scale_estimates_are_concrete(text: str) -> bool:
    """Step 1 depth bar: quantitative capacity plan, not a one-liner."""
    body = (text or "").strip()
    if len(body) < 280:
        return False
    lower = body.lower()
    topics = (
        "dau",
        "qps",
        "read",
        "write",
        "storage",
        "bandwidth",
        "egress",
        "latency",
        "sla",
        "availability",
        "concurrent",
    )
    topic_hits = sum(1 for t in topics if t in lower)
    if topic_hits < 4:
        return False
    # Need several numeric figures (5k, 100000, 4 Mbps, 99.9%, etc.).
    numbers = re.findall(r"\d+(?:[.,]\d+)?\s*(?:[%kKmMbBgGtTpP]|(?:mbps|gbps|tb|gb|ms|qps))?", body)
    if len(numbers) < 4:
        return False
    # Reject single-paragraph summaries without structure.
    bulletish = len(re.findall(r"(?m)^\s*[-*|]|\bDAU\b|\bQPS\b", body))
    if bulletish < 3 and "\n" not in body:
        return False
    return True


def _core_services_are_concrete(text: str) -> bool:
    """Step 3 depth bar: headed services with owned objects and operations — not HTTP APIs."""
    body = (text or "").strip()
    if len(body) < 500:
        return False
    lower = body.lower()
    if (
        "are defined via rest" in lower
        or "ensuring clear service boundaries" in lower
    ) and len(body) < 900:
        return False
    services = {m.group(1) for m in _SERVICE_HEADER_RE.finditer(body)}
    services |= set(re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", body))
    if len(services) < 3:
        return False
    ops = len(
        re.findall(
            r"(?i)\b(own|owns|owned|responsib|operat|command|query|bounded context)\b",
            body,
        )
    )
    bullets = len(re.findall(r"(?m)^\s*[-*]", body))
    return bullets >= 6 or ops >= 4


def _comms_are_concrete(text: str) -> bool:
    """Step 4 depth bar: named protocols across user, service, and infra planes."""
    body = (text or "").strip().lower()
    if len(body) < 400:
        return False
    styles = sum(
        1
        for k in (
            "rest",
            "grpc",
            "graphql",
            "websocket",
            "kafka",
            "pubsub",
            "pub/sub",
            "pub-sub",
            "queue",
            "stream",
            "event",
            "request/response",
            "request-response",
        )
        if k in body
    )
    planes = sum(
        1
        for k in ("user", "client", "gateway", "service", "database", "cdn", "broker")
        if k in body
    )
    return styles >= 2 and planes >= 3


def fmea_notes_are_concrete(text: str) -> bool:
    """Step 5 depth bar: multiple failure modes with mitigations."""
    body = (text or "").strip()
    if len(body) < 450:
        return False
    lower = body.lower()
    mode_hits = sum(
        1
        for k in (
            "spof",
            "single point",
            "bottleneck",
            "race",
            "split-brain",
            "split brain",
            "failover",
            "partition",
            "backpressure",
            "poison",
            "hot partition",
        )
        if k in lower
    )
    if mode_hits < 3:
        return False
    mitigation_hits = sum(
        1
        for k in (
            "mitigat",
            "replica",
            "failover",
            "autoscal",
            "retry",
            "circuit",
            "rate limit",
            "backoff",
            "multi-cdn",
            "shard",
            "quarantine",
        )
        if k in lower
    )
    if mitigation_hits < 3:
        return False
    rows = len(re.findall(r"(?m)^\s*[-*|]|\bFailure\b|\bMode\b|\bRisk\b", body))
    if rows < 3 and body.count("\n") < 4:
        return False
    return True


def fallback_fmea_notes(*, apis: str = "", comms: str = "") -> str:
    """Concrete FMEA when the model left `fmea_notes` empty or shallow."""
    del comms
    names = re.findall(r"(?im)^###?\s+([A-Z][\w /.-]*Service)", apis or "")
    catalog = names[0] if names else "the primary metadata store"
    worker = next((n for n in names if "content" in n.lower() or "media" in n.lower()), "transcoder workers")
    return (
        "### FMEA\n"
        "| Failure mode | Impact | Mitigation |\n"
        "|---|---|---|\n"
        f"| SPOF: primary Postgres / {catalog} | Catalog or auth outage | Sync replicas + automated failover; reads on replicas |\n"
        f"| Bottleneck: {worker} / ingest backlog | Upload-to-playable delay | Priority queues + worker autoscaling + degraded lower-res publish |\n"
        "| Race: concurrent metadata edits | Lost title/visibility updates | Conditional writes / etags on the owning service |\n"
        "| Split-brain: dual-writer cache | Stale authz or wrong entitlement | Single-writer keys; sticky cache ownership |\n"
        "| CDN origin overload | Playback errors at peak | Multi-CDN + origin shield + high cache TTLs |\n"
        "| Hot partition / poison message | One shard or consumer stalls | Partition keys by id hash; quarantine + retry with backoff |\n"
    )


_HLD_PRIMARY_FIELD = {
    1: "scale_estimates",
    2: "updated_business_spec (domain model section) + tradeoff_ledger",
    3: "core_microservices",
    4: "communication_schemes + design_diagram_lines",
    5: "fmea_notes",
    6: "design_justification",
}

_VIDEO_DOMAIN_HINTS = (
    "video",
    "youtube",
    "watch",
    "upload",
    "stream",
    "channel",
    "creator",
    "tiktok",
)

_ENTITY_HEADING_RE = re.compile(
    r"(?m)^(?:###\s+|\*\*|[-*]\s+\*\*)([A-Z][A-Za-z0-9]+)"
)
_RESERVED_ENTITY_NAMES = {
    "domain",
    "entity",
    "entities",
    "attributes",
    "relationships",
    "ownership",
    "model",
}


def listed_domain_entities(body: str) -> list[str]:
    """Entity names from ### headings or bold list items in a domain-model section."""
    names: list[str] = []
    seen: set[str] = set()
    for match in _ENTITY_HEADING_RE.finditer(body or ""):
        name = match.group(1)
        key = name.lower()
        if key in _RESERVED_ENTITY_NAMES or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def domain_model_is_concrete(spec: str) -> bool:
    """Step 2 depth bar: ≥5 named entities with attributes or relationships."""
    section = extract_spec_section(spec, "Domain model") or ""
    body = section or (spec or "")
    if not section and "## problem" in body.lower():
        return False
    entities = listed_domain_entities(body)
    if len(entities) < 5:
        return False
    lower = body.lower()
    rel_hits = sum(
        1
        for token in ("1:n", "1:1", "n:m", "n:1", "owns", "relationship", "belongs")
        if token in lower
    )
    attr_hits = lower.count("attribute") + len(re.findall(r"\bid\b", lower))
    return rel_hits >= 1 or attr_hits >= 3


def fallback_domain_model(spec: str, scale: str = "") -> str:
    """Deterministic domain catalog when the model did not write ## Domain model."""
    blob = f"{spec}\n{scale}".lower()
    if any(hint in blob for hint in _VIDEO_DOMAIN_HINTS):
        entities = (
            (
                "User",
                "id, handle, email, created_at, status",
                "1:N Channel (owns); 1:N WatchEvent; 1:N Subscription; 1:N Comment",
            ),
            (
                "Channel",
                "id, owner_user_id, name, description, created_at",
                "N:1 User; 1:N Video; 1:N Subscription",
            ),
            (
                "Video",
                "id, channel_id, title, description, visibility, duration_s, created_at",
                "N:1 Channel; 1:N TranscodeJob; 1:N WatchEvent; 1:N Comment",
            ),
            (
                "TranscodeJob",
                "id, video_id, rendition, status, created_at",
                "N:1 Video",
            ),
            (
                "WatchEvent",
                "id, user_id, video_id, position_s, watched_at",
                "N:1 User; N:1 Video",
            ),
            (
                "Subscription",
                "id, subscriber_user_id, channel_id, created_at",
                "N:1 User; N:1 Channel",
            ),
            (
                "Comment",
                "id, video_id, author_user_id, body, created_at",
                "N:1 Video; N:1 User",
            ),
        )
    else:
        entities = (
            (
                "User",
                "id, handle, email, created_at, status",
                "1:N Session; 1:N Account",
            ),
            (
                "Session",
                "id, user_id, issued_at, expires_at",
                "N:1 User",
            ),
            (
                "Account",
                "id, owner_user_id, plan, created_at",
                "N:1 User; 1:N Resource",
            ),
            (
                "Resource",
                "id, account_id, title, status, created_at",
                "N:1 Account; 1:N AuditEvent",
            ),
            (
                "AuditEvent",
                "id, actor_user_id, resource_id, action, created_at",
                "N:1 User; N:1 Resource",
            ),
            (
                "Notification",
                "id, user_id, kind, body, created_at",
                "N:1 User",
            ),
        )
    lines = [
        "Entities, attributes, and ownership for later bounded-context splits "
        "(labeled assumptions from the living spec).",
        "",
    ]
    for name, attrs, rels in entities:
        lines.extend(
            [
                f"### {name}",
                f"- Attributes: {attrs}",
                f"- Relationships: {rels}",
                f"- Owner: {name} aggregate (split into a service in the next step)",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def ensure_domain_model(spec: str, scale: str = "", ledger: str = "") -> str:
    """Guarantee a ## Domain model section exists before the step can brief or advance."""
    living = spec or ""
    if domain_model_is_concrete(living):
        return living
    ledger_body = (ledger or "").strip()
    if ledger_body and domain_model_is_concrete(f"## Domain model\n\n{ledger_body}"):
        return upsert_spec_section(living, "Domain model", ledger_body)
    return upsert_spec_section(living, "Domain model", fallback_domain_model(living, scale))


def _merge_domain_into_spec(updated: str, prior: str) -> str:
    """Keep the living spec when the model returns only a domain-model fragment."""
    raw = (updated or "").strip()
    base = prior or ""
    if not raw:
        return base
    domain = extract_spec_section(raw, "Domain model")
    if domain and "## problem" not in raw.lower() and base.lower().count("##") >= 3:
        return upsert_spec_section(base, "Domain model", domain)
    if domain_model_is_concrete(raw) and "## problem" not in raw.lower() and base.lower().count("##") >= 3:
        return upsert_spec_section(base, "Domain model", raw)
    return raw


def _step_artifact_rules(step: int) -> str:
    primary = _HLD_PRIMARY_FIELD.get(step, "")
    header = (
        f"\nTHIS TURN primary field: {primary}. "
        "You MUST populate it in full (never \"\"). "
        "Leave other large fields as \"\" / design_diagram_lines as [] unless this "
        "step's primary field is the diagram.\n"
    )
    if step == 1:
        return header + (
            "HLD Step 1 — write scale_estimates NOW with labeled assumptions "
            "(do not wait for the user to supply QPS):\n"
            "- Markdown bullets/table with numeric: DAU or MAU, peak concurrent users, "
            "read QPS, write/upload QPS, storage growth, egress/bandwidth, "
            "latency (p99) and availability SLA.\n"
            "- Include working assumptions (bitrate, session length, cache hit ratio).\n"
            "- If the product is YouTube-like, assume large-scale defaults and label them.\n"
            "- assistant_message: per CHAT DEPTH, walk through how you derived each number "
            "(the arithmetic chain from DAU → peak concurrency → QPS), which assumptions "
            "drive it most, how sensitive the design is if they are wrong, and what these "
            "numbers rule in/out for later steps. Then ask them to approve if they "
            "have no other concerns. Never tell them to click a button. Optional one ❓.\n"
            "Example scale_estimates snippet:\n"
            "- DAU: 50,000,000 (assumed YouTube-like; labeled assumption)\\n"
            "- Peak concurrent viewers: 5,000,000\\n"
            "- Peak read QPS: 120,000\\n- Peak write/upload QPS: 8,000\\n"
        )
    if step == 2:
        return header + (
            "HLD Step 2 — write a domain model into updated_business_spec "
            "(## Domain model section) and tradeoff_ledger ownership notes:\n"
            "- Entities, key attributes, relationships (1:1 / 1:N / N:M), ownership.\n"
            "- Do not skip to microservices. ready_to_advance=true when ≥5 entities are listed.\n"
        )
    if step == 3:
        return header + (
            "HLD Step 3 — write core_microservices NOW as headed service descriptions:\n"
            "- Reason about bounded contexts from the domain model. Group one or more "
            "domain objects under each service that will own their operations.\n"
            "- ≥3 headed *Service names. Each service: owned objects, operations/"
            "responsibilities, and peer collaborators. No METHOD /path, OpenAPI, or "
            "payload schemas — those would lock a protocol too early. This is NOT an API step.\n"
            "- NEVER a one-sentence 'REST/JSON boundaries' summary.\n"
            "Example:\n"
            "### IdentityService\\nOwns User, Session, Credential. Operations: register, "
            "login, refresh, profile read. Collaborators: gateway + peers for authz.\\n"
            "### VideoCatalogService\\nOwns Video, VisibilityPolicy. Operations: register "
            "upload, read metadata, update title/visibility.\\n"
        )
    if step == 4:
        return header + (
            "HLD Step 4 — write communication_schemes AND design_diagram_lines NOW.\n"
            "communication_schemes must cover three planes with named styles "
            "(request/response, stream, pub/sub, etc.):\n"
            "- User ↔ system (gateway, CDN, client protocols).\n"
            "- Core microservice ↔ core microservice (sync vs async).\n"
            "- Core microservice ↔ infra (API gateway, databases, CDN, brokers, object storage).\n"
            "Do NOT write HTTP METHOD /path catalogs; name the scheme/protocol only.\n"
            "Diagram: 12–25 nodes, not a concept pipeline. Required kinds: Client, "
            "LoadBalancer, APIGateway, Auth/IdentityService, each named *Service from "
            "api_contracts, Redis, Kafka, Elasticsearch, CDN, Postgres, ObjectStorage/S3. "
            "Edges for sync vs async. Keep arrow labels SHORT (HTTPS, gRPC, Kafka). "
            "For EVERY arrow, write a ## Diagram relationships section in "
            "updated_business_spec with one ### StartId → EndId heading and 2-4 sentences: "
            "what flows, protocol, why that coupling, what fails if the hop is down. "
            "Those sentences are the hover popup on that line in the UI. "
            "Use design_diagram_lines only; set design_diagram to \"\".\n"
            "Example comms snippet:\n"
            "### User ↔ system\\nHTTPS request/response via API Gateway; playback via CDN HLS stream.\\n"
            "### Service ↔ service\\nSync gRPC for authz; Kafka pub/sub for VideoPublished.\\n"
        )
    if step == 5:
        return header + (
            "HLD Step 5 — write fmea_notes NOW as structured rows (never leave empty).\n"
            "≥4 modes covering SPOF, bottleneck, race, split-brain; each Impact + Mitigation.\n"
            "Example:\n"
            "### FMEA\\n"
            "- SPOF: primary Postgres — Impact: catalog outage. Mitigation: replicas+failover.\\n"
            "- Bottleneck: transcoder backlog — Impact: publish delay. Mitigation: autoscale workers.\\n"
            "- Race: concurrent metadata edits — Impact: lost updates. Mitigation: etags.\\n"
            "- Split-brain: dual-writer cache — Impact: stale authz. Mitigation: single-writer keys.\\n"
        )
    if step == 6:
        return header + (
            "HLD Step 6 — write design_justification synthesis (stack, CAP, residual risks). "
            "Set design_ready_to_approve=true. Ask them to approve if they have no other concerns "
            "before sending the design. Never tell them to click a button.\n"
            "When writing design_justification, you MUST explain the functionality of each component "
            "from the design diagram. For each major component (API Gateway, microservices, databases, "
            "CDN, message queue, etc.), describe what it does, its role in the system, and how it "
            "interacts with other components. Structure this as clear sections with component names "
            "as headers or bullet points.\n"
        )
    return header


def _apply_depth_gates(
    *,
    step: int,
    ready_advance: bool,
    result: dict[str, Any],
    scale: str,
    apis: str,
    comms: str,
    fmea: str,
    diagram: str,
    spec: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Block advance when artifacts are shallow; unlock when the depth bar is met."""
    hint = ""
    if step == 1:
        if _scale_estimates_are_concrete(scale):
            ready_advance = True
        else:
            ready_advance = False
            hint = (
                "Scale estimates are still too brief. Expand `scale_estimates` into a structured "
                "capacity plan with numeric DAU/concurrent users, read/write QPS, storage, "
                "bandwidth/egress, and latency/availability targets before we advance."
            )
    elif step == 2:
        if domain_model_is_concrete(spec):
            ready_advance = True
        else:
            ready_advance = False
            hint = (
                "The domain model is still too brief. Write a `## Domain model` section with "
                "at least five entities, each with attributes and relationships "
                "(1:1 / 1:N / N:M), before we split them into services."
            )
    elif step == 3:
        if _core_services_are_concrete(apis):
            ready_advance = True
        else:
            ready_advance = False
            hint = (
                "Core microservices are still too brief. Expand `core_microservices` with headed "
                "*Service descriptions that list owned domain objects and operations "
                "(not HTTP METHOD /path catalogs) before we advance."
            )
    elif step == 4:
        diagram_ok = _diagram_is_concrete(diagram, apis)
        comms_ok = _comms_are_concrete(comms)
        if diagram_ok and comms_ok:
            ready_advance = True
        else:
            ready_advance = False
            if not comms_ok and not diagram_ok:
                hint = (
                    "Communication schemes and the system diagram are still too high-level. "
                    "Name protocols for user↔system, service↔service, and service↔infra "
                    "(request/response, stream, pub/sub), and expand the diagram to show "
                    "API gateway/load balancer, auth, each business microservice, caches, "
                    "brokers, CDN, and distinct storage systems before we advance."
                )
            elif not comms_ok:
                hint = (
                    "Communication schemes are still too brief. Expand `communication_schemes` "
                    "to name protocols for user↔system, service↔service, and service↔infra "
                    "(request/response, stream, pub/sub) before we advance."
                )
            else:
                hint = (
                    "The system diagram is still too high-level. Expand it to show "
                    "API gateway/load balancer, auth, each business microservice, "
                    "caches, brokers, CDN, and distinct storage systems before we advance."
                )
    elif step == 5:
        if fmea_notes_are_concrete(fmea):
            ready_advance = True
        else:
            ready_advance = False
            hint = (
                "FMEA notes are still too brief. Expand `fmea_notes` into structured failure "
                "modes (SPOFs, bottlenecks, races, split-brain) each with impact and mitigation "
                "before we advance."
            )

    if hint:
        prior = (result.get("assistant_message") or "").lower()
        # Avoid clobbering a message that already explains the same gap.
        if not any(k in prior for k in ("too brief", "too high-level", "expand", "concrete")):
            result = {**result, "assistant_message": hint}
        elif not (result.get("assistant_message") or "").strip():
            result = {**result, "assistant_message": hint}
    return ready_advance, result


def _prefer_richer_text(new: str, old: str) -> str:
    """Keep prior artifact when the model returns empty or a truncated scrap."""
    new_s = (new or "").strip()
    old_s = (old or "").strip()
    if not new_s:
        return old_s
    if not old_s:
        return new_s
    if len(new_s) >= int(len(old_s) * 0.75):
        return new_s
    # Dramatic shrink usually means truncation / overwrite — keep the richer prior.
    if len(old_s) >= 200 and len(new_s) < int(len(old_s) * 0.5):
        return old_s
    return new_s


def _prefer_diagram(new: str, old: str, apis: str) -> str:
    new_s = (new or "").strip()
    old_s = (old or "").strip()
    if not new_s:
        return old_s
    if not old_s:
        return new_s
    new_ok = _diagram_is_concrete(new_s, apis)
    old_ok = _diagram_is_concrete(old_s, apis)
    if new_ok:
        return new_s
    if old_ok and not new_ok:
        return old_s
    if _diagram_node_count(new_s) >= _diagram_node_count(old_s):
        return new_s
    return old_s


def hld_step_node(state: DesignGraphState) -> dict[str, Any]:
    """Run the current HLD step (1–6)."""
    step = max(1, min(6, int(state.get("design_step") or 1)))
    business_spec = maybe_compact_business_spec(state.get("business_spec") or "")
    pending = (state.get("pending_user_feedback") or "").strip()
    prior = [m for m in (state.get("messages") or []) if m.get("node") == "hld"]
    history_tail = format_phase_context(
        str(state.get("discussion_digest") or ""), prior, "hld"
    )
    step_rules = _step_artifact_rules(step)
    keep_mode = should_keep_and_patch(state, step)
    carry = str(state.get("carry_change") or "").strip()
    keep_block = KEEP_AND_PATCH_RULES if keep_mode else ""

    result = invoke_json(
        system=(
            "You are the Architect agent's HLD track node (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            f"{workflow_prompt_block('hld', 'hld', step)}"
            f"Current HLD step: {step} — {_STEP_TITLES.get(step, '')}.\n"
            f"{user_message_first_block(pending)}"
            f"{keep_block}"
            "Fill this step's primary artifact completely on this turn using labeled "
            "assumptions. Ask them to approve if they have no other concerns. Never tell them to click a button.\n"
            "If the user just commented, address that comment before restating the step.\n"
            "Do not skip steps unless the user explicitly directs it.\n"
            "If DISCUSSION MEMORY locks a local stand-alone topology, do not propose "
            "Kafka, CDN, multi-region, or new microservices — that session belongs on LLD.\n"
            f"{step_rules}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "updated_business_spec": string,\n'
            '  "tradeoff_ledger": string,\n'
            '  "scale_estimates": string,\n'
            '  "core_microservices": string,\n'
            '  "communication_schemes": string,\n'
            '  "fmea_notes": string,\n'
            '  "design_diagram_lines": [string, ...],\n'
            '  "design_diagram": string,\n'
            '  "design_justification": string,\n'
            '  "ready_to_advance": boolean,\n'
            '  "design_ready_to_approve": boolean,\n'
            '  "assistant_message": string\n'
            "}\n"
            "If you rewrite the primary field, it must meet the depth bar so "
            "ready_to_advance can be true. Empty primary field is a failure "
            "unless a keep-existing-artifacts instruction is in force.\n"
            "assistant_message MUST brief what this step completed: name the numbers, "
            "objects, services, protocols, or failure modes you wrote, why, what you "
            "rejected, and what it costs. Never write a status line such as "
            "\"HLD step 1 update.\" or \"Here is the diagram.\"\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
        ),
        user=(
            f"Living specification:\n\n{business_spec}\n\n"
            f"Trade-off ledger:\n{state.get('tradeoff_ledger') or '(empty)'}\n\n"
            f"Scale estimates:\n{state.get('scale_estimates') or '(empty)'}\n\n"
            f"Core microservices:\n{state.get('api_contracts') or '(empty)'}\n\n"
            f"Communication schemes:\n{state.get('communication_schemes') or '(empty)'}\n\n"
            f"FMEA notes:\n{state.get('fmea_notes') or '(empty)'}\n\n"
            f"Current diagram:\n{state.get('design_diagram') or '(none)'}\n\n"
            f"Current justification:\n{state.get('design_justification') or '(none)'}\n\n"
            f"{history_tail}\n\n"
            f"Latest user message:\n{pending or '(none — produce the step artifact from the spec using labeled assumptions)'}\n"
            f"Carry-forward change to apply if this step is affected:\n{carry or '(none)'}\n"
            f"Reminder: primary field this turn is {_HLD_PRIMARY_FIELD.get(step)}. "
            "Do not leave it empty unless a keep-existing-artifacts instruction is in force."
        ),
    )

    prior_apis = str(state.get("api_contracts") or "")
    prior_comms = str(state.get("communication_schemes") or "")
    prior_scale = str(state.get("scale_estimates") or "")
    prior_fmea = str(state.get("fmea_notes") or "")
    prior_ledger = str(state.get("tradeoff_ledger") or "")
    prior_diagram = str(state.get("design_diagram") or "")

    picker = keep_or_patch if keep_mode else _prefer_richer_text
    scale = picker(coerce_artifact_markdown(result.get("scale_estimates")), prior_scale)
    apis = picker(
        coerce_artifact_markdown(
            result.get("core_microservices") or result.get("api_contracts")
        ),
        prior_apis,
    )
    comms = picker(coerce_artifact_markdown(result.get("communication_schemes")), prior_comms)
    fmea = picker(coerce_artifact_markdown(result.get("fmea_notes")), prior_fmea)
    if step == 5 and not fmea_notes_are_concrete(fmea):
        fmea = fallback_fmea_notes(apis=apis, comms=comms)
    ledger = picker(coerce_artifact_markdown(result.get("tradeoff_ledger")), prior_ledger)

    new_diagram = sanitize_mermaid(
        coerce_diagram_text(result, fallback="")
    )
    if keep_mode:
        diagram = sanitize_mermaid(keep_or_patch(new_diagram, prior_diagram))
    else:
        diagram = sanitize_mermaid(_prefer_diagram(new_diagram, prior_diagram, apis))
    if step >= 4 and not diagram_is_concrete(diagram, minimum=8):
        diagram = ensure_design_diagram(
            business_spec,
            diagram or prior_diagram,
            track="hld",
            allow_llm=True,
        )
    justification = maybe_compact_design_justification(
        keep_or_patch(
            str(result.get("design_justification") or ""),
            str(state.get("design_justification") or ""),
        )
        if keep_mode
        else str(result.get("design_justification") or state.get("design_justification") or "")
    )
    raw_spec = str(result.get("updated_business_spec") or "")
    if keep_mode:
        spec_out = keep_or_patch(raw_spec, business_spec)
    else:
        spec_out = _merge_domain_into_spec(raw_spec, business_spec)
    spec_out = scrub_control_phrases_from_spec(str(spec_out or business_spec))
    if step == 2:
        spec_out = ensure_domain_model(spec_out, scale, ledger)
    catalog = ""
    relationships = ""
    if step >= 4 and diagram_is_concrete(diagram, minimum=6):
        spec_out, catalog, relationships = apply_diagram_catalogs(
            diagram,
            str(spec_out),
            justification=justification,
            comms=comms,
            allow_llm=True,
        )
        if not catalog_covers_diagram(justification, diagram):
            justification = catalog

    ready_advance = bool(result.get("ready_to_advance"))
    design_ready = bool(result.get("design_ready_to_approve"))
    if step >= 6:
        design_ready = design_ready or bool(diagram.strip())
        ready_advance = design_ready

    ready_advance, result = _apply_depth_gates(
        step=step,
        ready_advance=ready_advance,
        result=result,
        scale=scale,
        apis=apis,
        comms=comms,
        fmea=fmea,
        diagram=diagram,
        spec=str(spec_out),
    )
    if keep_mode:
        ready_advance = True
        if step >= 6:
            design_ready = True
            ready_advance = True

    primary_field = {
        1: "scale_estimates",
        2: "business_spec",
        3: "api_contracts",
        4: "communication_schemes",
        5: "fmea_notes",
        6: "design_justification",
    }.get(step, "scale_estimates")
    assistant = ensure_step_briefing(
        str(result.get("assistant_message") or ""),
        track="hld",
        step=step,
        title=_STEP_TITLES.get(step, "High-level design"),
        artifacts={
            "business_spec": str(spec_out),
            "tradeoff_ledger": ledger,
            "scale_estimates": scale,
            "api_contracts": apis,
            "communication_schemes": comms,
            "fmea_notes": fmea,
            "design_diagram": diagram,
            "design_justification": catalog or justification,
        },
        primary_field="design_justification" if step == 4 and catalog else primary_field,
        pending=pending,
    )
    if catalog and step == 4:
        assistant = with_diagram_walkthrough(assistant, catalog, relationships)
    if pending:
        changed = (
            scale != prior_scale
            or apis != prior_apis
            or comms != prior_comms
            or fmea != prior_fmea
            or ledger != prior_ledger
            or diagram != prior_diagram
            or str(spec_out) != business_spec
        )
        assistant = with_resolution_close(str(assistant), changed=changed)
    assistant = with_rewind_notice(str(assistant), str(state.get("rewind_notice") or ""))
    digest = refresh_discussion_digest(
        str(state.get("discussion_digest") or ""),
        pending=pending,
        assistant=assistant,
        phase="hld",
        track="hld",
        spec=str(spec_out),
    )

    return {
        "phase": "hld",
        "design_track": "hld",
        "design_step": step,
        "business_spec": spec_out,
        "tradeoff_ledger": ledger,
        "scale_estimates": scale,
        "api_contracts": apis,
        "communication_schemes": comms,
        "fmea_notes": fmea,
        "design_diagram": diagram,
        "design_justification": justification,
        "ready_to_advance": ready_advance,
        "design_ready_to_approve": design_ready,
        "ready_for_design": design_ready,
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        "rewind_notice": "",
        "publish_requested": False,
        "stay_on_interrupt": False,
        "discussion_digest": digest,
        "messages": [{"role": "assistant", "content": assistant, "node": "hld"}],
    }


def hld_wait_node(state: DesignGraphState) -> dict[str, Any]:
    step = max(1, min(6, int(state.get("design_step") or 1)))
    ready = bool(state.get("ready_to_advance"))
    design_ready = bool(state.get("design_ready_to_approve"))
    design_approve = is_design_approve_step("hld", step) and design_ready
    assistant = with_next_prompt(
        state.get("pending_assistant_message") or "",
        approve_label=approve_label("hld", "hld", step, design_ready=design_ready),
        can_approve=ready or design_approve,
    )

    resume = interrupt(
        {
            "phase": "hld",
            "design_track": "hld",
            "design_step": step,
            "assistant_message": assistant,
            "business_spec": state.get("business_spec") or "",
            "tradeoff_ledger": state.get("tradeoff_ledger") or "",
            "scale_estimates": state.get("scale_estimates") or "",
            "api_contracts": state.get("api_contracts") or "",
            "communication_schemes": state.get("communication_schemes") or "",
            "fmea_notes": state.get("fmea_notes") or "",
            "design_diagram": state.get("design_diagram") or "",
            "design_justification": state.get("design_justification") or "",
            "ready_to_advance": ready,
            "design_ready_to_approve": design_ready,
            "can_approve": ready or design_approve,
            "approve_kind": "design" if design_approve else "advance",
            "approve_label": approve_label("hld", "hld", step, design_ready=design_ready),
        }
    )

    action = (resume or {}).get("action", "chat")
    user_text = ((resume or {}).get("text") or "").strip()
    keynotes, kind, clar = gate_user_chat(
        state,
        user_text,
        action=action,
        node="hld",
        stay={
            "phase": "hld",
            "design_track": "hld",
            "design_step": step,
            "ready_to_advance": ready,
            "design_ready_to_approve": design_ready,
        },
    )
    if clar:
        return clar
    state["discussion_digest"] = keynotes
    last_as = str(state.get("pending_assistant_message") or "")
    action = resolve_wait_action(action, user_text, last_as, consult_kind=kind)
    advance_now = is_advance_request(user_text, last_as)
    msgs: list[dict[str, Any]] = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "hld"})

    if action == "approve" and is_design_approve_step("hld", step):
        msg = (
            "Wrapping up this step and moving on. "
            "Design version queued for market evaluation, then handoff to the "
            "Orchestrator. Review the market report when it appears."
            if advance_now
            else (
                "Design version queued for market evaluation, then handoff to the "
                "Orchestrator. Review the market report when it appears."
            )
        )
        msgs.append({"role": "assistant", "content": msg, "node": "hld"})
        return {
            "phase": "market_research",
            "design_track": "hld",
            "design_step": step,
            "publish_requested": False,
            "resume_after_market": True,
            "market_evaluation_done": False,
            "pending_user_feedback": "",
            "pending_assistant_message": msg,
            "stay_on_interrupt": False,
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if action == "approve" and step < 6:
        next_step = step + 1
        title = _STEP_TITLES.get(next_step, "")
        msg = (
            f"Wrapping up this step and moving on. Next: HLD step {next_step} — {title}."
            if advance_now
            else f"Advancing to HLD step {next_step}: {title}."
        )
        msgs.append({"role": "assistant", "content": msg, "node": "hld"})
        return {
            "phase": "hld",
            "design_track": "hld",
            "design_step": next_step,
            "ready_to_advance": False,
            "design_ready_to_approve": False,
            "pending_user_feedback": "",
            "pending_assistant_message": msg,
            "stay_on_interrupt": False,
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "hld"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "stay_on_interrupt": False,
            "discussion_digest": keynotes,
            "messages": msgs,
        }

    if user_text:
        rewound = rewind_or_block_skip(
            state,
            user_text,
            node="hld",
            current_phase="hld",
            current_track="hld",
            current_step=step,
            msgs=msgs,
        )
        if rewound is not None:
            return rewound

    if action == "answer" and user_text:
        return answer_before_approve(
            state,
            user_text,
            node="hld",
            base={
                "phase": "hld",
                "design_track": "hld",
                "design_step": step,
                "ready_to_advance": ready,
                "design_ready_to_approve": design_ready,
            },
        )

    return {
        "phase": "hld",
        "design_track": "hld",
        "design_step": step,
        "ready_to_advance": False,
        "pending_user_feedback": user_text,
        "publish_requested": False,
        "stay_on_interrupt": False,
        "discussion_digest": keynotes,
        "messages": msgs,
    }
