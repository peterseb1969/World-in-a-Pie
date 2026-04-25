# WIP — Completed Features

Detailed write-ups for features that have been fully implemented. For current priorities, see `roadmap.md`.

---

## v1.1 — MCP Read-Only Mode + NL Query Scaffold

### MCP Read-Only Mode (2026-03-30)

`WIP_MCP_MODE=readonly` env var that prevents registration of `create_*`, `import_*`, `archive_*`, `deactivate_*` tools. Same server, same code — the MCP protocol already handles tool visibility. Pairs with the `/analyst` slash command to create a Query Claude that physically cannot modify the data model.

- Removes 31 write tools, 38 read tools remain
- 4 tests
- Design: `docs/design/nl-query-scaffold.md`

### NL Query Scaffold (2026-03-31)

Turned the natural language interface pattern (validated in WIP-DnD Compendium with 1,384 entities) into reusable WIP infrastructure + app scaffolding. Every new WIP app is NL-ready out of the box via `create-app-project.sh --preset query`.

Four deliverables:
1. **`describe_data_model` MCP tool** — returns all active templates with fields, formatted for system prompt injection. Paginated, all fields inline.
2. **`wip://query-assistant-prompt` MCP resource** — complete system prompt combining generic query instructions + live template catalog. Apps read this at startup.
3. **`--preset query` scaffold** — `create-app-project.sh` generates a working NL app: Express backend with Claude agentic loop, React chat widget, Vite proxy, all wired up.
4. **Architecture guide** (`docs/nl-interface-guide.md`) — rationale behind key decisions (Haiku for cost, server-side sessions for security, dynamic prompts for compaction resilience).

No `@wip/agent` library yet — the agent loop is scaffolded as owned code. Extract into a package after 3+ apps stabilize the pattern.

- 11 tests
- Design: `docs/design/nl-query-scaffold.md`
- Validated by: WIP-DnD Compendium

---

## Bug Fixes

### Reporting-Sync: Template Deactivation Not Synced (2026-03-29)

Template status changes (deactivated/deleted) were NOT propagated to PostgreSQL. `_process_template_event()` handled all template events identically and never checked for `template.deleted`. No `_wip_templates` metadata table existed.

**Impact:** DnD Compendium agent read from PostgreSQL, couldn't detect deactivated templates, ran in circles creating workarounds.

**Fix:** Added `_wip_templates` metadata table (like `terminologies`), event-driven + batch sync, tests.

- Discovered: 2026-03-28 during DnD K8s deployment

### Reporting-Sync: `document.archived` Events Not Synced (2026-03-30)

Archived documents remained `active` in PostgreSQL because `_process_document_event()` fell through to the upsert path for `document.archived` events.

**Fix:** Handle `document.archived` alongside `document.deleted` — both now update the PostgreSQL status column (`"archived"` or `"deleted"` respectively). Unit + E2E lifecycle tests.

- Discovered: 2026-03-29 during reporting integration testing
- Related to: Template deactivation bug (below)

### Console: Files Page Ignores Namespace (2026-03-29)

The files page (`/files`) always queried `namespace=wip` regardless of the selected namespace. Files uploaded to other namespaces (e.g., `dnd`) were invisible in the UI.

**Fix:** Pass active namespace from Console's namespace selector to the files API call.

- Discovered: 2026-03-28 during DnD data migration to K8s

---

## Features

### `@wip/client` Completion (2026-03-29)

`@wip/client` is the ONLY supported path for AI-assisted app development. Every app and agent that bypasses the client hits the same WIP conventions (bulk-first 200 OK, identity dedup, synonym resolution, pagination) and wastes significant time working around them.

**Implemented:**
- `files.uploadFile()`, `files.downloadFileContent()` — file operations
- `reporting.runQuery()`, `reporting.listTables()`, `reporting.getTableSchema()`, `reporting.getSyncStatus()` — SQL query execution and table introspection
- `templates.createTemplates()` — bulk template creation
- `awaitSync()` — wait for reporting-sync to catch up
- Server-side auth handled by `@wip/proxy` (no client change needed)
- All bulk methods exposed alongside single-item convenience methods

**Remaining:** Sync-aware read guidance documentation (API for state, PG for analytics).

### App Gateway — `@wip/proxy` (2026-03-29)

