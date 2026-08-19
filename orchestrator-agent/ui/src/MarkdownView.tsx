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

export function MarkdownView({ content, className }: Props) {
  return (
    <div className={className ? `md ${className}` : "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{softenLatex(content)}</ReactMarkdown>
    </div>
  );
}
