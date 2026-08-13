import { useEffect, useId, useMemo, useRef, useState } from "react";
import mermaid from "mermaid";
import { sanitizeMermaidSource } from "./sanitizeMermaid";

type Props = { source: string };

type Point = { x: number; y: number };

type EdgeInfo = {
  id: string;
  start: string;
  end: string;
  path: SVGPathElement;
  label: SVGGElement | null;
};

type NodeDrag = {
  kind: "node";
  el: SVGGElement;
  nodeKey: string;
  origin: Point;
  start: Point;
  edges: EdgeInfo[];
};

type DragMode = { kind: "pan"; origin: Point; start: Point } | NodeDrag | null;

const MIN_SCALE = 0.4;
const MAX_SCALE = 2.5;
const ZOOM_STEP = 1.15;

export function MermaidDiagram({ source }: Props) {
  const reactId = useId().replace(/:/g, "");
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SVGGElement | null>(null);
  const edgesRef = useRef<EdgeInfo[]>([]);
  const nodesRef = useRef<Map<string, SVGGElement>>(new Map());
  const viewRef = useRef({ x: 0, y: 0, scale: 1 });
  const dragRef = useRef<DragMode>(null);
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState(!source.trim());
  const [ready, setReady] = useState(false);
  const sanitized = useMemo(() => sanitizeMermaidSource(source), [source]);

  const applyView = () => {
    const scene = sceneRef.current;
    if (!scene) return;
    const { x, y, scale } = viewRef.current;
    scene.setAttribute("transform", `translate(${x} ${y}) scale(${scale})`);
  };

  const resetView = () => {
    viewRef.current = { x: 0, y: 0, scale: 1 };
    applyView();
  };

  const zoomBy = (factor: number, center?: Point) => {
    const host = hostRef.current;
    const prev = viewRef.current;
    const nextScale = clamp(prev.scale * factor, MIN_SCALE, MAX_SCALE);
    const ratio = nextScale / prev.scale;
    if (ratio === 1) return;

    const cx = center?.x ?? (host ? host.clientWidth / 2 : 0);
    const cy = center?.y ?? (host ? host.clientHeight / 2 : 0);
    viewRef.current = {
      scale: nextScale,
      x: cx - (cx - prev.x) * ratio,
      y: cy - (cy - prev.y) * ratio,
    };
    applyView();
  };

  useEffect(() => {
    let cancelled = false;

    async function render() {
      const host = hostRef.current;
      if (!host) return;

      if (!sanitized.trim()) {
        host.innerHTML = "";
        sceneRef.current = null;
        edgesRef.current = [];
        nodesRef.current = new Map();
        if (!cancelled) {
          setEmpty(true);
          setError(null);
          setReady(false);
        }
        return;
      }

      try {
        const { svg } = await mermaid.render(`mmd-${reactId}-${Date.now()}`, sanitized);
        if (cancelled || !hostRef.current) return;

        hostRef.current.innerHTML = svg;
        const svgEl = hostRef.current.querySelector("svg");
        if (!svgEl) {
          setError("Diagram SVG missing");
          setReady(false);
          return;
        }

        svgEl.removeAttribute("width");
        svgEl.removeAttribute("height");
        svgEl.style.width = "100%";
        svgEl.style.height = "100%";
        svgEl.setAttribute("preserveAspectRatio", "xMidYMid meet");

        const scene = document.createElementNS("http://www.w3.org/2000/svg", "g");
        scene.classList.add("diagram-scene");
        while (svgEl.firstChild) {
          scene.appendChild(svgEl.firstChild);
        }
        svgEl.appendChild(scene);
        sceneRef.current = scene;
        viewRef.current = { x: 0, y: 0, scale: 1 };
        applyView();

        const nodes = new Map<string, SVGGElement>();
        scene.querySelectorAll<SVGGElement>("g.node, g.cluster").forEach((el) => {
          el.classList.add("diagram-movable");
          el.style.cursor = "grab";
          const key = logicalNodeId(el);
          if (key) nodes.set(key, el);
        });
        nodesRef.current = nodes;
        edgesRef.current = indexEdges(scene, [...nodes.keys()]);

        setEmpty(false);
        setError(null);
        setReady(true);
      } catch (err) {
        if (!cancelled && hostRef.current) {
          hostRef.current.innerHTML = "";
          sceneRef.current = null;
          edgesRef.current = [];
          nodesRef.current = new Map();
          setError(err instanceof Error ? err.message : String(err));
          setReady(false);
          setEmpty(false);
        }
      }
    }

    void render();
    return () => {
      cancelled = true;
    };
  }, [sanitized, reactId]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !ready) return;

    const pointerDeltaToScene = (from: Point, to: Point): Point => {
      const scene = sceneRef.current;
      const ctm = scene?.getScreenCTM();
      if (!ctm) {
        const scale = viewRef.current.scale || 1;
        return { x: (to.x - from.x) / scale, y: (to.y - from.y) / scale };
      }
      const inv = ctm.inverse();
      const a = new DOMPoint(from.x, from.y).matrixTransform(inv);
      const b = new DOMPoint(to.x, to.y).matrixTransform(inv);
      return { x: b.x - a.x, y: b.y - a.y };
    };

    const applyNodeDrag = (drag: NodeDrag, client: Point) => {
      const delta = pointerDeltaToScene(drag.origin, client);
      writeTranslate(drag.el, drag.start.x + delta.x, drag.start.y + delta.y);

      for (const edge of drag.edges) {
        rerouteEdge(edge, nodesRef.current);
      }
    };

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = host.getBoundingClientRect();
      const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      zoomBy(factor, { x: event.clientX - rect.left, y: event.clientY - rect.top });
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const target = event.target as Element | null;
      const node = target?.closest?.("g.node, g.cluster") as SVGGElement | null;

      if (node && sceneRef.current?.contains(node)) {
        const nodeKey = logicalNodeId(node);
        const connected = nodeKey
          ? edgesRef.current.filter((e) => e.start === nodeKey || e.end === nodeKey)
          : [];
        dragRef.current = {
          kind: "node",
          el: node,
          nodeKey: nodeKey || "",
          origin: { x: event.clientX, y: event.clientY },
          start: readTranslate(node),
          edges: connected,
        };
        node.style.cursor = "grabbing";
        node.setPointerCapture?.(event.pointerId);
        event.preventDefault();
        return;
      }

      dragRef.current = {
        kind: "pan",
        origin: { x: event.clientX, y: event.clientY },
        start: { x: viewRef.current.x, y: viewRef.current.y },
      };
      host.classList.add("is-panning");
      host.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    };

    const onPointerMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;

      if (drag.kind === "pan") {
        viewRef.current = {
          ...viewRef.current,
          x: drag.start.x + (event.clientX - drag.origin.x),
          y: drag.start.y + (event.clientY - drag.origin.y),
        };
        applyView();
        return;
      }

      applyNodeDrag(drag, { x: event.clientX, y: event.clientY });
    };

    const onPointerUp = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (drag.kind === "node") {
        applyNodeDrag(drag, { x: event.clientX, y: event.clientY });
        drag.el.style.cursor = "grab";
        try {
          drag.el.releasePointerCapture?.(event.pointerId);
        } catch {
          /* ignore */
        }
      } else {
        host.classList.remove("is-panning");
        try {
          host.releasePointerCapture?.(event.pointerId);
        } catch {
          /* ignore */
        }
      }
      dragRef.current = null;
    };

    host.addEventListener("wheel", onWheel, { passive: false });
    host.addEventListener("pointerdown", onPointerDown);
    host.addEventListener("pointermove", onPointerMove);
    host.addEventListener("pointerup", onPointerUp);
    host.addEventListener("pointercancel", onPointerUp);

    return () => {
      host.removeEventListener("wheel", onWheel);
      host.removeEventListener("pointerdown", onPointerDown);
      host.removeEventListener("pointermove", onPointerMove);
      host.removeEventListener("pointerup", onPointerUp);
      host.removeEventListener("pointercancel", onPointerUp);
    };
  }, [ready]);

  if (!source.trim() && empty) {
    return <div className="diagram">No diagram yet.</div>;
  }

  return (
    <div className="diagram diagram-interactive">
      <div className="diagram-toolbar" role="toolbar" aria-label="Diagram view controls">
        <button type="button" className="btn ghost diagram-tool" onClick={() => zoomBy(ZOOM_STEP)} title="Zoom in">
          +
        </button>
        <button
          type="button"
          className="btn ghost diagram-tool"
          onClick={() => zoomBy(1 / ZOOM_STEP)}
          title="Zoom out"
        >
          −
        </button>
        <button type="button" className="btn ghost diagram-tool" onClick={resetView} title="Reset view">
          Reset
        </button>
        <span className="diagram-hint">Drag nodes · drag background to pan · scroll to zoom</span>
      </div>
      {error ? (
        <div className="diagram-error">
          <pre>{sanitized}</pre>
          <p className="error">{error}</p>
        </div>
      ) : null}
      <div
        ref={hostRef}
        className="diagram-canvas"
        hidden={Boolean(error)}
        aria-label="Interactive architecture diagram"
      />
    </div>
  );
}

