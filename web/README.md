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

## Product Permissions

- Ordinary users can create fill tasks and view/download only their own fill-run results.
- Admin users can manage knowledge bases, upload documents, and run ingestion. Their fill-run list still defaults to their own created tasks.
- Workspace selection is retained for compatibility and knowledge-base grouping. It does not expose other users' fill tasks.
- The result UI is download-oriented: users download `filled_form.xlsx` and `review_items.csv/jsonl`; there is no online field review or editing workflow.
