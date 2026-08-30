import { useEffect, useState } from "react";

export const HOLDER_HEADER = "X-Session-Holder";
const HEARTBEAT_MS = 4_000;

export type InteractionState = {
  holder_id: string;
  is_holder: boolean;
  interactive: boolean;
  locked: boolean;
};

function newHolderId(): string {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof webCrypto?.getRandomValues === "function") {
    webCrypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function sessionHolderId(sessionId: string): string {
  const key = `session-holder:${sessionId}`;
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = newHolderId();
    sessionStorage.setItem(key, id);
  }
  return id;
}

export function holderHeaders(sessionId: string, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  if (sessionId) headers.set(HOLDER_HEADER, sessionHolderId(sessionId));
  return headers;
}

export function useSessionPresence(sessionId: string): {
  ready: boolean;
  interactive: boolean;
  locked: boolean;
} {
  const [state, setState] = useState({
    ready: false,
    interactive: false,
    locked: false,
  });

  useEffect(() => {
    if (!sessionId) return;
    const holder = sessionHolderId(sessionId);
    let cancelled = false;

    async function beat() {
      try {
        const res = await fetch(`/api/sessions/${sessionId}/presence`, {
          method: "POST",
          headers: { "Content-Type": "application/json", [HOLDER_HEADER]: holder },
          body: JSON.stringify({ holder_id: holder }),
        });
        if (cancelled) return;
        if (res.status === 423) {
          setState({ ready: true, interactive: false, locked: true });
          return;
        }
        if (!res.ok) {
          setState({ ready: true, interactive: false, locked: false });
          return;
        }
        setState({ ready: true, interactive: true, locked: false });
      } catch {
        if (!cancelled) setState((prev) => ({ ...prev, ready: true }));
      }
    }

    function release() {
      const body = JSON.stringify({ holder_id: holder });
      navigator.sendBeacon(
        `/api/sessions/${sessionId}/presence/release`,
        new Blob([body], { type: "application/json" }),
      );
    }

    void beat();
    const timer = window.setInterval(() => void beat(), HEARTBEAT_MS);
    window.addEventListener("pagehide", release);
    window.addEventListener("beforeunload", release);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("pagehide", release);
      window.removeEventListener("beforeunload", release);
      release();
    };
  }, [sessionId]);

  return state;
}