/** Rebuild a continuous orthogonal edge between the two live node anchors. */
function rerouteEdge(edge: EdgeInfo, nodes: Map<string, SVGGElement>) {
  const startEl = nodes.get(edge.start);
  const endEl = nodes.get(edge.end);
  if (!startEl || !endEl) return;

  const startCenter = nodeCenter(startEl);
  const endCenter = nodeCenter(endEl);
  const startPt = borderAnchor(startEl, startCenter, endCenter);
  const endPt = borderAnchor(endEl, endCenter, startCenter);
  const points =
    edge.start === edge.end ? selfLoopPoints(startCenter, startEl) : orthogonalRoute(startPt, endPt);

  writeEdgePath(edge.path, points);
  try {
    edge.path.setAttribute("data-points", btoa(JSON.stringify(points)));
  } catch {
    /* ignore */
  }

  if (edge.label) {
    const mid = pathMidpoint(points);
    writeTranslate(edge.label, mid.x, mid.y);
  }
}

function nodeCenter(el: SVGGElement): Point {
  return readTranslate(el);
}

function nodeHalfSize(el: SVGGElement): Point {
  try {
    const box = el.getBBox();
    return { x: Math.max(box.width / 2, 8), y: Math.max(box.height / 2, 8) };
  } catch {
    return { x: 40, y: 24 };
  }
}

