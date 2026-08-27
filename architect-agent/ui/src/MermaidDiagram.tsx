import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { DiagramEdgeNote } from "./api";
import { lookupEdgeNote } from "./fallbackDiagram";
import { diagramLoadErrorMessage, renderMermaid } from "./mermaidRender";
import { sanitizeMermaidSource } from "./sanitizeMermaid";

type Props = { source: string; edgeNotes?: DiagramEdgeNote[] };

type Point = { x: number; y: number };

type EdgeInfo = {
  id: string;
  start: string;
  end: string;
  startLabel: string;
  endLabel: string;
  path: SVGPathElement;
  hitPath: SVGPathElement;
  label: SVGGElement | null;
  protocol?: string;
  relationship: string;
};

type TooltipState = {
  startLabel: string;
  endLabel: string;
  protocol?: string;
  relationship: string;
  x: number;
  y: number;
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

export function MermaidDiagram({ source, edgeNotes = [] }: Props) {
  const reactId = useId().replace(/:/g, "");
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SVGGElement | null>(null);
  const edgesRef = useRef<EdgeInfo[]>([]);
  const nodesRef = useRef<Map<string, SVGGElement>>(new Map());
  const viewRef = useRef({ x: 0, y: 0, scale: 1 });
  const dragRef = useRef<DragMode>(null);
  const hoveredEdgeRef = useRef<EdgeInfo | null>(null);
  const edgeNotesRef = useRef(edgeNotes);
  const tooltipElRef = useRef<HTMLDivElement>(null);
  edgeNotesRef.current = edgeNotes;
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState(!source.trim());
  const [ready, setReady] = useState(false);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
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
        // Clean up hit paths
        host.querySelectorAll<SVGPathElement>("[data-hit-path='true']").forEach(p => p.remove());
        if (!cancelled) {
          setEmpty(true);
          setError(null);
          setReady(false);
        }
        return;
      }

      try {
        const { svg } = await renderMermaid(`mmd-${reactId}-${Date.now()}`, sanitized);
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
        // Mermaid inlines max-width to the diagram's intrinsic size; that caps the SVG
        // at ~half the full-width panel and clips (swallows) nodes dragged past it.
        svgEl.style.maxWidth = "none";
        svgEl.style.width = "100%";
        svgEl.style.height = "100%";
        svgEl.style.pointerEvents = "auto";
        svgEl.setAttribute("overflow", "visible");
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
        unclipDiagramLabels(svgEl);

        const nodes = new Map<string, SVGGElement>();
        scene.querySelectorAll<SVGGElement>("g.node, g.cluster").forEach((el) => {
          el.classList.add("diagram-movable");
          el.style.cursor = "grab";
          const key = logicalNodeId(el);
          if (key) nodes.set(key, el);
        });
        nodesRef.current = nodes;
        
        // Clean up old hit paths before creating new ones
        scene.querySelectorAll<SVGPathElement>("[data-hit-path='true']").forEach(p => p.remove());
        
        edgesRef.current = indexEdges(scene, [...nodes.keys()], edgeNotesRef.current);
        hoveredEdgeRef.current = null;
        setTooltip(null);

        setEmpty(false);
        setError(null);
        setReady(true);
      } catch (err) {
        if (!cancelled && hostRef.current) {
          hostRef.current.innerHTML = "";
          sceneRef.current = null;
          edgesRef.current = [];
          nodesRef.current = new Map();
          // Clean up hit paths
          hostRef.current.querySelectorAll<SVGPathElement>("[data-hit-path='true']").forEach(p => p.remove());
          setError(diagramLoadErrorMessage(err));
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
    for (const edge of edgesRef.current) {
      applyEdgeNote(edge, edgeNotes);
    }
  }, [edgeNotes]);

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

    const clearHover = () => {
      const current = hoveredEdgeRef.current;
      if (current) {
        current.path.style.stroke = "";
        current.path.style.strokeWidth = "";
      }
      hoveredEdgeRef.current = null;
      setTooltip(null);
    };

    const tooltipPoint = (clientX: number, clientY: number) => ({
      left: Math.max(12, Math.min(clientX + 18, window.innerWidth - 380)),
      top: Math.max(12, Math.min(clientY + 18, window.innerHeight - 200)),
    });

    const showHover = (edge: EdgeInfo, clientX: number, clientY: number) => {
      const { left, top } = tooltipPoint(clientX, clientY);
      if (hoveredEdgeRef.current === edge && tooltipElRef.current) {
        tooltipElRef.current.style.left = `${left}px`;
        tooltipElRef.current.style.top = `${top}px`;
        return;
      }
      const current = hoveredEdgeRef.current;
      if (current && current !== edge) {
        current.path.style.stroke = "";
        current.path.style.strokeWidth = "";
      }
      hoveredEdgeRef.current = edge;
      edge.path.style.stroke = "#3db8ff";
      edge.path.style.strokeWidth = "3";
      setTooltip({
        startLabel: edge.startLabel,
        endLabel: edge.endLabel,
        protocol: edge.protocol,
        relationship: edge.relationship,
        x: clientX,
        y: clientY,
      });
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
      if (drag) {
        clearHover();
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
        return;
      }

      const edge = edgeAtPointer(host, edgesRef.current, event.clientX, event.clientY);
      if (edge) showHover(edge, event.clientX, event.clientY);
      else if (hoveredEdgeRef.current) clearHover();
    };

    const onPointerLeave = (event: PointerEvent) => {
      if (dragRef.current) return;
      if (event.relatedTarget instanceof Node && host.contains(event.relatedTarget)) return;
      clearHover();
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
    host.addEventListener("pointerleave", onPointerLeave);

    return () => {
      host.removeEventListener("wheel", onWheel);
      host.removeEventListener("pointerdown", onPointerDown);
      host.removeEventListener("pointermove", onPointerMove);
      host.removeEventListener("pointerup", onPointerUp);
      host.removeEventListener("pointercancel", onPointerUp);
      host.removeEventListener("pointerleave", onPointerLeave);
      clearHover();
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
        <span className="diagram-hint">Hover a line for the relationship · drag nodes · scroll to zoom</span>
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
      {tooltip
        ? createPortal(
            <div
              ref={tooltipElRef}
              className="diagram-tooltip"
              role="tooltip"
              style={{
                left: `${Math.max(12, Math.min(tooltip.x + 18, window.innerWidth - 380))}px`,
                top: `${Math.max(12, Math.min(tooltip.y + 18, window.innerHeight - 200))}px`,
              }}
            >
              <div className="diagram-tooltip-content">
                <strong>
                  {tooltip.startLabel} → {tooltip.endLabel}
                </strong>
                {tooltip.protocol ? (
                  <span className="diagram-tooltip-protocol">{tooltip.protocol}</span>
                ) : null}
                <p className="diagram-tooltip-description">{tooltip.relationship}</p>
              </div>
            </div>,
            document.body,
          )
        : null}
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
  writeEdgePath(edge.hitPath, points); // Also update the hit path
  try {
    edge.path.setAttribute("data-points", btoa(JSON.stringify(points)));
    edge.hitPath.setAttribute("data-points", btoa(JSON.stringify(points)));
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

function applyEdgeNote(edge: EdgeInfo, edgeNotes: DiagramEdgeNote[]) {
  const note = lookupEdgeNote(edgeNotes, edge.start, edge.end);
  if (note?.from_label) edge.startLabel = note.from_label;
  if (note?.to_label) edge.endLabel = note.to_label;
  if (note?.label) edge.protocol = note.label;
  if (note?.explanation?.trim()) edge.relationship = note.explanation.trim();
}

function edgeAtPointer(
  host: HTMLElement,
  edges: EdgeInfo[],
  clientX: number,
  clientY: number,
): EdgeInfo | null {
  const stack = document.elementsFromPoint(clientX, clientY);
  for (const el of stack) {
    if (!(el instanceof Element) || el.closest(".diagram-tooltip")) continue;
    if (!host.contains(el)) continue;
    for (const edge of edges) {
      if (el === edge.hitPath || el === edge.path) return edge;
      if (edge.label && (el === edge.label || edge.label.contains(el))) return edge;
    }
  }
  const top = stack.find((el) => host.contains(el) && el !== host);
  if (top?.closest("g.node, g.cluster")) return null;
  for (const edge of edges) {
    if (pointInStroke(edge.hitPath, clientX, clientY) || pointInStroke(edge.path, clientX, clientY)) {
      return edge;
    }
  }
  return null;
}

function pointInStroke(path: SVGPathElement, clientX: number, clientY: number): boolean {
  if (typeof path.isPointInStroke !== "function") return false;
  const ctm = path.getScreenCTM();
  if (!ctm) return false;
  try {
    const local = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return path.isPointInStroke(local);
  } catch {
    return false;
  }
}

function collectEdgePaths(scene: SVGGElement): SVGPathElement[] {
  const seen = new Set<SVGPathElement>();
  const out: SVGPathElement[] = [];
  const add = (path: SVGPathElement) => {
    if (seen.has(path) || path.getAttribute("data-hit-path") === "true") return;
    seen.add(path);
    out.push(path);
  };
  scene
    .querySelectorAll<SVGPathElement>(
      ".edgePaths path, .edgePath path, path.flowchart-link, path[data-et='edge']",
    )
    .forEach(add);
  scene.querySelectorAll<SVGPathElement>("path[id], path[data-id]").forEach((path) => {
    const id = path.getAttribute("data-id") || path.id || "";
    if (/(?:^|[-_])L_|flowchart-link/.test(id) || path.classList.contains("flowchart-link")) add(path);
  });
  return out;
}

function indexEdges(scene: SVGGElement, nodeKeys: string[], edgeNotes: DiagramEdgeNote[]): EdgeInfo[] {
  const edges: EdgeInfo[] = [];
  const seen = new Set<string>();

  collectEdgePaths(scene).forEach((path) => {
    const edgeId = path.getAttribute("data-id") || stripDiagramPrefix(path.id);
    if (!edgeId || seen.has(edgeId)) return;
    seen.add(edgeId);
    const ends = parseEdgeEndpoints(edgeId, nodeKeys);
    const start = ends?.start || edgeId;
    const end = ends?.end || "";
    const labelEl = (scene
      .querySelector(`.edgeLabel [data-id="${cssEscape(edgeId)}"]`)
      ?.closest("g.edgeLabel") ?? null) as SVGGElement | null;

    let protocol: string | undefined;
    if (labelEl) {
      const labelSpan = labelEl.querySelector("span, p, div");
      const fromLabel = labelSpan?.textContent?.trim();
      if (fromLabel) protocol = fromLabel;
    }

    path.parentElement?.setAttribute("pointer-events", "visiblePainted");
    path.style.pointerEvents = "stroke";

    const note = lookupEdgeNote(edgeNotes, start, end);
    const startLabel = note?.from_label || start;
    const endLabel = note?.to_label || end || "connected component";
    const relationship =
      note?.explanation?.trim()
      || (protocol && protocol.length > 40 ? protocol : undefined)
      || `${startLabel} connects to ${endLabel}.`;

    const hitPath = createHitPath(path, `${startLabel} → ${endLabel}. ${relationship}`);

    const edge: EdgeInfo = {
      id: edgeId,
      start,
      end,
      startLabel,
      endLabel,
      path,
      hitPath,
      label: labelEl,
      protocol: note?.label || (protocol && protocol.length <= 40 ? protocol : undefined),
      relationship,
    };
    applyEdgeNote(edge, edgeNotes);
    edges.push(edge);
  });

  return edges;
}

function createHitPath(originalPath: SVGPathElement, label: string): SVGPathElement {
  const hitPath = originalPath.cloneNode(false) as SVGPathElement;
  hitPath.setAttribute("data-hit-path", "true");
  hitPath.removeAttribute("marker-end");
  hitPath.removeAttribute("marker-start");
  hitPath.removeAttribute("style");
  hitPath.removeAttribute("class");
  hitPath.setAttribute("fill", "none");
  hitPath.setAttribute("stroke", "rgba(0, 48, 96, 0.08)");
  hitPath.setAttribute("stroke-width", "28");
  hitPath.setAttribute("stroke-linecap", "round");
  hitPath.setAttribute("stroke-linejoin", "round");
  hitPath.setAttribute("vector-effect", "non-scaling-stroke");
  hitPath.style.pointerEvents = "stroke";
  hitPath.style.cursor = "pointer";
  const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
  title.textContent = label;
  hitPath.appendChild(title);
  originalPath.after(hitPath);
  return hitPath;
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

/** Let node/edge labels paint in full — Mermaid sizes boxes before bold CSS applies. */
function unclipDiagramLabels(svgEl: SVGSVGElement) {
  svgEl.querySelectorAll("foreignObject").forEach((fo) => {
    fo.setAttribute("overflow", "visible");
    const inner = fo.querySelector("div, span, p") as HTMLElement | null;
    if (inner) {
      inner.style.overflow = "visible";
      inner.style.whiteSpace = "pre-wrap";
      inner.style.wordBreak = "break-word";
      inner.style.textOverflow = "unset";
      const extra = 8;
      const width = Math.max(
        Number(fo.getAttribute("width")) || 0,
        Math.ceil(inner.scrollWidth) + extra,
      );
      const height = Math.max(
        Number(fo.getAttribute("height")) || 0,
        Math.ceil(inner.scrollHeight) + extra,
      );
      fo.setAttribute("width", String(width));
      fo.setAttribute("height", String(height));
    }
  });
  svgEl.querySelectorAll("text").forEach((el) => {
    el.removeAttribute("textLength");
    el.removeAttribute("lengthAdjust");
  });
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
