/**
 * Fix common LLM Mermaid mistakes that break the parser,
 * and ensure style fills have readable text contrast.
 */
export function sanitizeMermaidSource(raw: string): string {
  let source = raw.trim();
  if (!source) return source;

  source = source.replace(/^```(?:mermaid)?\s*/i, "").replace(/\s*```$/i, "");

  // Edges first so shape passes don't touch edge text.
  source = source.replace(
    /(--\s*)([^>"\n]+?)(\s*-->)/g,
    (_m, pre: string, label: string, post: string) => {
      const trimmed = label.trim();
      if (!trimmed || trimmed.startsWith("|") || trimmed.startsWith('"')) {
        return `${pre}${label}${post}`;
      }
      if (!needsQuotes(trimmed)) return `${pre}${label}${post}`;
      return `${pre}"${escapeQuotes(trimmed)}"${post}`;
    },
  );

  source = source.replace(/(\|)([^|\n]+?)(\|)/g, (_m, _pre: string, inner: string) => {
    if (inner.startsWith('"') || !needsQuotes(inner)) return `|${inner}|`;
    return `|"${escapeQuotes(inner)}"|`;
  });

  // Only quote rectangle/diamond labels — do not treat (...) as shapes.
  source = quoteShapeLabels(source, "[", "]");
  source = quoteShapeLabels(source, "{", "}");

  source = source
    .split("\n")
    .map((line) => rewriteStyleContrast(line))
    .map((line) => rewriteClassDefContrast(line))
    .join("\n");

  return source;
}

function needsQuotes(label: string): boolean {
  if (!label) return false;
  if (label.startsWith('"') && label.endsWith('"')) return false;
  return /[()[\]{}|/\\@#%&=+]/.test(label);
}

function escapeQuotes(label: string): string {
  return label.replace(/"/g, "#quot;");
}

function quoteShapeLabels(source: string, open: string, close: string): string {
  const openEsc = escapeRegExp(open);
  const closeEsc = escapeRegExp(close);
  const re = new RegExp(
    `(\\b[A-Za-z][\\w-]*\\s*)${openEsc}(?!")([^${closeEsc}\\n]*?)${closeEsc}`,
    "g",
  );
  return source.replace(re, (_m, id: string, label: string) => {
    if (!needsQuotes(label)) return `${id}${open}${label}${close}`;
    return `${id}${open}"${escapeQuotes(label)}"${close}`;
  });
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Ensure `style` / `classDef` fills stay light enough for black bold labels.
 */
function rewriteStyleContrast(line: string): string {
  const match = line.match(/^(\s*style\s+\S+\s+)(.+)$/i);
  if (!match) return line;
  return `${match[1]}${normalizeFillProps(match[2])}`;
}

function rewriteClassDefContrast(line: string): string {
  const match = line.match(/^(\s*classDef\s+\S+\s+)(.+)$/i);
  if (!match) return line;
  return `${match[1]}${normalizeFillProps(match[2])}`;
}

function normalizeFillProps(rawProps: string): string {
  let props = rawProps.trim().replace(/;+\s*$/, "");
  const fillMatch = props.match(/(?:^|,)\s*fill\s*:\s*(#[0-9a-fA-F]{3,8}|[a-zA-Z]+)/);
  if (!fillMatch) return rawProps.trim();

  const fill = ensureLightFill(fillMatch[1]);
  const textColor = "#000000";

  props = props
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p && !/^(fill|color|font-weight)\s*:/i.test(p))
    .join(",");
  props = `fill:${fill}${props ? `,${props}` : ""}`;
  if (!props.toLowerCase().includes("stroke:")) {
    props = `${props},stroke:#4a5a70`;
  }
  return `${props},color:${textColor},font-weight:bold`;
}

function ensureLightFill(fill: string): string {
  const rgb = parseCssColor(fill);
  if (!rgb) return "#b8d4f0";
  if (relativeLuminance(rgb) >= 0.55) {
    return toHex(rgb);
  }
  // Dark fills → light tint of same hue so black text stays readable.
  const lightened: [number, number, number] = [
    Math.round(rgb[0] + (255 - rgb[0]) * 0.72),
    Math.round(rgb[1] + (255 - rgb[1]) * 0.72),
    Math.round(rgb[2] + (255 - rgb[2]) * 0.72),
  ];
  return toHex(lightened);
}

function relativeLuminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function toHex(rgb: [number, number, number]): string {
  return `#${rgb
    .map((c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, "0"))
    .join("")}`;
}

function parseCssColor(value: string): [number, number, number] | null {
  const named: Record<string, [number, number, number]> = {
    white: [255, 255, 255],
    black: [0, 0, 0],
    red: [255, 0, 0],
    green: [0, 128, 0],
    blue: [0, 0, 255],
    yellow: [255, 255, 0],
    cyan: [0, 255, 255],
    magenta: [255, 0, 255],
    gray: [128, 128, 128],
    grey: [128, 128, 128],
  };
  const lower = value.toLowerCase();
  if (named[lower]) return named[lower];

  const hex = value.replace("#", "");
  if (/^[0-9a-fA-F]{3}$/.test(hex)) {
    return [
      parseInt(hex[0] + hex[0], 16),
      parseInt(hex[1] + hex[1], 16),
      parseInt(hex[2] + hex[2], 16),
    ];
  }
  if (/^[0-9a-fA-F]{6}$/.test(hex) || /^[0-9a-fA-F]{8}$/.test(hex)) {
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
    ];
  }
  return null;
}
