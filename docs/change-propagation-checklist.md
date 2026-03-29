# Change Propagation Checklist

When adding or modifying a field, feature, or behavior in WIP, changes must propagate across multiple layers. Use this checklist to ensure nothing is missed.

Not every change touches every layer — use judgement. But review the full list before considering the work done.

---

## Core

- [ ] **Service API model** — request/response schemas in the relevant component (`components/<name>/src/*/models/api_models.py`)
- [ ] **Service domain model** — MongoDB document model (`models/*.py`)
- [ ] **Service logic** — business rules, validation, side effects (`services/*.py`)
- [ ] **NATS events** — event payloads if the change affects what downstream consumers need
- [ ] **Reporting-Sync** — `worker.py` (event handling), `batch_sync.py` (full-sync), `schema_manager.py` (PG DDL)

## Client Libraries

- [ ] **@wip/client** (`libs/wip-client/`) — TypeScript types, API methods if applicable
- [ ] **@wip/react** (`libs/wip-react/`) — hooks for new mutations or queries
- [ ] **Version bump** — bump `package.json` version for both libs after changes
- [ ] **Rebuild tarballs** — `npm pack` in each lib, scp to consumer Pis if applicable

## UI

- [ ] **WIP Console** (`ui/wip-console/`) — types, forms, list views, detail views
- [ ] **Console rebuild** — `npm run build` after changes (served as static files via Caddy)

## MCP Server

- [ ] **MCP tools** (`components/mcp-server/src/wip_mcp/server.py`) — expose new parameters on relevant tools
- [ ] **MCP client** (`components/mcp-server/src/wip_mcp/client.py`) — update if API call signature changed

## WIP-Toolkit

- [ ] **Import** — `WIP-Toolkit/src/wip_toolkit/import_/restore.py` and `fresh.py` (explicit field mapping)
- [ ] **Export** — usually pass-through, but verify

## Scripts

- [ ] **`scripts/seed_comprehensive.py`** — seed data should exercise new features
- [ ] **`scripts/dev-delete.py`** — delete logic should handle new fields/behaviors
- [ ] **`scripts/setup.sh`** — if new infrastructure or config is needed
- [ ] **Other scripts** — `import_testdata.py`, `import_obo_graph.py`, etc. if relevant

## Tests

- [ ] **Component tests** — unit/integration tests for the service that changed
- [ ] **Reporting-sync tests** — if event handling or PG schema changed
- [ ] **Client lib tests** — type compilation, hook tests
- [ ] **E2E** — manual or scripted verification through the full stack

## Documentation

- [ ] **Design doc** (`docs/design/`) — update status if implementing a planned feature
- [ ] **Roadmap** (`docs/roadmap.md`) — update progress
- [ ] **API docs** — if endpoint signatures changed

---

## Example: Adding a boolean field to Terminology

| Layer | File(s) | What to do |
|-------|---------|------------|
| Domain model | `terminology.py` | Add field with default |
| API models | `api_models.py` | Add to Create/Update/Response |
| Service | `terminology_service.py` | Validation rules, side effects |
| Events | `terminology_service.py` | Include in event payload |
| Reporting-Sync | `schema_manager.py`, `worker.py`, `batch_sync.py` | PG column, upsert, event handling |
| @wip/client | `types/terminology.ts` | Add to TS interfaces |
| @wip/react | `use-mutations.ts` | New hooks if needed |
| MCP server | `server.py` | Add parameter to create/update tools |
| WIP-Toolkit | `restore.py`, `fresh.py` | Add to import payload mapping |
| Console UI | Form, List, Detail views | Checkbox/tag/info display |
| Scripts | `seed_comprehensive.py`, `dev-delete.py` | Exercise the new field |
| Tests | Component + reporting-sync tests | Cover new behavior |