Every browser-based WIP app needs auth injection (browser can't hold API keys) and MinIO URL rewriting. The DnD Compendium solved this with 70 lines of hand-rolled Express proxy. `@wip/proxy` replaces that.

Express middleware that any app drops in with one line. Handles API key injection and file content proxying. Works on localhost and K8s identically.

- DnD Compendium refactored to use it
- `create-app-project.sh` updated
- Design: `docs/design/app-gateway.md`
- Note: App Gateway Phase 2-3 (multi-app routing) tracked separately in roadmap

### App User Authentication — Phase 1 (2026-03-31)

Apps had no user authentication — anyone on the network could use them. Phase 1 adds app-side OIDC via Dex.

**Key finding:** The design doc originally assumed Caddy had a `caddy-security` OIDC plugin. It does not — the Console handles OIDC entirely client-side (`oidc-client-ts`). Phase 1 was redesigned to use app-side OIDC instead.

**Components:**
1. `TrustedHeaderProvider` in wip-auth 0.4.0 — accepts `X-WIP-User` + `X-WIP-Groups` when valid API key present (anti-spoofing)
2. `forwardIdentity` option in @wip/proxy 0.2.0 — forwards identity headers to WIP services
3. `wip-apps` Dex client registered (ports 3001-3005)
4. Query scaffold auth middleware (`server/auth.ts`) — OIDC via `openid-client`, PKCE flow, `express-session`

**Opt-in:** Set `OIDC_ISSUER` to enable. No auth = local dev mode.

- Design: `docs/design/authentication-authorization.md`
- Note: Phase 2 (namespace permissions) and Phase 3 (audit trails) tracked separately in roadmap

### Mutable Terminologies (2026-03-29)

`mutable: true` flag on terminologies for user-editable controlled vocabularies. Mutable terminologies allow real term deletion (with relation cascade) while using the full terminology infrastructure — ontology relations, reporting-sync, MCP tools, ontology browser.

**Why:** Apps that need user-defined picklists, tags, or classifications previously had to reinvent terminology semantics using documents. The ClinTrial app exposed this.

**Scope:** `mutable` defaults to `false` — zero impact on existing terminologies. Term delete becomes hard-delete + relation cascade for mutable terms.

- Implemented: Def-Store, Reporting-Sync, WIP-Toolkit, @wip/client, Console UI
- Design: `docs/design/mutable-terminologies.md`

### Hard Delete for All Entity Types (2026-03-30)

When a namespace has `deletion_mode: "full"`, any entity type can be hard-deleted via `hard_delete: bool` on DeleteItem. Covers documents, templates, terminologies, terms, and relations. Version-specific hard-delete supported for documents and templates. Registry entries cleaned up when last version is removed. Reporting-sync handles hard-delete events with `DELETE FROM` instead of `UPDATE status`.

- All services, MCP tools, @wip/client, Console. 43 tests.
- Discovered: 2026-03-30 during ClinTrial import testing

### Kubernetes Deployment (2026-03-28)

Deployed and validated on a 3-node MicroK8s cluster (Raspberry Pi 5, aarch64, Rook-Ceph storage). All 13 pods running, full end-to-end including Console login, OIDC, seeding, and remote MCP access.

**What's done:**
- All K8s manifests validated: 5 infrastructure StatefulSets/Deployments, 7 service Deployments, NGINX Ingress with 9 routes
- Rook-Ceph block storage for all PVCs (38Gi+ allocated)
- K8s Secrets for credentials, ConfigMaps for service config + Dex OIDC
- Self-signed TLS via Ingress, Dex OIDC with group claims
- MCP server with HTTP streamable transport, accessible from Claude Code over HTTPS
- Remote seeding from Mac via `--host --via-proxy --port 443`


Note: Helm chart packaging, Network Policies, and production hardening tracked separately in roadmap.

---

## Test Infrastructure

### MCP Server Transport Regression Tests (2026-03-31)

14 end-to-end tests using real MCP client connections across stdio, HTTP streamable, and SSE transports.

Coverage: tool listing, resource listing, readonly mode, API key accept/reject, custom port, no-key warning, cross-transport consistency (identical tool sets and resource sets).

- File: `components/mcp-server/tests/test_transports.py`

### PostgreSQL Reporting Integration Tests (2026-03-30)

90+ real PostgreSQL integration tests across 3 files using `POSTGRES_TEST_URI` with graceful skip markers when DB unavailable. 308 total test methods across all reporting-sync test files.

- `test_integration.py` — 60+ tests: schema creation, data type mapping, schema evolution, sync strategies, metadata tables, namespace deletion, edge cases
- `test_e2e.py` — 20+ tests: full NATS→SyncWorker→PostgreSQL pipeline for all entity types including hard-delete
- `test_entity_lifecycle.py` — 10+ tests: complete create→update→archive→delete lifecycle

---

## Platform & Infrastructure (older)

- DnD Compendium K8s deployment — first WIP app on Pi cluster (2026-03-28)
- Complete ID pass-through for restore — all entity types preserve original IDs, 527/527 verified (2026-03-28)
- Namespace deletion — persistent journal, crash-safe, dry-run, inbound reference checking, MCP tool (2026-03-27)
- Ontology browser — ego-graph with Cytoscape.js, click-to-navigate, cross-namespace traversal (2026-03-27)
- Reporting-sync terminology/term population — startup batch sync + event-driven sync (2026-03-27)
- Namespace-required propagation — removed all `namespace="wip"` defaults across full stack (2026-03-30)
- Backend developer agent setup — `setup-backend-agent.sh`, role-specific CLAUDE.md, 8 backend slash commands (2026-03-30)
- Windows/WSL platform support — auto-detection, named volume overlays
- Binary file storage (MinIO) — full CRUD, UI, reference tracking, orphan detection
- Semantic types — 7 types (email, url, lat/lon, percentage, duration, geo_point)
- Ontology support — OBO Graph JSON import, typed relations, traversal
- Template draft mode — draft status, cascading activation
- MCP server — 70+ tools, 5 resources, stdio + SSE + HTTP streamable transport
- @wip/client + @wip/react — TypeScript client and React hooks
- CSV/XLSX import — preview + import endpoints in Document-Store
- Event replay — start, pause, resume, cancel via API and MCP tools
- Bulk-first API convention — all write endpoints accept arrays
- Security hardening — CORS, rate limiting, bcrypt keys, upload limits, security headers
- Information package for app-building AI — slash commands, reference docs, MCP resources
- Universal synonym resolution — auto-synonym registration, resolve layer at API boundary, WIP-Toolkit backfill + namespace rewriting
