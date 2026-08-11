import { useEffect, useId, useState } from "react";
import mermaid from "mermaid";

type Props = { source: string };

export function MermaidDiagram({ source }: Props) {
  const reactId = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function render() {
      if (!source.trim()) {
        setSvg("");
        setError(null);
        return;
      }
      try {
        const { svg: next } = await mermaid.render(`mmd-${reactId}-${Date.now()}`, source);
        if (!cancelled) {
          setSvg(next);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setSvg("");
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    }
    void render();
    return () => {
      cancelled = true;
    };
  }, [source, reactId]);

  if (!source.trim()) {
    return <div className="diagram">No diagram yet.</div>;
  }
  if (error) {
    return (
      <div className="diagram">
        <pre>{source}</pre>
        <p className="error">{error}</p>
      </div>
    );
  }
  return <div className="diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}
