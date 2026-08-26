export type ChatMessage = {
  role: "assistant" | "user" | "system";
  content: string;
  node?: string;
};

export type PeerConsult = {
  peer_sub_agent_id: string;
  peer_name: string;
  peer_microservice_id: string;
  we_initiate: boolean;
  offered_api: string;
  status: string;
};

export type PlanItem = {
  id: string;
  kind: string;
  title: string;
  priority: number;
  depends_on: string[];
  peer_services: string[];
  status: string;
  notes: string;
  contracts?: Record<string, string>;
};

export type ExecutionPlan = {
  version: number;
  summary: string;
  transition: string;
  items: PlanItem[];
};

export type BlockIssue = {
  kind: string;
  title: string;
  detail: string;
  item_id?: string;
  item_title?: string;
  instructions?: string;
};

export type SubEngineer = {
  sub_agent_id: string;
  design_session_id: string;
  microservice_id: string;
  microservice_name: string;
  plan_spec: string;
  entity_relationships: string;
  feature_spec: string;
  tech_stack: string;
  offered_api: string;
  implementation_notes: string;
  peer_consults: PeerConsult[];
  incoming_api_requests: { from_name?: string; detail: string; status: string }[];
  status: string;
  messages: ChatMessage[];
  can_approve: boolean;
  approve_kind: string;
  approve_label: string;
  can_pause: boolean;
  can_execute: boolean;
  plan_locked: boolean;
  discussion_open: boolean;
  execution_plan?: ExecutionPlan;
  workspace_path?: string;
  git_ship_status?: string;
  git_ship_error?: string;
  block_issue?: BlockIssue | null;
  workflow?: {
    id: string;
    phase: string;
    title: string;
    tiles: { id: string; title: string; status: string; kind: string; body: string; diagram: string }[];
  };
};

export type FleetSession = {
  design_session_id: string;
  ui_path: string;
  updated_at: string;
  created_at: string;
  design_version: number;
  sub_agents: SubEngineer[];
  messages: ChatMessage[];
  git_repo_url: string;
  git_key_configured: boolean;
  git_received_at: string;
};

export type FleetSummary = {
  design_session_id: string;
  design_version: number;
  sub_agent_count: number;
  updated_at: string;
  ui_path: string;
};

async function readError(_res: Response): Promise<string> {
  return "Something went wrong. Please try again.";
}

export async function listSessions(): Promise<FleetSummary[]> {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { sessions: FleetSummary[] };
  return data.sessions ?? [];
}

export async function ingestPackage(markdown: string): Promise<{ design_session_id: string }> {
  const body = new FormData();
  body.set("markdown", markdown);
  const res = await fetch("/ingest", { method: "POST", body });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<{ design_session_id: string }>;
}

export async function getSession(sessionId: string): Promise<FleetSession> {
  const res = await fetch(`/api/sessions/${sessionId}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<FleetSession>;
}

export async function chat(
  sessionId: string,
  message: string,
  serviceId?: string,
): Promise<FleetSession> {
  const res = await fetch(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<FleetSession>;
}

export async function approve(sessionId: string, serviceId?: string): Promise<FleetSession> {
  const res = await fetch(`/api/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<FleetSession>;
}

export async function pause(sessionId: string, serviceId?: string): Promise<FleetSession> {
  const res = await fetch(`/api/sessions/${sessionId}/pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<FleetSession>;
}

export async function execute(sessionId: string, serviceId?: string): Promise<FleetSession> {
  const res = await fetch(`/api/sessions/${sessionId}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service_id: serviceId ?? null }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<FleetSession>;
}

export function subLabel(sub: SubEngineer): string {
  return sub.microservice_name || sub.microservice_id || "service";
}

export function packageDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/package`;
}
