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

export type MicroservicePlan = {
  microservice_id: string;
  names: string[];
  role_key: string;
  architect_api_contract: string;
  api_type: string;
  api_type_recommendation: string;
  proposed_api_type: string;
  api_design: string;
  tech_stack: string;
  plan_spec: string;
  status: string;
  messages: ChatMessage[];
  search_notes: string;
  can_approve: boolean;
  approve_kind: string;
  approve_label: string;
  discussion_open: boolean;
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
  plan_spec: string;
  api_type: string;
  api_design: string;
  app_status: string;
  search_notes: string;
  services: MicroservicePlan[];
  active_service_id: string;
  messages: ChatMessage[];
  engineer_handoffs: EngineerHandoff[];
  last_handoff: EngineerHandoff | null;
  can_approve: boolean;
  approve_kind: string;
  approve_label: string;
  discussion_locked: boolean;
  finalized: boolean;
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
  const text = await res.text();
  try {
    const json = JSON.parse(text) as { detail?: string };
    return json.detail ?? text;
  } catch {
    return text || res.statusText;
  }
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
  const res = await fetch(`/api/sessions/${sessionId}`);
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function approve(sessionId: string, serviceId?: string): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export async function endSession(sessionId: string): Promise<WorkflowSession> {
  const res = await fetch(`/api/sessions/${sessionId}/end`, { method: "POST" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<WorkflowSession>;
}

export function planDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/plan`;
}

export function serviceLabel(svc: MicroservicePlan): string {
  const names = svc.names || [];
  return names[names.length - 1] || svc.role_key || "service";
}
