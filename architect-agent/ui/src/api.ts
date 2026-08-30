import { holderHeaders } from "./sessionPresence";

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

export type WorkflowTile = {
  id: string;
  title: string;
  status: "current" | "done" | "pending" | string;
  kind: string;
  body: string;
  diagram: string;
};

export type WorkflowState = {
  id: string;
  phase: string;
  track?: string;
  step?: number;
  title: string;
  tiles: WorkflowTile[];
};

export type DiagramEdgeNote = {
  from: string;
  to: string;
  from_label?: string;
  to_label?: string;
  label?: string;
  explanation: string;
};

export type DesignSession = {
  design_session_id: string;
  phase: string;
  design_track: string;
  design_step: number;
  design_step_title: string;
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
  diagram_edges: DiagramEdgeNote[];
  design_justification: string;
  tradeoff_ledger: string;
  scale_estimates: string;
  api_contracts: string;
  communication_schemes: string;
  fmea_notes: string;
  market_evaluation_report: string;
  market_evaluation_grade: string;
  market_evaluation_done: boolean;
  messages: ChatMessage[];
  workflow: WorkflowState;
  ui_path: string;
  updated_at: string;
  design_version: number;
  last_handoff: HandoffResult | null;
  can_retry_handoff: boolean;
  interaction?: InteractionState;
};

export type InteractionState = {
  holder_id: string;
  is_holder: boolean;
  interactive: boolean;
  locked: boolean;
};

export type DesignStartResponse = {
  design_session_id: string;
  ui_url: string;
  phase: string;
  ready_for_design: boolean;
  message: string | null;
};

export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

export type SessionSummary = {
  design_session_id: string;
  phase: string;
  design_track: string;
  design_step: number;
  updated_at: string;
  finalized: boolean;
  design_version: number;
  ui_path: string;
};

async function readError(res: Response): Promise<string> {
  if (res.status === 404) return "Session not found.";
  if (res.status === 423) return "This session is open in another browser.";
  return "Something went wrong. Please try again.";
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new HttpError(res.status, await readError(res));
  const data = (await res.json()) as { sessions: SessionSummary[] };
  return data.sessions ?? [];
}

export async function startDesign(markdown: string): Promise<DesignStartResponse> {
  const body = new FormData();
  body.set("markdown", markdown);
  const res = await fetch("/design", { method: "POST", body });
  if (!res.ok) throw new HttpError(res.status, await readError(res));
  return res.json() as Promise<DesignStartResponse>;
}

export async function getSession(sessionId: string): Promise<DesignSession> {
  if (!sessionId.trim()) throw new HttpError(404, "Session not found.");
  const res = await fetch(`/api/sessions/${sessionId}`, { headers: holderHeaders(sessionId) });
  if (!res.ok) throw new HttpError(res.status, await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function chat(sessionId: string, message: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: holderHeaders(sessionId, { "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function approve(sessionId: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: holderHeaders(sessionId),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function retryHandoff(sessionId: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/retry-handoff`, {
    method: "POST",
    headers: holderHeaders(sessionId),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<DesignSession>;
}

export async function endSession(sessionId: string): Promise<DesignSession> {
  const res = await fetch(`/api/sessions/${sessionId}/end`, {
    method: "POST",
    headers: holderHeaders(sessionId),
  });
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

export function formatPhaseLabel(phase: string | undefined): string | null {
  const raw = (phase || "").trim().toLowerCase().replaceAll("_", " ");
  if (!raw) return null;
  if (raw === "phase0" || raw === "phase 0") return "Phase 0";
  if (raw === "market research") return "Market evaluation";
  if (raw === "lld") return "LLD";
  if (raw === "hld") return "HLD";
  if (raw === "done") return "Done";
  return raw;
}

export function formatMessageNode(node?: string): string {
  if (!node) return "";
  return formatPhaseLabel(node) || node.replaceAll("_", " ");
}

function normalizeChip(label: string): string {
  return label
    .toLowerCase()
    .replaceAll("·", " ")
    .replaceAll("/", " ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\bstep\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function chipIsRedundant(existing: string | null, candidate: string): boolean {
  if (!existing) return false;
  const a = normalizeChip(existing);
  const b = normalizeChip(candidate);
  if (!a || !b) return false;
  if (a === b || a.includes(b) || b.includes(a)) return true;
  const tokensA = new Set(a.split(" ").filter((w) => w.length > 1));
  const tokensB = b.split(" ").filter((w) => w.length > 1);
  return tokensB.length > 0 && tokensB.every((w) => tokensA.has(w));
}

/** Compact rail index ("HLD 3") so the tile heading can keep the full step name. */
export function workflowRailLabel(title: string): string {
  const dash = title.indexOf(" - ");
  if (dash > 0) return title.slice(0, dash).trim();
  return title;
}

/** Process-state chips that are not already shown by the workflow rail. */
export function sessionHeaderChips(session: DesignSession): string[] {
  const tiles = session.workflow?.tiles || [];
  const current = (session.workflow?.title || "").trim();
  if (!tiles.length) {
    return current ? [current] : [];
  }
  const onTrackStep = session.phase === "lld" || session.phase === "hld";
  if (onTrackStep || session.phase === "done") return [];
  const track = (session.design_track || "").toUpperCase();
  if (track && track !== "UNSET" && !chipIsRedundant(current || null, track)) {
    return [track];
  }
  return [];
}

export function shouldShowMessageNode(node: string | undefined, phase: string): boolean {
  const label = formatMessageNode(node);
  const current = formatPhaseLabel(phase);
  if (!label) return false;
  if (!current) return true;
  return label.toLowerCase() !== current.toLowerCase();
}
