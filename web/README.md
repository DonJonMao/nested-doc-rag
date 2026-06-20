# Gongkan Web

Vue 3 frontend for the Gongkan RAG form-filling platform.

## Stack

- Vue 3 + Vite + TypeScript
- Vue Router + Pinia
- Axios
- Element Plus
- `@microsoft/fetch-event-source` for authenticated SSE

## Environment

Copy `.env.example` to `.env.local` when needed:

```env
VITE_API_BASE_URL=http://localhost:8080
VITE_APP_NAME=工勘智能填表
```

## Commands

```bash
npm install
npm run dev
npm run build
```

The UI uses real Go API endpoints. It does not mock fill-run success or fake ingestion status. Long-running ingestion and fill tasks require the Go API server, Go worker, Redis, PostgreSQL, object storage, Python Core, Qdrant, and model providers to be configured.
