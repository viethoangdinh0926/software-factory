"""Per-session interaction lease so one browser tab at a time can drive a session."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

LEASE_TTL_SECONDS = 45.0


class SessionBusyError(Exception):
    """Another holder already owns this session's interaction lease."""

    def __init__(self, holder_id: str) -> None:
        super().__init__("This session is open in another browser.")
        self.holder_id = holder_id


@dataclass
class _Lease:
    holder_id: str
    expires_at: float


def _empty_snapshot() -> dict[str, Any]:
    return {
        "holder_id": "",
        "is_holder": False,
        "interactive": False,
        "locked": False,
    }


class SessionPresence:
    def __init__(self) -> None:
        self._leases: dict[str, _Lease] = {}
        self._guard = threading.Lock()

    def _purge(self, session_id: str) -> None:
        lease = self._leases.get(session_id)
        if lease and lease.expires_at <= time.monotonic():
            self._leases.pop(session_id, None)

    def snapshot(self, session_id: str, holder_id: str | None) -> dict[str, Any]:
        with self._guard:
            self._purge(session_id)
            lease = self._leases.get(session_id)
            if lease is None:
                return _empty_snapshot()
            is_holder = bool(holder_id) and holder_id == lease.holder_id
            return {
                "holder_id": lease.holder_id,
                "is_holder": is_holder,
                "interactive": is_holder,
                "locked": not is_holder,
            }

    def heartbeat(self, session_id: str, holder_id: str) -> dict[str, Any]:
        holder = (holder_id or "").strip()
        if not holder:
            raise ValueError("holder_id is required")
        with self._guard:
            self._purge(session_id)
            lease = self._leases.get(session_id)
            if lease and lease.holder_id != holder:
                raise SessionBusyError(lease.holder_id)
            self._leases[session_id] = _Lease(
                holder_id=holder,
                expires_at=time.monotonic() + LEASE_TTL_SECONDS,
            )
            return {
                "holder_id": holder,
                "is_holder": True,
                "interactive": True,
                "locked": False,
            }

    def release(self, session_id: str, holder_id: str) -> dict[str, Any]:
        holder = (holder_id or "").strip()
        with self._guard:
            self._purge(session_id)
            lease = self._leases.get(session_id)
            if lease and holder and lease.holder_id == holder:
                self._leases.pop(session_id, None)
            return _empty_snapshot()

    def require(self, session_id: str, holder_id: str | None) -> None:
        """Block UI mutations from a non-holder when a lease exists.

        No lease means A2A, scripts, and existing tests keep working.
        """
        with self._guard:
            self._purge(session_id)
            lease = self._leases.get(session_id)
            if lease is None:
                return
            if holder_id and holder_id == lease.holder_id:
                return
            raise SessionBusyError(lease.holder_id)
