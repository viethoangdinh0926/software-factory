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
      "  Client[Web/Mobile Client] -->|HTTPS| LB[Load Balancer]",
      "  LB -->|TCP| GW[API Gateway]",
      "  GW -->|gRPC| Auth[Auth IdentityService]",
      "  GW -->|HTTPS| Core[Core Domain Service]",
      "  Core -->|SQL| DB[(Postgres)]",
      "  Core -->|cache| Cache[(Redis)]",
      "  Core -->|pub/sub| Bus[(Kafka)]",
      "  GW -->|HTTPS| Search[Search Service]",
      "  Search -->|index| ES[(Elasticsearch)]",
      "  Client -->|HLS| CDN[CDN]",
    ].join("\n");
  }

  const lower = (spec || "").toLowerCase();
  const lines = [
    "flowchart LR",
    "  UI[Desktop UI] -->|compose| App[Application Shell]",
    "  App -->|in-process| Editor[Request Editor]",
    "  App -->|in-process| Collections[Collection Store]",
    "  App -->|in-process| History[History Store]",
    "  App -->|in-process| Http[HTTP Client]",
    "  App -->|in-process| Errors[Error Presenter]",
    "  Collections -->|SQL| Sqlite[(SQLite)]",
    "  History -->|SQL| Sqlite",
    "  Editor -->|dispatch| Http",
    "  Http -->|HTTPS| Remote[Remote API]",
  ];
  if (/(auth|token|credential|api key)/.test(lower)) {
    lines.push("  App -->|in-process| Creds[Credential Store]", "  Creds -->|SQL| Sqlite");
  }
  if (/(queue|offline|sync)/.test(lower)) {
    lines.push("  App -->|in-process| Queue[Request Queue]", "  Queue -->|dispatch| Http", "  Queue -->|SQL| Sqlite");
  }
  return lines.join("\n");
}

const NODE_LABELS = [
  /\b([A-Za-z][\w-]*)\s*\[\s*\(?\s*"?([^\]\)\n"]+?)"?\s*\)?\s*\]/g,
  /\b([A-Za-z][\w-]*)\s*\{\s*"?([^}\n"]+?)"?\s*\}/g,
  /\b([A-Za-z][\w-]*)\s*\(\s*"?([^)\n"]+?)"?\s*\)/g,
];