/** Mermaid decision nodes (`A{...}`) render as a 4-point polygon diamond. */
function isDiamondShape(el: SVGGElement): boolean {
  const poly = el.querySelector("polygon");
  if (poly?.points && poly.points.numberOfItems === 4) return true;
  // Hand-drawn diamonds are paths; detect near-square node with no rect.
  if (el.querySelector("rect, circle, ellipse")) return false;
  const path = el.querySelector("path");
  if (!path) return false;
  const half = nodeHalfSize(el);
  return Math.abs(half.x - half.y) / Math.max(half.x, half.y) < 0.2;
}

/**
 * Point on the node border in the direction of `toward`.
 * Rectangles use L∞ (AABB); diamonds use L1 (rhombus) so edges meet the tips/sides.
 */
function borderAnchor(el: SVGGElement, center: Point, toward: Point): Point {
  const poly = shapePolygonInScene(el);
  if (poly && poly.length >= 3) {
    const hit = rayPolygonIntersect(center, toward, poly);
    if (hit) return hit;
  }

  const half = nodeHalfSize(el);
  const dx = toward.x - center.x;
  const dy = toward.y - center.y;
  if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) {
    return { x: center.x + half.x, y: center.y };
  }

  if (isDiamondShape(el)) {
    // Rhombus with vertices at (±r,0) and (0,±r), r ≈ half of square bbox.
    const r = Math.max(half.x, half.y);
    const t = r / (Math.abs(dx) + Math.abs(dy));
    return { x: center.x + dx * t, y: center.y + dy * t };
  }

  const scale = Math.min(half.x / Math.abs(dx), half.y / Math.abs(dy));
  return { x: center.x + dx * scale, y: center.y + dy * scale };
}

