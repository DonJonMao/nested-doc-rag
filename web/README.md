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
npm run typecheck
npm test
npm run build
```

The UI uses real Go API endpoints. It does not mock fill-run success or fake ingestion status. Long-running ingestion and fill tasks require the Go API server, Go worker, Redis, PostgreSQL, object storage, Python Core, Qdrant, and model providers to be configured.

## Product Permissions

- Ordinary users can create fill tasks and view/download only their own fill-run results.
- Admin users can manage knowledge bases, upload documents, and run ingestion. Their fill-run list still defaults to their own created tasks.
- Workspace selection is retained for compatibility and knowledge-base grouping. It does not expose other users' fill tasks.
- The result UI is download-oriented: users download `filled_form.xlsx` and `review_items.csv/jsonl`; there is no online field review or editing workflow.

## Field evidence workbench

The fill-run detail uses the authenticated API's `evidence.fields` to show all field outcomes, actual writeback actions, gate reasons and separately archived source quotations. Exact matches use Unicode code-point offsets and are checked against source text again before highlighting. A located quotation does not establish that the answer is correct or permitted for writeback.

Older results fall back to every `writeback.fields` entry and clearly indicate that source-span records are unavailable. References are rendered through Vue text nodes; no HTML from source documents is executed. Image downloads use the existing owner-only evidence endpoint. Unit and mounted-component tests cover Unicode spans, invalid/ambiguous matches, search, independent raw-status filters, keyboard focus, result refreshes and legacy results.
