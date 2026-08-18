from __future__ import annotations

import re
from typing import Any

from langgraph.types import interrupt

from architect_agent.context_budget import (
    INTERVIEW_TECHNIQUE_DIGEST,
    JSON_OUTPUT_DIGEST,
    PRINCIPAL_ARCHITECT_DIGEST,
    format_history_tail,
    maybe_compact_business_spec,
    maybe_compact_design_justification,
)
from architect_agent.graph.nodes.common import (
    answer_before_approve,
    approve_label,
    invoke_json,
    is_design_approve_step,
)
from architect_agent.graph.state import DesignGraphState
from architect_agent.json_util import coerce_diagram_text
from architect_agent.mermaid_sanitize import sanitize_mermaid
from architect_agent.query_intent import FEEDBACK_RESOLUTION_RULES, is_informational_query, with_resolution_close

_STEP_TITLES = {
    1: "Requirements & capacity estimation",
    2: "Domain object modeling",
    3: "Microservice integration & API design",
    4: "Infrastructure, trade-offs & system diagram",
    5: "Vulnerability & edge-case analysis (FMEA)",
    6: "Session synthesis & wrap-up",
}

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

_HTTP_ENDPOINT_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/[A-Za-z0-9_{}\-./?*]*",
    re.I,
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


def _api_contracts_are_concrete(text: str) -> bool:
    """Step 3 depth bar: named services with real HTTP endpoints."""
    body = (text or "").strip()
    if len(body) < 600:
        return False
    lower = body.lower()
    # Classic shallow summary.
    if (
        "are defined via rest" in lower
        or "ensuring clear service boundaries" in lower
    ) and _HTTP_ENDPOINT_RE.search(body) is None:
        return False
    endpoints = _HTTP_ENDPOINT_RE.findall(body)
    if len(endpoints) < 6:
        return False
    services = {m.group(1) for m in _SERVICE_HEADER_RE.finditer(body)}
    services |= set(re.findall(r"\b([A-Z][A-Za-z]+Service)\b", body))
    if len(services) < 3:
        return False
    # Prefer method diversity (not six GETs only).
    methods = {m.upper() for m in endpoints}
    if len(methods) < 2:
        return False
    return True


def _fmea_notes_are_concrete(text: str) -> bool:
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