const PIPE_EDGE_RE =
  /^([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*(?:--+|==+|\.-+|-+\.)>\s*\|([^|\n]*)\|\s*([A-Za-z][\w-]*)/;
const TEXT_EDGE_RE =
  /^([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*--\s*([^>\n]+?)\s*-->\s*([A-Za-z][\w-]*)/;
const BARE_EDGE_RE =
  /^([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*(?:--+|==+|\.-+|-+\.)>\s*([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?(?:\s*:\s*(.+))?$/;
const SKIP_EDGE = /^(style |classdef |class |click |subgraph|direction|%%|linkstyle|flowchart|graph |classdiagram)/i;
const REL_HEADING_RE = /^###\s+(.+?)\s*(?:→|->|--|>)\s*(.+?)\s*$/;

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

export type DiagramEdge = {
  from: string;
  to: string;
  from_label: string;
  to_label: string;
  label: string;
  explanation: string;
};

export function extractDiagramEdges(diagram: string): { from: string; to: string; label: string }[] {
  const seen = new Set<string>();
  const out: { from: string; to: string; label: string }[] = [];
  for (const raw of (diagram || "").split("\n")) {
    const line = raw.trim();
    if (!line || SKIP_EDGE.test(line)) continue;
    const parsed = parseEdgeLine(line);
    if (!parsed) continue;
    const key = `${parsed.from}\0${parsed.to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(parsed);
  }
  return out;
}

function parseEdgeLine(line: string): { from: string; to: string; label: string } | null {
  const pipe = PIPE_EDGE_RE.exec(line);
  if (pipe) return { from: pipe[1], to: pipe[3], label: (pipe[2] || "").trim() };
  const text = TEXT_EDGE_RE.exec(line);
  if (text) return { from: text[1], to: text[3], label: (text[2] || "").trim() };
  const bare = BARE_EDGE_RE.exec(line);
  if (bare) return { from: bare[1], to: bare[2], label: (bare[3] || "").trim() };
  return null;
}

function normRelToken(value: string): string {
  return (value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function lookupEdgeNote(
  notes: {
    from: string;
    to: string;
    from_label?: string;
    to_label?: string;
    label?: string;
    explanation?: string;
  }[],
  start: string,
  end: string,
): (typeof notes)[number] | undefined {
  const startKeys = new Set([normRelToken(start)].filter(Boolean));
  const endKeys = new Set([normRelToken(end)].filter(Boolean));
  return notes.find((note) => {
    const left = new Set(
      [normRelToken(note.from), normRelToken(note.from_label || "")].filter(Boolean),
    );
    const right = new Set(
      [normRelToken(note.to), normRelToken(note.to_label || "")].filter(Boolean),
    );
    return [...startKeys].some((k) => left.has(k)) && [...endKeys].some((k) => right.has(k));
  });
}

export function parseRelationshipCatalog(catalog: string): { from: string; to: string; explanation: string }[] {
  const lines = (catalog || "").split("\n");
  const out: { from: string; to: string; explanation: string }[] = [];
  let current: { from: string; to: string; parts: string[] } | null = null;
  const flush = () => {
    if (!current) return;
    const explanation = current.parts.join("\n").trim();
    if (explanation) out.push({ from: current.from, to: current.to, explanation });
    current = null;
  };
  for (const line of lines) {
    const heading = REL_HEADING_RE.exec(line.trim());
    if (heading) {
      flush();
      current = {
        from: heading[1].trim().replace(/`/g, ""),
        to: heading[2].trim().replace(/`/g, ""),
        parts: [],
      };
      continue;
    }
    if (current && !line.startsWith("## ")) current.parts.push(line);
  }
  flush();
  return out;
}

export function fallbackRelationshipNotes(
  diagram: string,
  spec = "",
  comms = "",
  catalog = "",
): DiagramEdge[] {
  const labels = Object.fromEntries(extractDiagramComponents(diagram).map((c) => [c.id, c.label]));
  const parsed = parseRelationshipCatalog(catalog);
  return extractDiagramEdges(diagram).map((edge) => {
    const from_label = labels[edge.from] || edge.from;
    const to_label = labels[edge.to] || edge.to;
    const hit = lookupEdgeNote(parsed, edge.from, edge.to);
    return {
      from: edge.from,
      to: edge.to,
      from_label,
      to_label,
      label: edge.label,
      explanation:
        hit?.explanation ||
        relationshipExplanation(edge.from, edge.to, from_label, to_label, edge.label, spec, comms),
    };
  });
}

function relationshipExplanation(
  startId: string,
  endId: string,
  startLabel: string,
  endLabel: string,
  edgeLabel: string,
  spec: string,
  comms: string,
): string {
  const proto = edgeLabel.trim() ? ` over ${edgeLabel.trim()}` : "";
  const snippet = snippetFromText(comms, startLabel, endLabel, startId, endId)
    || snippetFromText(spec, startLabel, endLabel, startId, endId);
  const body = `${startLabel} calls${proto} ${endLabel}. The arrow is a directed dependency: ${startLabel} needs ${endLabel} to complete its work, and the protocol on this hop is what the two sides actually speak.`;
  if (snippet && !body.toLowerCase().includes(snippet.toLowerCase().slice(0, 40))) {
    return `${body} ${snippet}`;
  }
  return body;
}

function snippetFromText(text: string, ...needles: string[]): string {
  const wanted = needles.filter((n) => n && n.trim().length > 1);
  if (!text.trim() || wanted.length < 2) return "";
  for (const para of text.split(/\n+/)) {
    const blob = para.toLowerCase();
    const hits = wanted.filter((n) => blob.includes(n.toLowerCase())).length;
    if (hits >= 2) {
      const clean = para.replace(/\s+/g, " ").replace(/^[-* ]+/, "").trim();
      if (clean.length >= 40) return clean.slice(0, 600);
    }
  }
  return "";
}

function sectionBounds(spec: string, heading: string): { start: number; end: number; contentStart: number } | null {
  const title = heading.startsWith("## ") ? heading : `## ${heading}`;
  const body = spec || "";
  const startRe = new RegExp(`(?:^|\\n)${escapeRegExp(title)}\\s*\\n`);
  const found = startRe.exec(body);
  if (!found) return null;
  const start = found.index + (found[0].startsWith("\n") ? 1 : 0);
  const contentStart = found.index + found[0].length;
  const rest = body.slice(contentStart);
  // Next H2 only (`\\n## `). Do not stop at `###` or at end-of-line.
  const relEnd = rest.search(/\n## /);
  const end = relEnd < 0 ? body.length : contentStart + relEnd;
  return { start, end, contentStart };
}

export function extractSpecSection(spec: string, heading: string): string {
  const bounds = sectionBounds(spec, heading);
  if (!bounds) return "";
  return (spec || "").slice(bounds.contentStart, bounds.end).trim();
}

export function stripSpecSection(spec: string, heading: string): string {
  const bounds = sectionBounds(spec, heading);
  if (!bounds) return (spec || "").trim();
  const body = spec || "";
  return `${body.slice(0, bounds.start)}\n${body.slice(bounds.end)}`
    .replace(/\n{3,}/g, "\n\n")
    .trim();
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
  for (const edge of extractDiagramEdges(diagram)) {
    const start = edge.from;
    const end = edge.to;
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
