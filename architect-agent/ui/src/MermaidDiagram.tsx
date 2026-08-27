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
  originalD: string;
  pathStart: Point;
  pathEnd: Point;
  startCenter0: Point;
  endCenter0: Point;
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
        
        edgesRef.current = indexEdges(scene, nodes, edgeNotesRef.current);
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

/** Keep Mermaid’s original curve; retarget the endpoints to the live node borders. */
function rerouteEdge(edge: EdgeInfo, nodes: Map<string, SVGGElement>) {
  const startEl = nodes.get(edge.start);
  const endEl = nodes.get(edge.end);
  if (!startEl || !endEl) return;

  const startCenter = nodeCenter(startEl);
  const endCenter = nodeCenter(endEl);
  const startPt = {
    x: edge.pathStart.x + (startCenter.x - edge.startCenter0.x),
    y: edge.pathStart.y + (startCenter.y - edge.startCenter0.y),
  };
  const endPt = {
    x: edge.pathEnd.x + (endCenter.x - edge.endCenter0.x),
    y: edge.pathEnd.y + (endCenter.y - edge.endCenter0.y),
  };

  const nextD =
    edge.start === edge.end
      ? translatePath(edge.originalD, {
          x: startPt.x - edge.pathStart.x,
          y: startPt.y - edge.pathStart.y,
        })
      : keepOriginalCurve(edge.originalD)
        ? remapPath(edge.originalD, edge.pathStart, edge.pathEnd, startPt, endPt) ||
          cubicBetween(startPt, endPt)
        : cubicBetween(startPt, endPt);

  edge.path.setAttribute("d", nextD);
  edge.hitPath.setAttribute("d", nextD);

  if (edge.label) {
    try {
      const len = edge.path.getTotalLength();
      const mid = edge.path.getPointAtLength(len / 2);
      writeTranslate(edge.label, mid.x, mid.y);
    } catch {
      writeTranslate(edge.label, (startPt.x + endPt.x) / 2, (startPt.y + endPt.y) / 2);
    }
  }
}

function nodeCenter(el: SVGGElement): Point {
  return readTranslate(el);
}

function keepOriginalCurve(d: string): boolean {
  if (/[CcQqSsAa]/.test(d)) return true;
  const nums = d.match(/[-+]?(?:\d*\.\d+|\d+)/g) || [];
  return nums.length >= 12;
}

function cubicBetween(from: Point, to: Point): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy);
  if (dist < 0.5) return `M ${fmt(from.x)} ${fmt(from.y)} L ${fmt(to.x)} ${fmt(to.y)}`;
  const lift = Math.min(48, dist * 0.22);
  const nx = (-dy / dist) * lift;
  const ny = (dx / dist) * lift;
  const c1x = from.x + dx * 0.35 + nx;
  const c1y = from.y + dy * 0.35 + ny;
  const c2x = from.x + dx * 0.65 + nx;
  const c2y = from.y + dy * 0.65 + ny;
  return `M ${fmt(from.x)} ${fmt(from.y)} C ${fmt(c1x)} ${fmt(c1y)} ${fmt(c2x)} ${fmt(c2y)} ${fmt(to.x)} ${fmt(to.y)}`;
}

function fmt(n: number): string {
  return (Math.round(n * 100) / 100).toString();
}

type PathCmd = { cmd: string; nums: number[] };

