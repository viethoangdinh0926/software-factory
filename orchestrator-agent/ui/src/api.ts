import { holderHeaders } from "./sessionPresence";

export type ChatMessage = {
  role: "assistant" | "user" | "system";
  content: string;
  node?: string;
};

export type EngineerHandoff = {
  status: string;
  handoff_id: string;
  path: string;
  target_url: string | null;
  detail: string;
  at: string;
  action: string;
  design_session_id: string;
  design_version: number;
  microservice_id: string | null;
};

export type WorkflowTile = {
  id: string;
  title: string;
  status: string;
  kind: string;
  body: string;
  diagram: string;
};

export type WorkflowState = {
  id: string;
  phase: string;
  title: string;
  tiles: WorkflowTile[];
};

export type MicroservicePlan = {
  microservice_id: string;
  names: string[];
  role_key: string;
  architect_api_contract: string;
  feature_spec: string;
  entity_relationships: string;
  api_type: string;
  api_type_recommendation: string;
  proposed_api_type: string;
  api_design: string;
  tech_stack: string;
  plan_spec: string;
  bug_spec?: string;
  spec_version?: number;
  spec_changelog?: string;
  update_kind?: string;
  status: string;
  messages: ChatMessage[];
  search_notes: string;
  can_approve: boolean;
  approve_kind: string;
  approve_label: string;
  discussion_open: boolean;
  workflow?: WorkflowState;
};

export type WorkflowSession = {
  design_session_id: string;
  ui_path: string;
  updated_at: string;
  created_at: string;
  design_version: number;
  architect_track: string;
  topology: string;
  phase: string;
  wait_kind: string;
  package_markdown: string;
  design_diagram: string;
  tech_stack: string;
  feature_spec: string;
  plan_spec: string;
  api_type: string;
  api_design: string;
  app_status: string;
  search_notes: string;
  services: MicroservicePlan[];
  active_service_id: string;
  messages: ChatMessage[];
  workflow?: WorkflowState;
  engineer_handoffs: EngineerHandoff[];
  last_handoff: EngineerHandoff | null;
  can_approve: boolean;
  approve_kind: string;
  approve_label: string;
  discussion_locked: boolean;
  finalized: boolean;
  git_repo_url: string;
  git_key_configured: boolean;
  git_key_fingerprint: string;
  git_send_status: string;
  git_send_error: string;
  git_sent_at: string;
  can_send_git: boolean;
  interaction?: InteractionState;
};

export type InteractionState = {
  holder_id: string;
  is_holder: boolean;
  interactive: boolean;
  locked: boolean;
};

export type WorkflowSummary = {
  design_session_id: string;
  design_version: number;
  architect_track: string;
  topology: string;
  phase: string;
  updated_at: string;
  finalized: boolean;
  ui_path: string;
};

async function readError(res: Response): Promise<string> {
  if (res.status === 404) return "Session not found.";
  if (res.status === 423) return "This session is open in another browser.";
  return "Something went wrong. Please try again.";
}

export async function listSessions(): Promise<WorkflowSummary[]> {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { sessions: WorkflowSummary[] };
  return data.sessions ?? [];
}

export async function ingestPackage(markdown: string): Promise<{ design_session_id: string }> {
  const body = new FormData();
  body.set("markdown", markdown);
  const res = await fetch("/ingest", { method: "POST", body });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<{ design_session_id: string }>;
}

export async function getSession(sessionId: string): Promise<WorkflowSession> {
  if (!sessionId.trim()) throw new Error("Session not found.");
  const res = await fetch(`/api/sessions/${sessionId}`, { headers: holderHeaders(sessionId) });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function chat(
  sessionId: string,
  message: string,
  serviceId?: string,
): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: holderHeaders(sessionId, { "Content-Type": "application/json" }),
    body: JSON.stringify({ message, service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function approve(sessionId: string, serviceId?: string): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: holderHeaders(sessionId, { "Content-Type": "application/json" }),
    body: JSON.stringify({ service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function endSession(sessionId: string): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/end`, {
    method: "POST",
    headers: holderHeaders(sessionId),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function retryIngest(sessionId: string): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/retry-ingest`, {
    method: "POST",
    headers: holderHeaders(sessionId),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

async function readGitError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
  } catch {
    // fall through
  }
  return "Something went wrong. Please try again.";
}

export async function saveGit(
  sessionId: string,
  gitRepoUrl: string,
  sshPrivateKey: string,
): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/git`, {
    method: "PUT",
    headers: holderHeaders(sessionId, { "Content-Type": "application/json" }),
    body: JSON.stringify({
      git_repo_url: gitRepoUrl,
      ssh_private_key: sshPrivateKey || null,
    }),
  });
  if (!res.ok) throw new Error(await readGitError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function sendGit(sessionId: string): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/git/send`, {
    method: "POST",
    headers: holderHeaders(sessionId),
  });
  if (!res.ok) throw new Error(await readGitError(res));
  return res.json() as Promise<WorkflowSession>;
}

export function planDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/plan`;
}

export function serviceLabel(svc: MicroservicePlan): string {
  const names = svc.names || [];
  return names[names.length - 1] || svc.role_key || "service";
}