/** Polygon vertices transformed into diagram-scene coordinates. */
function shapePolygonInScene(el: SVGGElement): Point[] | null {
  const poly = el.querySelector("polygon");
  if (!poly?.points || poly.points.numberOfItems < 3) return null;
  const scene = el.ownerSVGElement?.querySelector(".diagram-scene") as SVGGElement | null;
  const sceneCtm = scene?.getScreenCTM();
  const polyCtm = poly.getScreenCTM();
  if (!sceneCtm || !polyCtm) return null;
  const toScene = sceneCtm.inverse().multiply(polyCtm);
  const pts: Point[] = [];
  for (let i = 0; i < poly.points.numberOfItems; i++) {
    const p = poly.points.getItem(i);
    const mapped = new DOMPoint(p.x, p.y).matrixTransform(toScene);
    pts.push({ x: mapped.x, y: mapped.y });
  }
  return pts;
}

/** Closest ray hit from origin toward `toward` against a polygon. */
function rayPolygonIntersect(origin: Point, toward: Point, poly: Point[]): Point | null {
  const dx = toward.x - origin.x;
  const dy = toward.y - origin.y;
  if (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9) return poly[0] ?? null;

  let bestT = Infinity;
  let best: Point | null = null;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const hit = raySegmentIntersect(origin, dx, dy, a, b);
    if (hit && hit.t > 1e-6 && hit.t < bestT) {
      bestT = hit.t;
      best = hit.point;
    }
  }
  return best;
}

function raySegmentIntersect(
  origin: Point,
  dx: number,
  dy: number,
  a: Point,
  b: Point,
): { t: number; point: Point } | null {
  const ex = b.x - a.x;
  const ey = b.y - a.y;
  const denom = dx * ey - dy * ex;
  if (Math.abs(denom) < 1e-9) return null;
  const ox = a.x - origin.x;
  const oy = a.y - origin.y;
  const t = (ox * ey - oy * ex) / denom;
  const u = (ox * dy - oy * dx) / denom;
  if (t <= 1e-6 || u < -1e-6 || u > 1 + 1e-6) return null;
  return { t, point: { x: origin.x + t * dx, y: origin.y + t * dy } };
}

function orthogonalRoute(from: Point, to: Point): Point[] {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return [from, to];

  // Prefer a single elbow; keep the path continuous (no stranded midpoints).
  if (Math.abs(dx) >= Math.abs(dy)) {
    const midX = from.x + dx / 2;
    return [from, { x: midX, y: from.y }, { x: midX, y: to.y }, to];
  }
  const midY = from.y + dy / 2;
  return [from, { x: from.x, y: midY }, { x: to.x, y: midY }, to];
}

function selfLoopPoints(center: Point, el: SVGGElement): Point[] {
  const half = nodeHalfSize(el);
  if (isDiamondShape(el)) {
    const r = Math.max(half.x, half.y);
    const out = r + 18;
    return [
      { x: center.x + r, y: center.y },
      { x: center.x + out, y: center.y },
      { x: center.x + out, y: center.y - out },
      { x: center.x, y: center.y - out },
      { x: center.x, y: center.y - r },
    ];
  }
  const r = Math.max(half.x, half.y) + 18;
  return [
    { x: center.x + half.x, y: center.y },
    { x: center.x + r, y: center.y },
    { x: center.x + r, y: center.y - r },
    { x: center.x, y: center.y - r },
    { x: center.x, y: center.y - half.y },
  ];
}

function writeEdgePath(path: SVGPathElement, points: Point[]) {
  if (points.length === 0) return;
  const [first, ...rest] = points;
  path.setAttribute("d", `M ${first.x} ${first.y}` + rest.map((p) => ` L ${p.x} ${p.y}`).join(""));
}