function tokenizePath(d: string): PathCmd[] {
  const out: PathCmd[] = [];
  const re = /([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(d || ""))) {
    const nums = (match[2].match(/[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?/g) || []).map(Number);
    out.push({ cmd: match[1], nums });
  }
  return out;
}

function toAbsolutePath(cmds: PathCmd[]): PathCmd[] {
  let x = 0;
  let y = 0;
  let startX = 0;
  let startY = 0;
  const abs: PathCmd[] = [];

  for (const { cmd, nums } of cmds) {
    const upper = cmd.toUpperCase();
    const rel = cmd !== upper;

    if (upper === "Z") {
      abs.push({ cmd: "Z", nums: [] });
      x = startX;
      y = startY;
      continue;
    }

    if (upper === "H") {
      const pts: number[] = [];
      for (const n of nums) {
        x = rel ? x + n : n;
        pts.push(x, y);
      }
      abs.push({ cmd: "L", nums: pts });
      continue;
    }

    if (upper === "V") {
      const pts: number[] = [];
      for (const n of nums) {
        y = rel ? y + n : n;
        pts.push(x, y);
      }
      abs.push({ cmd: "L", nums: pts });
      continue;
    }

    if (upper === "A") {
      const next: number[] = [];
      for (let i = 0; i + 6 < nums.length; i += 7) {
        const nx = rel ? x + nums[i + 5] : nums[i + 5];
        const ny = rel ? y + nums[i + 6] : nums[i + 6];
        next.push(nums[i], nums[i + 1], nums[i + 2], nums[i + 3], nums[i + 4], nx, ny);
        x = nx;
        y = ny;
      }
      abs.push({ cmd: "A", nums: next });
      continue;
    }

    const stride = upper === "C" ? 6 : upper === "S" || upper === "Q" ? 4 : 2;
    const next: number[] = [];
    for (let i = 0; i < nums.length; i += stride) {
      const chunk = nums.slice(i, i + stride);
      const originX = x;
      const originY = y;
      for (let j = 0; j + 1 < chunk.length; j += 2) {
        const nx = rel ? originX + chunk[j] : chunk[j];
        const ny = rel ? originY + chunk[j + 1] : chunk[j + 1];
        chunk[j] = nx;
        chunk[j + 1] = ny;
        if (stride === 2 || j + 2 >= chunk.length) {
          x = nx;
          y = ny;
        }
      }
      next.push(...chunk);
      if (upper === "M" && i === 0) {
        startX = x;
        startY = y;
      }
    }

    if (upper === "M" && next.length > 2) {
      abs.push({ cmd: "M", nums: next.slice(0, 2) });
      abs.push({ cmd: "L", nums: next.slice(2) });
    } else {
      abs.push({ cmd: upper, nums: next });
    }
  }

  return abs;
}

function pathTerminals(cmds: PathCmd[]): { start: Point; end: Point } | null {
  let start: Point | null = null;
  let x = 0;
  let y = 0;
  for (const { cmd, nums } of cmds) {
    if (cmd === "Z" || nums.length < 2) continue;
    if (cmd === "A") {
      for (let i = 0; i + 6 < nums.length; i += 7) {
        x = nums[i + 5];
        y = nums[i + 6];
        if (!start) start = { x, y };
      }
      continue;
    }
    for (let i = 0; i + 1 < nums.length; i += 2) {
      x = nums[i];
      y = nums[i + 1];
      if (!start) start = { x, y };
    }
  }
  return start ? { start, end: { x, y } } : null;
}

function mapPathCommands(cmds: PathCmd[], mapPt: (p: Point) => Point, scale = 1): PathCmd[] {
  return cmds.map(({ cmd, nums }) => {
    if (cmd === "Z" || nums.length === 0) return { cmd, nums };
    if (cmd === "A") {
      const next: number[] = [];
      for (let i = 0; i + 6 < nums.length; i += 7) {
        const mapped = mapPt({ x: nums[i + 5], y: nums[i + 6] });
        next.push(nums[i] * scale, nums[i + 1] * scale, nums[i + 2], nums[i + 3], nums[i + 4], mapped.x, mapped.y);
      }
      return { cmd, nums: next };
    }
    const next = nums.slice();
    for (let i = 0; i + 1 < next.length; i += 2) {
      const mapped = mapPt({ x: next[i], y: next[i + 1] });
      next[i] = mapped.x;
      next[i + 1] = mapped.y;
    }
    return { cmd, nums: next };
  });
}

function serializePath(cmds: PathCmd[]): string {
  return cmds
    .map(({ cmd, nums }) => (nums.length ? `${cmd} ${nums.map(fmt).join(" ")}` : cmd))
    .join(" ");
}

function chordMap(fromA: Point, fromB: Point, toA: Point, toB: Point): { map: (p: Point) => Point; scale: number } {
  const v0x = fromB.x - fromA.x;
  const v0y = fromB.y - fromA.y;
  const v1x = toB.x - toA.x;
  const v1y = toB.y - toA.y;
  const len0 = Math.hypot(v0x, v0y) || 1;
  const len1 = Math.hypot(v1x, v1y) || 1;
  const scale = len1 / len0;
  const dAng = Math.atan2(v1y, v1x) - Math.atan2(v0y, v0x);
  const cos = Math.cos(dAng);
  const sin = Math.sin(dAng);
  return {
    scale,
    map: (p: Point) => {
      const lx = p.x - fromA.x;
      const ly = p.y - fromA.y;
      return {
        x: toA.x + (lx * cos - ly * sin) * scale,
        y: toA.y + (lx * sin + ly * cos) * scale,
      };
    },
  };
}

function remapPath(d: string, fromStart: Point, fromEnd: Point, toStart: Point, toEnd: Point): string | null {
  const cmds = toAbsolutePath(tokenizePath(d));
  if (!cmds.length) return null;
  const { map, scale } = chordMap(fromStart, fromEnd, toStart, toEnd);
  return serializePath(mapPathCommands(cmds, map, scale));
}

function translatePath(d: string, delta: Point): string {
  const cmds = toAbsolutePath(tokenizePath(d));
  if (!cmds.length) return d;
  return serializePath(
    mapPathCommands(cmds, (p) => ({ x: p.x + delta.x, y: p.y + delta.y })),
  );
}

function pathStartEnd(d: string): { start: Point; end: Point } | null {
  return pathTerminals(toAbsolutePath(tokenizePath(d)));
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

function indexEdges(
  scene: SVGGElement,
  nodes: Map<string, SVGGElement>,
  edgeNotes: DiagramEdgeNote[],
): EdgeInfo[] {
  const edges: EdgeInfo[] = [];
  const seen = new Set<string>();
  const nodeKeys = [...nodes.keys()];

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
    const originalD = path.getAttribute("d") || "";
    const terminals = pathStartEnd(originalD) || { start: { x: 0, y: 0 }, end: { x: 1, y: 0 } };

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
      originalD,
      pathStart: terminals.start,
      pathEnd: terminals.end,
      startCenter0: nodes.get(start) ? nodeCenter(nodes.get(start)!) : terminals.start,
      endCenter0: nodes.get(end) ? nodeCenter(nodes.get(end)!) : terminals.end,
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
  hitPath.setAttribute("stroke", "#000000");
  hitPath.setAttribute("stroke-opacity", "0");
  hitPath.setAttribute("stroke-width", "28");
  hitPath.setAttribute("stroke-linecap", "round");
  hitPath.setAttribute("stroke-linejoin", "round");
  hitPath.setAttribute("vector-effect", "non-scaling-stroke");
  hitPath.style.fill = "none";
  hitPath.style.stroke = "#000000";
  hitPath.style.strokeOpacity = "0";
  hitPath.style.strokeWidth = "28";
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
