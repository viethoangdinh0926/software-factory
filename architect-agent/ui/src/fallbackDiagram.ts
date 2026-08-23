/** Client-side sketch when the session is past the diagram step but Mermaid is empty. */

export function diagramIsDue(phase: string, track: string, step: number): boolean {
  if (phase === "market_research" || phase === "done") {
    return track === "lld" || track === "hld";
  }
  if (phase === "lld") return step >= 2;
  if (phase === "hld") return step >= 4;
  return false;
}

export function fallbackDesignDiagram(spec: string, track: string): string {
  if (track === "hld") {
    return [
      "flowchart LR",
      "  Client[Web/Mobile Client] --> LB[Load Balancer]",
      "  LB --> GW[API Gateway]",
      "  GW --> Auth[Auth IdentityService]",
      "  GW --> Core[Core Domain Service]",
      "  Core --> DB[(Postgres)]",
      "  Core --> Cache[(Redis)]",
      "  Core --> Bus[(Kafka)]",
      "  GW --> Search[Search Service]",
      "  Search --> ES[(Elasticsearch)]",
      "  Client --> CDN[CDN]",
    ].join("\n");
  }

  const lower = (spec || "").toLowerCase();
  const lines = [
    "flowchart LR",
    "  UI[Desktop UI] --> App[Application Shell]",
    "  App --> Editor[Request Editor]",
    "  App --> Collections[Collection Store]",
    "  App --> History[History Store]",
    "  App --> Http[HTTP Client]",
    "  App --> Errors[Error Presenter]",
    "  Collections --> Sqlite[(SQLite)]",
    "  History --> Sqlite",
    "  Editor --> Http",
    "  Http --> Remote[Remote API]",
  ];
  if (/(auth|token|credential|api key)/.test(lower)) {
    lines.push("  App --> Creds[Credential Store]", "  Creds --> Sqlite");
  }
  if (/(queue|offline|sync)/.test(lower)) {
    lines.push("  App --> Queue[Request Queue]", "  Queue --> Http", "  Queue --> Sqlite");
  }
  return lines.join("\n");
}

const NODE_LABELS = [
  /\b([A-Za-z][\w-]*)\s*\[\s*\(?\s*"?([^\]\)\n"]+?)"?\s*\)?\s*\]/g,
  /\b([A-Za-z][\w-]*)\s*\{\s*"?([^}\n"]+?)"?\s*\}/g,
  /\b([A-Za-z][\w-]*)\s*\(\s*"?([^)\n"]+?)"?\s*\)/g,
];

const EDGE_RE = /\b([A-Za-z][\w-]*)\s*-+\s*>\s*([A-Za-z][\w-]*)/g;

export function extractDiagramComponents(diagram: string): { id: string; label: string }[] {
  const seen = new Set<string>();
  const out: { id: string; label: string }[] = [];
  for (const pattern of NODE_LABELS) {
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(diagram || ""))) {
      const id = match[1];
      const key = id.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      const label = (match[2] || id).trim();
      if (label) out.push({ id, label });
    }
  }
  return out;
}

export function extractSpecSection(spec: string, heading: string): string {
  const title = heading.startsWith("## ") ? heading : `## ${heading}`;
  const re = new RegExp(`^${escapeRegExp(title)}\\s*\\n([\\s\\S]*?)(?=^## |$)`, "m");
  const match = (spec || "").match(re);
  return (match?.[1] || "").trim();
}

