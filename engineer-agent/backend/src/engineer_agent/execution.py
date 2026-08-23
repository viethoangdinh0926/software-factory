"""Execution-plan helpers: priority order, dependency gating, and plan transitions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

VALID_KINDS = {"feature", "bug", "feature_update"}
TERMINAL_ITEM = {"done", "skipped"}
RUNNABLE_ITEM = {"pending", "consulting", "in_progress", "blocked"}


def plan_items(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (plan or {}).get("items") if isinstance(plan, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def has_plan_items(plan: dict[str, Any] | None) -> bool:
    return bool(plan_items(plan))


def normalize_plan(raw: dict[str, Any] | None, *, version: int | None = None) -> dict[str, Any]:
    blob = raw if isinstance(raw, dict) else {}
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(plan_items(blob), start=1):
        item_id = str(item.get("id") or f"item-{index}").strip() or f"item-{index}"
        if item_id in seen_ids:
            item_id = f"{item_id}-{index}"
        seen_ids.add(item_id)
        kind = str(item.get("kind") or "feature").strip().lower()
        if kind not in VALID_KINDS:
            kind = "feature"
        status = str(item.get("status") or "pending").strip().lower()
        if status not in RUNNABLE_ITEM | TERMINAL_ITEM:
            status = "pending"
        depends = item.get("depends_on") or []
        if not isinstance(depends, list):
            depends = []
        peers = item.get("peer_services") or []
        if not isinstance(peers, list):
            peers = []
        try:
            priority = int(item.get("priority") or index)
        except (TypeError, ValueError):
            priority = index
        contracts = item.get("contracts") if isinstance(item.get("contracts"), dict) else {}
        items.append(
            {
                "id": item_id,
                "kind": kind,
                "title": str(item.get("title") or f"Work item {index}").strip() or f"Work item {index}",
                "priority": priority,
                "depends_on": [str(dep).strip() for dep in depends if str(dep).strip()],
                "peer_services": [str(peer).strip() for peer in peers if str(peer).strip()],
                "status": status,
                "notes": str(item.get("notes") or ""),
                "contracts": {str(k): str(v) for k, v in contracts.items()},
            }
        )
    ver = version if version is not None else blob.get("version") or 1
    try:
        ver_n = int(ver)
    except (TypeError, ValueError):
        ver_n = 1
    return {
        "version": ver_n,
        "summary": str(blob.get("summary") or "").strip(),
        "transition": str(blob.get("transition") or "").strip(),
        "items": items,
    }


def apply_transition(
    old_plan: dict[str, Any] | None,
    new_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """Carry forward completed work whose title still exists; restart the rest."""
    fresh = normalize_plan(new_plan)
    done_titles: dict[str, dict[str, Any]] = {}
    for item in plan_items(old_plan):
        if str(item.get("status") or "") != "done":
            continue
        key = str(item.get("title") or "").strip().lower()
        if key:
            done_titles[key] = item
    for item in fresh["items"]:
        key = str(item.get("title") or "").strip().lower()
        if key and key in done_titles:
            prior = done_titles[key]
            item["status"] = "done"
            item["contracts"] = dict(prior.get("contracts") or {})
            note = str(item.get("notes") or "").strip()
            carried = "Carried forward as already completed under the previous plan."
            item["notes"] = f"{note} {carried}".strip() if note else carried
        elif item["status"] not in TERMINAL_ITEM:
            item["status"] = "pending"
    if not str(fresh.get("transition") or "").strip():
        if plan_items(old_plan):
            fresh["transition"] = (
                "Carry forward completed items whose titles still exist; "
                "restart changed or new work from the current workspace."
            )
        else:
            fresh["transition"] = (
                "No prior plan. Start from the highest-priority item after "
                "pulling the repo and creating the private workspace folder."
            )
    return fresh


def next_runnable_item(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    items = plan_items(plan)
    done_ids = {str(item.get("id")) for item in items if str(item.get("status")) in TERMINAL_ITEM}

    blocked = [item for item in items if str(item.get("status")) == "blocked"]
    if blocked:
        blocked.sort(key=lambda item: (int(item.get("priority") or 99), str(item.get("id"))))
        return blocked[0]

    in_progress = [item for item in items if str(item.get("status")) == "in_progress"]
    if in_progress:
        in_progress.sort(key=lambda item: (int(item.get("priority") or 99), str(item.get("id"))))
        return in_progress[0]

    ready: list[dict[str, Any]] = []
    for item in items:
        status = str(item.get("status") or "")
        if status not in {"pending", "consulting"}:
            continue
        deps = [str(dep) for dep in (item.get("depends_on") or [])]
        if all(dep in done_ids for dep in deps):
            ready.append(item)
    if not ready:
        return None
    pending = [item for item in ready if str(item.get("status")) == "pending"]
    pool = pending or ready
    pool.sort(key=lambda item: (int(item.get("priority") or 99), str(item.get("id"))))
    return pool[0]


def all_items_terminal(plan: dict[str, Any] | None) -> bool:
    items = plan_items(plan)
    if not items:
        return False
    return all(str(item.get("status")) in TERMINAL_ITEM for item in items)


def snapshot_peer_contracts(
    item: dict[str, Any],
    peers: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Return (contracts, missing_peer_names) for services this item depends on."""
    wanted = [str(name).strip() for name in (item.get("peer_services") or []) if str(name).strip()]
    contracts: dict[str, str] = dict(item.get("contracts") or {})
    missing: list[str] = []
    by_name = {
        str(peer.get("microservice_name") or "").strip().lower(): peer for peer in peers
    }
    by_id = {str(peer.get("microservice_id") or "").strip().lower(): peer for peer in peers}
    for name in wanted:
        key = name.lower()
        peer = by_name.get(key) or by_id.get(key)
        api = str((peer or {}).get("offered_api") or "").strip()
        if peer_contract_ready(peer) and api:
            contracts[name] = api
        else:
            missing.append(name)
    return contracts, missing


def peer_contract_ready(peer: dict[str, Any] | None) -> bool:
    """True when the peer sub-engineer can settle a communication contract."""
    if not peer:
        return False
    if str(peer.get("status") or "") == "suspended":
        return False
    return bool(str(peer.get("offered_api") or "").strip())


def replace_item(plan: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(plan) if isinstance(plan, dict) else {"items": []}
    items = []
    found = False
    want = str(updated.get("id") or "")
    for item in plan_items(out):
        if str(item.get("id")) == want:
            items.append(updated)
            found = True
        else:
            items.append(item)
    if not found:
        items.append(updated)
    out["items"] = items
    return out


def clear_blocked_items(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Retry items that were blocked; keep completed work."""
    out = normalize_plan(plan)
    for item in out["items"]:
        if str(item.get("status") or "") == "blocked":
            item["status"] = "pending"
    return out
