# Architect Agent UI

React + TypeScript (Vite) SPA for the Architect agent.

Build output is written to `../backend/src/architect_agent/static` and served by FastAPI at the service public base URL.

```bash
npm install
npm run dev      # proxies /design and /api to backend :8080
npm run build    # emit static assets into backend
```
