import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

// Built assets are served by the FastAPI backend at the public base URL.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: path.resolve(rootDir, "../backend/src/architect_agent/static"),
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
