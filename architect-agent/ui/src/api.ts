export type ChatMessage = {
  role: "assistant" | "user" | "system";
  content: string;
  node?: string;
};

export type HandoffResult = {
  status: "sent" | "queued" | "failed" | string;
  handoff_id: string;
  path: string;
  target_url: string | null;
  detail: string;
  at: string;
};

export type DesignSession = {
  design_session_id: string;
  phase: string;
  design_track: string;
  design_step: number;
  ready_for_design: boolean;
  ready_to_advance: boolean;
  design_ready_to_approve: boolean;
  spec_approved: boolean;
  design_approved: boolean;
  finalized: boolean;
  can_approve: boolean;
  approve_label: string;
  approve_kind: string;
  business_spec: string;
  design_diagram: string;
  design_justification: string;
  tradeoff_ledger: string;
  scale_estimates: string;
  api_contracts: string;
  fmea_notes: string;
  market_evaluation_report: string;
  market_evaluation_grade: string;
  market_evaluation_done: boolean;
  messages: ChatMessage[];
  ui_path: string;
  updated_at: string;
  design_version: number;
  last_handoff: HandoffResult | null;
};

export type DesignStartResponse = {
  design_session_id: string;
  ui_url: string;
  phase: string;
  ready_for_design: boolean;
  message: string | null;
};

async function readError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const json = JSON.parse(text) as { detail?: string };
    return json.detail ?? text;
  } catch {
    return text || res.statusText;
  }
}

export async function startDesign(markdown: string): Promise<DesignStartResponse> {
  const body = new FormData();
  body.set("markdown", markdown);
  const res = await fetch("/design", { method: "POST", body });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignStartResponse>;
}

export async function getSession(sessionId: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function chat(sessionId: string, message: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function approve(sessionId: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/approve`, { method: "POST" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function endSession(sessionId: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/end`, { method: "POST" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export function specDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/spec`;
}

export function marketEvaluationDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/market-evaluation`;
}

export function finalDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/final`;
}

export function trackStepLabel(session: DesignSession): string | null {
  const track = (session.design_track || "").toUpperCase();
  if (!track || track === "UNSET") {
    return session.phase === "phase0" ? "Phase 0" : null;
  }
  const max = track === "LLD" ? 3 : track === "HLD" ? 6 : 0;
  if (!max || session.phase === "market_research") {
    return track;
  }
  const step = Math.max(0, session.design_step || 0);
  if (step <= 0) return track;
  return `${track} · Step ${step}/${max}`;
}
