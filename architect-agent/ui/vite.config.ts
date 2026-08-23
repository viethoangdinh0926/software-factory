import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(rootDir, "../backend/src/architect_agent/static");
const assetsDir = path.join(staticDir, "assets");

/** Keep prior Mermaid lazy-chunks so an already-open tab can still import them. */
function retainMermaidChunks(): Plugin {
  const keepRe =
    /(?:classDiagram|flowDiagram|stateDiagram|erDiagram|sequenceDiagram|mermaid-parser|diagram-)/i;
  const snapshot: { name: string; data: Buffer }[] = [];
  if (fs.existsSync(assetsDir)) {
    for (const name of fs.readdirSync(assetsDir)) {
      if (keepRe.test(name)) {
        snapshot.push({ name, data: fs.readFileSync(path.join(assetsDir, name)) });
      }
    }
  }
  return {
    name: "retain-mermaid-chunks",
    apply: "build",
    closeBundle() {
      if (!fs.existsSync(assetsDir)) return;
      for (const file of snapshot) {
        const dest = path.join(assetsDir, file.name);
        if (!fs.existsSync(dest)) {
          fs.writeFileSync(dest, file.data);
        }
      }
    },
  };
}

// Built assets are served by the FastAPI backend at the public base URL.
export default defineConfig({
  plugins: [react(), retainMermaidChunks()],
  base: "/",
  build: {
    outDir: staticDir,
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/design": "http://127.0.0.1:8080",
      "/api": "http://127.0.0.1:8080",
      "/healthz": "http://127.0.0.1:8080",
    },
  },
});
