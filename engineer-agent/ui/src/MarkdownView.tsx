import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  content: string;
  className?: string;
};

const LATEX_SYMBOLS: Record<string, string> = {
  approx: "≈",
  times: "×",
  cdot: "·",
  leq: "≤",
  geq: "≥",
  neq: "≠",
  sim: "~",
  infty: "∞",
};

/** Turn `$\\text{...}$` / `$\\approx$` into readable text so GFM does not mangle `_`. */
function softenLatex(content: string): string {
  if (!content) return content;
  if (!content.includes("$") && !content.includes("\\") && !content.includes("\t")) {
    return content;
  }
  let text = content.replace(/\$\$([\s\S]+?)\$\$/g, "$1");
  text = text.replace(/\$([^$\n]+)\$/g, (_m, inner: string) => decodeLatexSpan(inner));
  return decodeLatexSpan(text);
}

function decodeLatexSpan(inner: string): string {
  return inner
    .replace(/(?:\t|\\t)ext(?:rm|bf|it|tt)?\{([^{}]*)\}/g, "$1")
    .replace(/\\text(?:rm|bf|it|tt)?\{([^{}]*)\}/g, "$1")
    .replace(/\\(approx|times|cdot|leq|geq|neq|sim|infty)\b/g, (_m, name: string) => {
      return LATEX_SYMBOLS[name] ?? name;
    })
    .replace(/\\([A-Za-z]+)\s*/g, "$1 ");
}

/** Keep `<User Service>`-style labels; only real HTML tags stay tags. */
const KNOWN_HTML_TAG = /^(?:\/)?(?:a|abbr|b|blockquote|br|code|div|em|h[1-6]|hr|i|img|li|ol|p|pre|span|strong|table|tbody|td|th|thead|tr|ul)(?:\s|\/|$)/i;

export function preserveAngleBrackets(content: string): string {
  if (!content.includes("<")) return content;
  return content.replace(/<([^>\n]{1,120})>/g, (full, inner: string) => {
    const body = inner.trim();
    if (!body || body.startsWith("!--") || body.startsWith("?") || KNOWN_HTML_TAG.test(body)) {
      return full;
    }
    return `&lt;${inner}&gt;`;
  });
}

export function MarkdownView({ content, className }: Props) {
  return (
    <div className={className ? `md ${className}` : "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {preserveAngleBrackets(softenLatex(content || ""))}
      </ReactMarkdown>
    </div>
  );
}
