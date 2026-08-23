import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: path.resolve(rootDir, "../backend/src/engineer_agent/static"),
    emptyOutDir: true,
  },
  server: {
    port: 5175,
    proxy: {
      "/ingest": "http://127.0.0.1:8091",
      "/api": {
        target: "http://127.0.0.1:8091",
        timeout: 0,
      },
      "/healthz": "http://127.0.0.1:8091",
    },
  },
});