function pathMidpoint(points: Point[]): Point {
  if (points.length === 0) return { x: 0, y: 0 };
  if (points.length === 1) return points[0];
  let total = 0;
  const seg: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const len = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    seg.push(len);
    total += len;
  }
  if (total === 0) return points[Math.floor(points.length / 2)];
  let left = total / 2;
  for (let i = 0; i < seg.length; i++) {
    if (left <= seg[i]) {
      const t = seg[i] === 0 ? 0 : left / seg[i];
      return {
        x: points[i].x + (points[i + 1].x - points[i].x) * t,
        y: points[i].y + (points[i + 1].y - points[i].y) * t,
      };
    }
    left -= seg[i];
  }
  return points[points.length - 1];
}

function indexEdges(scene: SVGGElement, nodeKeys: string[]): EdgeInfo[] {
  const edges: EdgeInfo[] = [];
  const paths = scene.querySelectorAll<SVGPathElement>(".edgePaths path, path[data-et='edge']");
  const seen = new Set<string>();

  paths.forEach((path) => {
    const edgeId = path.getAttribute("data-id") || stripDiagramPrefix(path.id);
    if (!edgeId || seen.has(edgeId)) return;
    const ends = parseEdgeEndpoints(edgeId, nodeKeys);
    if (!ends) return;
    seen.add(edgeId);
    const labelEl = (scene
      .querySelector(`.edgeLabel [data-id="${cssEscape(edgeId)}"]`)
      ?.closest("g.edgeLabel") ?? null) as SVGGElement | null;
    edges.push({
      id: edgeId,
      start: ends.start,
      end: ends.end,
      path,
      label: labelEl,
    });
  });

  return edges;
}

function logicalNodeId(el: Element): string | null {
  const dataId = el.getAttribute("data-id");
  if (dataId) return dataId;

  const id = el.id || "";
  const flowchart = /(?:^|-)flowchart-(.+)-(\d+)$/.exec(id);
  if (flowchart) return flowchart[1];
  return null;
}

function stripDiagramPrefix(id: string): string {
  const edge = /(?:^|-)(L_.+)$/.exec(id);
  if (edge) return edge[1];
  return id;
}

function parseEdgeEndpoints(
  edgeId: string,
  nodeKeys: string[],
): { start: string; end: string } | null {
  const body = edgeId.startsWith("L_") ? edgeId.slice(2) : edgeId;
  const sorted = [...nodeKeys].sort((a, b) => b.length - a.length);
  for (const start of sorted) {
    if (!body.startsWith(`${start}_`)) continue;
    const rest = body.slice(start.length + 1);
    for (const end of sorted) {
      if (!rest.startsWith(`${end}_`)) continue;
      const counter = rest.slice(end.length + 1);
      if (/^\d+$/.test(counter)) return { start, end };
    }
  }

  const simple = /^L_([^_]+)_([^_]+)_(\d+)$/.exec(edgeId);
  if (simple) return { start: simple[1], end: simple[2] };
  return null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function readTranslate(el: SVGGElement): Point {
  const transform = el.getAttribute("transform") || "";
  const match = /translate\(\s*([-\d.eE]+)(?:[,\s]+)([-\d.eE]+)\s*\)/.exec(transform);
  if (match) {
    return { x: Number(match[1]), y: Number(match[2]) };
  }
  try {
    const ctm = el.transform.baseVal.consolidate()?.matrix;
    if (ctm) return { x: ctm.e, y: ctm.f };
  } catch {
    /* ignore */
  }
  return { x: 0, y: 0 };
}

function writeTranslate(el: SVGGElement, x: number, y: number) {
  const transform = el.getAttribute("transform") || "";
  if (/translate\s*\(/.test(transform)) {
    el.setAttribute(
      "transform",
      transform.replace(/translate\(\s*[-\d.eE]+(?:[,\s]+[-\d.eE]+)\s*\)/, `translate(${x}, ${y})`),
    );
    return;
  }
  el.setAttribute("transform", `translate(${x}, ${y}) ${transform}`.trim());
}

function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}
