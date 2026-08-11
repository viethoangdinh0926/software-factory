export type ChatMessage = {
  role: "assistant" | "user" | "system";
  content: string;
  node?: string;
};

export type DesignSession = {
  design_session_id: string;
  phase: string;
  ready_for_design: boolean;
  spec_approved: boolean;
  design_approved: boolean;
  finalized: boolean;
  can_approve: boolean;
  business_spec: string;
  design_diagram: string;
  design_justification: string;
  messages: ChatMessage[];
  ui_path: string;
  updated_at: string;
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

export function specDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/spec`;
}

export function finalDownloadUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/download/final`;
}
