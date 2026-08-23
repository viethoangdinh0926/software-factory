import mermaid from "mermaid";

const STALE_CHUNK_RE =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Loading chunk/i;

let chain: Promise<unknown> = Promise.resolve();
let warmed = false;

export function isStaleDiagramChunkError(err: unknown): boolean {
  const text = err instanceof Error ? err.message : String(err);
  return STALE_CHUNK_RE.test(text);
}

export function diagramLoadErrorMessage(err: unknown): string {
  if (isStaleDiagramChunkError(err)) {
    return "The diagram renderer is out of date. Refresh the page to load it.";
  }
  return err instanceof Error ? err.message : String(err);
}

/** Fetch flowchart + classDiagram chunks up front so the first paint does not race. */
export async function warmupMermaid(): Promise<void> {
  if (warmed) return;
  warmed = true;
  await mermaid.parse("flowchart LR\n  A-->B");
  await mermaid.parse("classDiagram\n  class A");
}

/**
 * Mermaid lazy-loads one Vite chunk per diagram type. Concurrent render() calls
 * abort each other's imports (Strict Mode, source flicker) and look like a 404.
 */
export function renderMermaid(id: string, source: string): Promise<{ svg: string }> {
  const run = chain.then(() => renderOnce(id, source), () => renderOnce(id, source));
  chain = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

async function renderOnce(id: string, source: string): Promise<{ svg: string }> {
  try {
    return await mermaid.render(id, source);
  } catch (err) {
    if (!isStaleDiagramChunkError(err)) throw err;
    await new Promise((resolve) => setTimeout(resolve, 120));
    return mermaid.render(`${id}-retry`, source);
  }
}