_HLD_PRIMARY_FIELD = {
    1: "scale_estimates",
    2: "updated_business_spec (domain model section) + tradeoff_ledger",
    3: "api_contracts",
    4: "design_diagram_lines",
    5: "fmea_notes",
    6: "design_justification",
}


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
            "numbers rule in/out for later steps. Then invite Approve. Optional one ❓.\n"
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
            "- Do not skip to APIs. ready_to_advance=true when ≥5 entities are listed.\n"
        )
    if step == 3:
        return header + (
            "HLD Step 3 — write api_contracts NOW as headed services with HTTP specs:\n"
            "- ≥3 services, ≥6 endpoints, mix GET and POST/PATCH.\n"
            "- Each endpoint: METHOD /path plus brief request/response or status.\n"
            "- NEVER a one-sentence 'REST/JSON boundaries' summary.\n"
            "Example:\n"
            "### IdentityService\\n- POST /v1/auth/login — {email,password} → token (200/401)\\n"
            "### VideoCatalogService\\n- GET /v1/videos/{id} — metadata (200/404)\\n"
            "- PATCH /v1/videos/{id} — title/visibility (200/403)\\n"
        )
    if step == 4:
        return header + (
            "HLD Step 4 — write design_diagram_lines NOW (12–25 nodes). Not a concept pipeline.\n"
            "Required node kinds: Client, LoadBalancer, APIGateway, Auth/IdentityService, "
            "each named *Service from api_contracts, Redis, Kafka, Elasticsearch, CDN, "
            "Postgres, ObjectStorage/S3. Edges for sync vs async.\n"
            "Use design_diagram_lines only; set design_diagram to \"\".\n"
            "Example lines: [\"flowchart LR\", \"  Client[Web] --> LB[Load Balancer]\", "
            "\"  LB --> GW[API Gateway]\", \"  GW --> Auth[IdentityService]\", ...]\n"
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
            "Set design_ready_to_approve=true. Invite Approve & send design.\n"
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
    fmea: str,
    diagram: str,
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
    elif step == 3:
        if _api_contracts_are_concrete(apis):
            ready_advance = True
        else:
            ready_advance = False
            hint = (
                "API contracts are still too brief. Expand `api_contracts` with headed services "
                "and concrete HTTP method+path endpoints (plus short payload/status notes) "
                "before we advance."
            )
    elif step == 4:
        if _diagram_is_concrete(diagram, apis):
            ready_advance = True
        else:
            ready_advance = False
            hint = (
                "The system diagram is still too high-level. Expand it to show "
                "API gateway/load balancer, auth, each business microservice, "
                "caches, brokers, CDN, and distinct storage systems before we advance."
            )
    elif step == 5:
        if _fmea_notes_are_concrete(fmea):
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
    history_tail = format_history_tail(prior)
    step_rules = _step_artifact_rules(step)

    result = invoke_json(
        system=(
            "You are the Architect agent's HLD track node (Principal Architect).\n"
            f"{PRINCIPAL_ARCHITECT_DIGEST}\n\n"
            f"{INTERVIEW_TECHNIQUE_DIGEST}\n\n"
            f"{JSON_OUTPUT_DIGEST}\n\n"
            f"Current HLD step: {step} — {_STEP_TITLES.get(step, '')}.\n"
            "Fill this step's primary artifact completely on this turn using labeled "
            "assumptions. The interviewer should only need to click Approve.\n"
            "Do not skip steps unless the user explicitly directs it.\n"
            f"{step_rules}"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "updated_business_spec": string,\n'
            '  "tradeoff_ledger": string,\n'
            '  "scale_estimates": string,\n'
            '  "api_contracts": string,\n'
            '  "fmea_notes": string,\n'
            '  "design_diagram_lines": [string, ...],\n'
            '  "design_diagram": string,\n'
            '  "design_justification": string,\n'
            '  "ready_to_advance": boolean,\n'
            '  "design_ready_to_approve": boolean,\n'
            '  "assistant_message": string\n'
            "}\n"
            "If you rewrite the primary field, it must meet the depth bar so "
            "ready_to_advance can be true. Empty primary field is a failure.\n"
            f"{FEEDBACK_RESOLUTION_RULES if pending else ''}"
        ),
        user=(
            f"Living specification:\n\n{business_spec}\n\n"
            f"Trade-off ledger:\n{state.get('tradeoff_ledger') or '(empty)'}\n\n"
            f"Scale estimates:\n{state.get('scale_estimates') or '(empty)'}\n\n"
            f"API contracts:\n{state.get('api_contracts') or '(empty)'}\n\n"
            f"FMEA notes:\n{state.get('fmea_notes') or '(empty)'}\n\n"
            f"Current diagram:\n{state.get('design_diagram') or '(none)'}\n\n"
            f"Current justification:\n{state.get('design_justification') or '(none)'}\n\n"
            f"Recent HLD turns:\n{history_tail}\n\n"
            f"Latest user message:\n{pending or '(none — produce the step artifact from the spec using labeled assumptions)'}\n"
            f"Reminder: primary field this turn is {_HLD_PRIMARY_FIELD.get(step)}. "
            "Do not leave it empty."
        ),
    )

    prior_apis = str(state.get("api_contracts") or "")
    prior_scale = str(state.get("scale_estimates") or "")
    prior_fmea = str(state.get("fmea_notes") or "")
    prior_ledger = str(state.get("tradeoff_ledger") or "")
    prior_diagram = str(state.get("design_diagram") or "")

    scale = _prefer_richer_text(str(result.get("scale_estimates") or ""), prior_scale)
    apis = _prefer_richer_text(str(result.get("api_contracts") or ""), prior_apis)
    fmea = _prefer_richer_text(str(result.get("fmea_notes") or ""), prior_fmea)
    ledger = _prefer_richer_text(str(result.get("tradeoff_ledger") or ""), prior_ledger)

    new_diagram = sanitize_mermaid(
        coerce_diagram_text(result, fallback="")
    )
    diagram = sanitize_mermaid(_prefer_diagram(new_diagram, prior_diagram, apis))
    justification = maybe_compact_design_justification(
        str(result.get("design_justification") or state.get("design_justification") or "")
    )

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
        fmea=fmea,
        diagram=diagram,
    )

    assistant = result.get("assistant_message") or f"HLD step {step} update."
    if pending:
        changed = (
            scale != prior_scale
            or apis != prior_apis
            or fmea != prior_fmea
            or ledger != prior_ledger
            or diagram != prior_diagram
            or str(result.get("updated_business_spec") or business_spec) != business_spec
        )
        assistant = with_resolution_close(str(assistant), changed=changed)

    return {
        "phase": "hld",
        "design_track": "hld",
        "design_step": step,
        "business_spec": result.get("updated_business_spec") or business_spec,
        "tradeoff_ledger": ledger,
        "scale_estimates": scale,
        "api_contracts": apis,
        "fmea_notes": fmea,
        "design_diagram": diagram,
        "design_justification": justification,
        "ready_to_advance": ready_advance,
        "design_ready_to_approve": design_ready,
        "ready_for_design": design_ready,
        "pending_user_feedback": "",
        "pending_assistant_message": assistant,
        "publish_requested": False,
        "stay_on_interrupt": False,
        "messages": [{"role": "assistant", "content": assistant, "node": "hld"}],
    }


def hld_wait_node(state: DesignGraphState) -> dict[str, Any]:
    step = max(1, min(6, int(state.get("design_step") or 1)))
    ready = bool(state.get("ready_to_advance"))
    design_ready = bool(state.get("design_ready_to_approve"))
    assistant = state.get("pending_assistant_message") or ""
    design_approve = is_design_approve_step("hld", step) and design_ready

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
    msgs: list[dict[str, Any]] = []
    if user_text:
        msgs.append({"role": "user", "content": user_text, "node": "hld"})

    if action == "approve" and design_approve:
        msg = (
            "Design version queued for market evaluation, then handoff to the "
            "Orchestrator. Review the market report when it appears."
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
            "messages": msgs,
        }

    if action == "approve" and ready and step < 6:
        next_step = step + 1
        msg = f"Advancing to HLD step {next_step}: {_STEP_TITLES.get(next_step, '')}."
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
            "messages": msgs,
        }

    if action == "session_done":
        msgs.append({"role": "assistant", "content": "Session marked done.", "node": "hld"})
        return {
            "phase": "done",
            "pending_assistant_message": "Session marked done.",
            "stay_on_interrupt": False,
            "messages": msgs,
        }

    if user_text and is_informational_query(user_text):
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
        "messages": msgs,
    }