export function stripSpecSection(spec: string, heading: string): string {
  const title = heading.startsWith("## ") ? heading : `## ${heading}`;
  const re = new RegExp(`\\n*${escapeRegExp(title)}\\s*\\n[\\s\\S]*?(?=^## |$)`, "m");
  return (spec || "").replace(re, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function catalogCoversDiagram(catalog: string, diagram: string): boolean {
  const labels = extractDiagramComponents(diagram).map((c) => c.label);
  if (!labels.length || !(catalog || "").trim()) return false;
  const blob = catalog.toLowerCase();
  const hits = labels.filter((label) => blob.includes(label.toLowerCase())).length;
  return hits >= Math.min(3, Math.max(1, Math.ceil(labels.length / 2)));
}

export function chatDescribesComponents(
  messages: { role: string; content: string }[],
  diagram: string,
): boolean {
  const texts = messages
    .filter((m) => m.role === "assistant")
    .map((m) => m.content)
    .join("\n");
  if (texts.toLowerCase().includes("diagram components")) return true;
  return catalogCoversDiagram(texts, diagram);
}

export function fallbackComponentCatalog(diagram: string, spec = ""): string {
  const components = extractDiagramComponents(diagram);
  if (!components.length) return "";
  const labels = Object.fromEntries(components.map((c) => [c.id, c.label]));
  const neighbors = new Map<string, string[]>();
  for (const c of components) neighbors.set(c.id, []);
  EDGE_RE.lastIndex = 0;
  let edge: RegExpExecArray | null;
  while ((edge = EDGE_RE.exec(diagram || ""))) {
    const [, start, end] = edge;
    if (neighbors.has(start) && labels[end] && !neighbors.get(start)!.includes(labels[end])) {
      neighbors.get(start)!.push(labels[end]);
    }
    if (neighbors.has(end) && labels[start] && !neighbors.get(end)!.includes(labels[start])) {
      neighbors.get(end)!.push(labels[start]);
    }
  }
  const lines = ["## Diagram components", ""];
  for (const { id, label } of components) {
    const peers = (neighbors.get(id) || []).slice(0, 4).join(", ");
    lines.push(`### ${label}`, componentRole(label, spec, peers), "");
  }
  return `${lines.join("\n").trim()}\n`;
}

function componentRole(label: string, spec: string, peers: string): string {
  const lower = label.toLowerCase();
  const specL = (spec || "").toLowerCase();
  let role = `${label} participates in the structure shown on the diagram.`;
  if (lower.includes("desktop ui") || lower === "ui") {
    role = "Operator-facing desktop surface for collections, the request editor, and results.";
  } else if (lower.includes("application shell") || lower === "app") {
    role = "Composes stores, the editor, HTTP, and error presentation; owns the desktop lifecycle.";
  } else if (lower.includes("request editor")) {
    role = "Builds the headers, body, and target of an API request before dispatch.";
  } else if (lower.includes("collection")) {
    role = "Persists named collections of API requests in the local store.";
  } else if (lower.includes("history")) {
    role = "Persists request/response pairs, status, and body for later review.";
  } else if (lower.includes("http")) {
    role = "Dispatches HTTP/HTTPS calls to the remote API and returns status and body.";
  } else if (lower.includes("error")) {
    role = "Surfaces network and server failures to the operator instead of failing silently.";
  } else if (lower.includes("sqlite")) {
    role = "Embedded transactional database for collections, history, and other local records.";
  } else if (lower.includes("remote") || lower.endsWith(" api")) {
    role = "Destination service under test; reached only while the client is online.";
  } else if (lower.includes("credential") || lower.includes("auth") || lower.includes("identity")) {
    role = "Holds tokens or API keys used when a request needs authentication.";
  } else if (lower.includes("queue")) {
    role =
      specL.includes("offline") || specL.includes("queue")
        ? "Holds work that could not be sent yet."
        : "Optional buffer between the shell and the HTTP client.";
  } else if (lower.includes("gateway")) {
    role = "Edge entry: routing, authn/authz, and protocol translation for clients.";
  } else if (lower.includes("load balancer") || lower === "lb") {
    role = "Spreads inbound client traffic across gateway instances.";
  } else if (lower.includes("cdn")) {
    role = "Caches and serves static or streamed content close to clients.";
  } else if (lower.includes("redis") || lower.includes("cache")) {
    role = "Low-latency cache and session/hot-key store in front of durable data.";
  } else if (lower.includes("kafka") || lower.includes("bus")) {
    role = "Async event backbone between services.";
  } else if (lower.includes("postgres") || lower.includes("database") || lower === "db") {
    role = "System of record for durable domain data.";
  } else if (lower.includes("search") || lower.includes("elastic")) {
    role = "Indexes documents for discovery and full-text lookup.";
  } else if (lower.includes("client")) {
    role = "User-facing client that talks to the edge of the system.";
  } else if (lower.includes("service")) {
    role = "Owns a bounded slice of domain operations and data.";
  }
  return peers ? `${role} Collaborates with ${peers}.` : role;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
