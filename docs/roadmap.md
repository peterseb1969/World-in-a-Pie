# WIP Roadmap

Future plans, pending features, and design specifications.

---

## v1.1

**Focus:** MCP Read-Only Mode + Natural Language Query Scaffold (flagship feature).

### MCP Read-Only Mode

`WIP_MCP_MODE=readonly` env var that prevents registration of `create_*`, `import_*`, `archive_*`, `deactivate_*` tools. Same server, same code — the MCP protocol already handles tool visibility. Pairs with the `/analyst` slash command (already implemented) to create a Query Claude that physically cannot modify the data model. Prerequisite for the NL Query Scaffold (below).

- Status: Not started (small, ~1 hour)

### NL Query Scaffold

Turn the natural language interface pattern (validated in WIP-DnD Compendium with 1,384 entities) into reusable WIP infrastructure + app scaffolding. Every new WIP app should be NL-ready out of the box via `create-app-project.sh --preset query`.

Four deliverables:
1. **`describe_data_model` MCP tool** — returns all active templates with fields, formatted for system prompt injection. Replaces hardcoded template catalogs.
2. **`wip://query-assistant-prompt` MCP resource** — complete system prompt combining generic query instructions + live template catalog. Apps read this at startup.
3. **`--preset query` scaffold** — `create-app-project.sh` generates a working NL app: Express backend with Claude agentic loop, React chat widget, Vite proxy, all wired up.
4. **Architecture guide** (`docs/nl-interface-guide.md`) — rationale behind key decisions (Haiku for cost, server-side sessions for security, dynamic prompts for compaction resilience).

No `@wip/agent` library yet — the agent loop is scaffolded as owned code. Extract into a package after 3+ apps stabilize the pattern.

- Design: `docs/design/nl-query-scaffold.md`
- Depends on: MCP Read-Only Mode
- Validated by: WIP-DnD Compendium
- Status: Design complete

---

## Near-Term

### BUG: Reporting-Sync — Template Deactivation Not Synced

Template status changes (deactivated/deleted) are NOT propagated to PostgreSQL. `_process_template_event()` handles all template events identically (create/update schema) and never checks for `template.deleted`. No `_wip_templates` metadata table exists in PostgreSQL (unlike `terminologies` and `terms` which DO have status tracking).

**Impact:** DnD Compendium agent read from PostgreSQL, couldn't detect deactivated templates, ran in circles creating workarounds. Resolved by switching to API reads.

**Fix:**
1. Add `_wip_templates` metadata table (like `terminologies`)
2. Update `_process_template_event()` to check event type and upsert template status
3. For `template.deleted`: set status to `inactive` in PG

- Discovered: 2026-03-28 during DnD K8s deployment
- Status: **Fixed** (2026-03-29) — `_wip_templates` metadata table, event-driven + batch sync, tests

### `@wip/client` Completion — Mandatory Interface for App Agents

`@wip/client` is the ONLY supported path for AI-assisted app development. Every app and agent that bypasses the client hits the same WIP conventions (bulk-first 200 OK, identity dedup, synonym resolution, pagination) and wastes significant time working around them.

**Already complete:**
- `files.uploadFile()`, `files.downloadFileContent()` — file operations
- `reporting.runQuery()`, `reporting.listTables()`, `reporting.getTableSchema()`, `reporting.getSyncStatus()` — SQL query execution and table introspection
- `templates.createTemplates()` — bulk template creation
- Server-side auth handled by `@wip/proxy` (no client change needed)
- All bulk methods exposed alongside single-item convenience methods

**Remaining:**
- Sync-aware read guidance documentation (API for state, PG for analytics)

**App agent system prompt directive:** "Use @wip/client for ALL WIP interactions. Do not make direct API calls."

- Status: **Complete** (2026-03-29) — all methods implemented including `awaitSync()`. Documentation remaining.

### Sync-Aware Helpers for Reporting Reads

Apps need guidance on when to read from API (MongoDB) vs reporting (PostgreSQL). PostgreSQL is eventually consistent — sync delay + template deactivation bug (above) cause stale state.

**Guidance to formalize:**
- **State management** (current truth, status checks): Read from API (MongoDB)
- **Analytics/reporting** (aggregations, cross-template joins): Read from PostgreSQL
- **Never** use PostgreSQL as source of truth for entity status

**Two helper approaches under consideration:**
1. Explicit wait — `{ waitForSync: true, timeout: 5000 }` parameter on `run_report_query`
2. Versioned reads — `sync_version` column from NATS sequence, `WHERE sync_version >= X`

- Status: Design discussion — see memory for details

### App Gateway & WIP Proxy — TOP PRIORITY

Every browser-based WIP app needs auth injection (browser can't hold API keys) and MinIO URL rewriting (presigned URLs point to internal hostnames). On K8s, apps also need sub-path routing at `/apps/{name}/`. The DnD Compendium solved this with 70 lines of hand-rolled Express proxy. Every new app would copy-paste this.

**Two deliverables, separable:**

1. **`@wip/proxy` middleware** (all deployments) — Express middleware that any app drops in with one line. Handles API key injection and file content proxying. Works on localhost and K8s identically. Also adds `files.downloadContent()` to `@wip/client`.

2. **App Gateway** (K8s / multi-app) — Extends existing Caddy (Podman) or NGINX Ingress (K8s) with `/apps/{name}/*` routing, `app-manifest.json` registration, and a portal landing page. Not a new service — configuration added to existing proxy infrastructure.

**Phase 1** (`@wip/proxy`) unblocks all app development. **Phase 2-3** (gateway) is needed when deploying multiple apps on one hostname.

- Design: `docs/design/app-gateway.md`
- Also see: `docs/WIP_DevGuardrails.md` (Guide 1 — original gateway vision)
- Discovered: 2026-03-28 during DnD K8s deployment
- Status: **Phase 1 complete** (2026-03-29) — `@wip/proxy` middleware, DnD refactored, `create-app-project.sh` updated. Phase 2-3 not started.

### App User Authentication — REQUIRED

`@wip/proxy` handles the app-to-WIP leg (API key injection), but there is **no user authentication** for the apps themselves. Anyone who can reach the URL can use the app. Even on a home network, WiFi access must not equal app access — guest WiFi credentials should not expose salary payslips or medical data.

**Solution:** OIDC session auth at the gateway level, extending the Caddy + Dex pattern already used for the Console:

1. User opens an app → gateway sees no session cookie → redirects to Dex login
2. User authenticates (same users/groups as Console: admin, editor, viewer)
3. Dex issues token → gateway establishes session cookie
4. Gateway injects user identity (`X-WIP-User`, `X-WIP-Groups`) into upstream requests
5. App receives authenticated context — zero per-app auth code

**Podman (Caddy):** Caddy already does this for the Console via `caddy-security` plugin. Extend the same config to `/apps/*` routes.

**K8s (NGINX Ingress):** `oauth2-proxy` sidecar or Ingress-level OIDC annotation, same Dex backend.

**`@wip/proxy` change:** Accept forwarded user identity from gateway headers instead of (or in addition to) a hardcoded API key. Per-user API keys or a gateway service account key are both viable.

- Design: Phase 4 in `docs/design/app-gateway.md`
- Depends on: App Gateway (Phase 2-3) for multi-app routing, but can be implemented for single-app Caddy deployments independently
- Status: Not started — required before any app serves sensitive data

### Console: Files Page Ignores Namespace

The files page (`/files`) always queries `namespace=wip` regardless of the selected namespace. Root cause: `list_files` API defaults to `namespace="wip"` and the Console doesn't pass the active namespace. Files uploaded to other namespaces (e.g., `dnd`) are invisible in the UI.

- Fix: Pass active namespace from Console's namespace selector to the files API call
- Discovered: 2026-03-28 during DnD data migration to K8s
- Status: **Fixed** (2026-03-29)

### Ontology Browser — UX Refinements

Implemented (2026-03-27). Known issues remaining:
- ~~Some nodes show UUID7 instead of human-readable labels~~ Fixed (2026-03-29)
- Document list in detail panel needs rethinking

- Design: `docs/design/ontology-browser.md`

### Mutable Terminologies

Add `mutable: true` flag on terminologies to support user-editable controlled vocabularies. Mutable terminologies allow real term deletion (with relationship cascade) while using the full terminology infrastructure — ontology relationships, reporting-sync, MCP tools, ontology browser.

**Why:** Apps that need user-defined picklists, tags, or classifications currently must reinvent terminology semantics using documents. The ClinTrial app exposed this: users need to extend a curated therapeutic area hierarchy with custom classifications. Without mutable terminologies, every app builds a document-based overlay pattern — different template names, different merge logic, each slightly wrong.

**Scope:** `mutable` defaults to `false` — zero impact on existing terminologies. Term delete becomes hard-delete + relationship cascade for mutable terms. Same API endpoints, same events, same reporting pipeline.

- Design: `docs/design/mutable-terminologies.md`
- Discovered: 2026-03-29 during ClinTrial app development
- Status: Design complete, not started

### Namespace Authorization — UX Polish

~50 button guards in Console detail views. API already enforces — this is cosmetic.

- Design: `docs/design/namespace-authorization.md`

### Registry Entry Reactivation

`POST /entries/{id}/reactivate` for reversible merges. Not yet implemented.

### WIP-Toolkit: `delete-namespace` Command

Wrap the two-step Registry API call into a single CLI command with `--dry-run` and `--force`. Low priority — `curl` and `dev-delete.py` already work.

### Dev-Namespace Workflow for Slash Commands

Update slash commands to use disposable dev namespaces during data modeling, with transfer to prod on completion. Mostly `create-app-project.sh` updates + minor slash command prompt changes. Depends on namespace deletion (done) and ID pass-through (done).

- Status: Not started, not a blocker

### Container Runtime Support

Test and document `setup.sh` with standard Docker and rootful Podman. Part of broader test/deployment review.

### Kubernetes Deployment — Validated on MicroK8s (2026-03-28)

Deployed and validated on a 3-node MicroK8s cluster (Raspberry Pi 5, aarch64, Rook-Ceph storage). All 13 pods running, full end-to-end including Console login, OIDC, seeding, and remote MCP access.

**What's done:**
- All K8s manifests validated: 5 infrastructure StatefulSets/Deployments, 7 service Deployments, NGINX Ingress with 9 routes
- Rook-Ceph block storage for all PVCs (38Gi+ allocated)
- K8s Secrets for credentials, ConfigMaps for service config + Dex OIDC
- Self-signed TLS via Ingress, Dex OIDC with group claims
- MCP server with HTTP streamable transport, accessible from Claude Code over HTTPS
- Remote seeding from Mac via `--host --via-proxy --port 443`
- Full installation log: `k8s-installation_log.md` (~1200 lines, 19 lessons learned)

**Remaining:**
- Helm chart or Kustomize packaging for configurable deployment
- Network Policies (Phase 6 of the installation plan)
- Production hardening (resource limits tuning, pod disruption budgets, horizontal scaling)
- Testing on other K8s distributions (k3s, cloud)

- Status: **Validated on MicroK8s** — see `k8s-installation_log.md` for full guide

### Detailed Installation Guide

Comprehensive, step-by-step installation instructions for every supported platform. Must cover:

- **Raspberry Pi 5 (aarch64):** OS preparation (Raspberry Pi OS 64-bit), rootless Podman setup, storage considerations (SD card vs SSD), memory tuning for 8GB
- **Raspberry Pi 4 (armv8.0):** Differences from Pi 5, performance expectations, recommended preset (`core` or `headless`)
- **Linux x86_64 (Debian/Ubuntu):** Package prerequisites, Podman or Docker installation, firewall/port configuration
- **Linux x86_64 (Fedora/RHEL):** SELinux considerations, Podman (default), cgroup v2 setup
- **macOS (Apple Silicon):** Podman Machine setup, resource allocation, volume mount performance
- **Each platform section includes:** Prerequisites, step-by-step install, preset selection guidance, TLS configuration (self-signed vs ACME), verification steps, common troubleshooting
- **Production vs development:** Clear separation of dev setup (self-signed, default keys) from production hardening (random secrets, ACME TLS, `--prod` flag)

Current state: `setup.sh` handles most of this automatically, but users need guidance on platform-specific prerequisites and post-install verification. The existing docs (`docs/production-deployment.md`, `docs/network-configuration.md`) cover pieces but there is no single end-to-end guide per platform.

- Status: Not started

### Data Migration Guide

Detailed instructions for migrating data between WIP instances, versions, and deployment types. Must cover:

- **Instance-to-instance migration:** Full namespace export via WIP-Toolkit, transfer, and restore on target instance (including ID pass-through requirements)
- **Version upgrades:** Procedure for upgrading WIP (pull new images, run migrations if any, verify data integrity)
- **Deployment type changes:** Moving from Podman Compose to Kubernetes, or from single-node to distributed deployment
- **Partial migration:** Exporting and importing individual namespaces, terminologies, or template sets
- **Rollback procedures:** How to revert a failed migration using backups and event replay
- **Backup strategy:** MongoDB dump/restore, MinIO bucket sync, PostgreSQL pg_dump, coordinated backup across all stores
- **Pre-migration checklist:** Verify source health, check disk space on target, confirm ID pass-through support, test with dry-run
- **Post-migration verification:** Entity count comparison, referential integrity checks, file accessibility, reporting-sync re-trigger

Depends on: Complete ID Pass-Through for Restore (v1.1) for fully reliable cross-instance migration.

- Status: Not started

### App Migration Guide

Detailed instructions for migrating applications built on WIP when the underlying WIP instance changes. Must cover:

- **App project relocation:** Moving an app project created by `create-app-project.sh` to point at a different WIP instance (updating MCP config, API base URLs, OIDC authority)
- **Data model evolution:** How to update an app when its WIP templates or terminologies change (field additions, type changes, terminology expansions) — impact on queries, UI bindings, and seed files
- **Namespace migration for apps:** Moving an app's namespace from one WIP instance to another, preserving all documents, files, and references
- **Client library upgrades:** Updating `@wip/client` and `@wip/react` to new versions, handling breaking changes in the typed API
- **Multi-app coordination:** When multiple apps share terminologies or reference each other's documents, migrating them together to maintain cross-app references
- **OIDC reconfiguration:** Updating the app's auth configuration when the WIP instance changes its OIDC provider or hostname
- **Seed file regeneration:** Using `/export-model` to regenerate seed files after data model changes, and `/bootstrap` to apply them to a new instance

Depends on: Data Migration Guide (above) for the underlying WIP migration procedures.

- Status: Not started

### App Deployment & Distribution Guide

Document the full lifecycle of deploying a WIP app to a different machine or handing it to another user. Two phases:

**Phase 1 — Manual deployment docs (near-term):**
- Step-by-step: clone app repo, install deps, configure MCP/API endpoints, run `/bootstrap` to seed data model, start the server
- Document what `create-app-project.sh` produces and how each piece maps to a deployment target
- Cover: Mac→Pi, Mac→VPS, dev→prod on same host
- Include the data pipeline: `/export-model` → transfer → `wip-toolkit import` or `/bootstrap`

**Phase 2 — One-click app distribution (medium-term):**
- Container image contract (app + data model seed in one image)
- `app-manifest.json` — metadata, WIP version requirements, namespace config
- Install tooling: `wip-toolkit install-app <image>` or similar
- Design doc exists: `docs/design/distributable-app-format.md` (specification only)

Phase 1 unblocks early adopters. Phase 2 depends on the NL query scaffold proving the app-building pattern at scale.

- Status: Not started

### MCP Server Configuration Guide & Transport Testing

Detailed instructions for configuring the WIP MCP server across different AI clients and transports. The MCP server supports three transports: stdio, SSE (deprecated in MCP spec), and HTTP streamable (current MCP spec).

Must cover:

- **Client configuration:** Step-by-step MCP config for Claude Code (`.mcp.json`), Claude Desktop (`claude_desktop_config.json`), Cursor, Windsurf, and other MCP-compatible clients
- **stdio transport:** Configuration examples, environment variable pass-through, working directory considerations
- **HTTP streamable transport:** Validated (2026-03-28) on K8s. Single `/mcp` endpoint, standard HTTP. Configured via `"type": "http"` in `.mcp.json`. Requires `NODE_EXTRA_CA_CERTS` for self-signed TLS (Node.js ignores macOS keychain).
- **SSE transport:** Legacy, deprecated in MCP spec. Still functional but prefer HTTP streamable.
- **Network scenarios:** Local (same host), LAN (e.g., app dev machine connecting to MCP server on a Pi), and remote (K8s Ingress, SSH tunnel, reverse proxy)
- **SSH stdio proxy:** Validated (2026-03-28) — SSH pipes the MCP server's stdio from a remote host to the local Claude Code client. Tested Mac→Pi with all 69 tools functional. Config: `"command": "ssh", "args": ["user@host", "cd /path && ... python -m wip_mcp"]` in `.mcp.json`. Requires service URLs overridden to `localhost` (container-internal hostnames don't resolve outside the container network).
- **K8s HTTP deployment:** Validated (2026-03-28) — MCP server as K8s Deployment with HTTP streamable transport, exposed via NGINX Ingress at `/mcp`. Key gotchas: DNS rebinding protection (`MCP_ALLOWED_HOST`), dual API keys (`API_KEY` for inbound, `WIP_API_KEY` for outbound), Node.js TLS trust (`NODE_EXTRA_CA_CERTS`).
- **Authentication:** How the MCP server forwards API keys to WIP services, configuring keys per client, OIDC token pass-through considerations
- **Tool filtering:** Documenting the planned `WIP_MCP_MODE=readonly` option (see MCP Read-Only Mode above) and how it affects tool registration per client
- **Troubleshooting:** Common failure modes (port conflicts, TLS cert trust, DNS rebinding 421 errors, dual API key misconfiguration, session loss on pod restart)

- Status: stdio + SSH proxy + HTTP streamable all validated. SSE functional but untested end-to-end.

### MCP Server Transport Regression Tests

Automated test coverage for the `--http`/`--sse` transport entry points added 2026-03-28. 32 existing tests cover client + tool wrappers; the transport layer has none.

Tests needed:
1. `--http` flag → `mcp.streamable_http_app()` is called (not `sse_app()`)
2. `--sse` flag → `mcp.sse_app()` still works (backward compat)
3. `MCP_ALLOWED_HOST` env var → appended to transport security allowed hosts/origins
4. `MCP_PORT` / `MCP_HOST` env vars → override FastMCP defaults (8000/127.0.0.1)
5. `API_KEY` middleware → rejects missing/wrong key, accepts correct key (both transports)

Target file: `components/mcp-server/tests/test_server.py` (new). Can mock uvicorn/anyio.

- Status: Not started

---

## Medium-Term

### Distributed Deployment

Make services independently deployable across multiple hosts. 80% ready — all service URLs are env vars. Main gaps:
- OIDC issuer URL is a build-time decision (baked into Dex config + frontend)
- Console nginx.conf uses docker-compose DNS names
- `setup.sh` assumes single-host deployment

- Design: `docs/design/distributed-deployment.md`
- Status: Phase 1-2 complete (console optional, reporting separable), Phase 3 pending

### Natural Language Interface — Standalone Deployment

Conversational data query UI as a standalone deployable service (beyond the per-app scaffold in Near-Term). BYOK (bring your own key) model, optional deployment. Would serve as a generic query frontend for any WIP instance without building a custom app.

The Near-Term NL Query Scaffold provides the per-app pattern. This item covers a shared, instance-wide NL service that works across all namespaces and templates.

- Design: `docs/design/natural-language-interface.md`
- Depends on: NL Query Scaffold (near-term)
- Status: Planning (scaffold-first approach adopted)

### `/init-nl-interface` Command — Data Model Snapshot

A forced refresh command that reads all templates, field names, terminology values, and document counts before answering any question. `/wip-status` on steroids. Motivation: the D&D Claude lost template awareness across compaction (missed 5 templates, gave wrong answers on Q6 and Q11). This command would build the Claude's working memory of the data model at the start of every query session, ensuring complete and accurate answers regardless of prior context.

The `describe_data_model` MCP tool (Near-Term, NL Query Scaffold) provides the underlying data. This slash command would call that tool and inject the result into the conversation context.

### Deterministic SQL Dashboard App

NL queries are impressive but non-reproducible. A dashboard with saved SQL queries against the PostgreSQL reporting backend provides: reproducibility (same query, same results), performance (no LLM context overhead), shareability (bookmarkable queries), and debuggability (visible, editable SQL). The AI's role shifts from "answer questions" to "help me write queries" — build once, run forever. Could be a standalone WIP app or a Metabase extension.

### Query Claude — Read-Only Family Member

A Claude instance with a restricted MCP tool set: no `create_*` tools, no app scaffolding skills. Only `query_by_template`, `run_report_query`, `search`, `list_*`, `get_*`, and `/init-nl-interface`. The analyst who queries the data warehouse but doesn't build the ETL pipeline. Different skills, different permissions, different risk profile. Safe to hand to any user because it can't modify the data model. Implementation: a separate MCP server config (or tool filter) plus a dedicated slash command set.

**These three ideas are complementary:** `/init-nl-interface` makes any Claude query-ready, Query Claude is a family member built around that command, and the SQL dashboard is the production-grade deterministic alternative.

---

## Longer-Term / Ideas

### Distributable App Format

Standard packaging for apps built on WIP. Container image contract, `app-manifest.json`, bootstrap flow. Would enable community app distribution and one-click install.

- Design: `docs/design/distributable-app-format.md`

### WIP Nano

Ultra-lightweight variant for Pi Zero and embedded systems. Minimal footprint, subset of features. Design only — future consideration.

- Design: `docs/design/wip-nano.md`

### Domain-Specific Ontology Relationships

Consider enabling namespace-scoped relationship type terminologies, so domains can define their own ontology relationship types rather than everything living in the global `_ONTOLOGY_RELATIONSHIP_TYPES` terminology. For example, a biomedical namespace might define `inhibits`, `activates`, `binds_to` without polluting the shared vocabulary. Likely overkill for most use cases — the extensible global terminology works fine — but worth considering if WIP is used across very different domains on the same instance.

### Metabase Pre-Built Dashboards

Metabase deployment works (`deploy/optional/metabase/`), but no pre-built dashboards yet. Would provide out-of-the-box analytics for common WIP data patterns.

---

## Design Documents

All feature designs live in `docs/design/`. Status of each:

| Document | Status |
|----------|--------|
| `ontology-support.md` | Implemented |
| `template-draft-mode.md` | Implemented |
| `template-reference-pinning.md` | Implemented |
| `event-replay.md` | Implemented (API + MCP tools) |
| `namespace-scoped-data.md` | Phase 1-2 complete, Phase 3-5 pending |
| `namespace-authorization.md` | Core complete, UX polish remaining |
| `namespace-deletion.md` | Implemented |
| `reference-fields.md` | Phase 1-2 complete, doc-to-doc references pending |
| `distributed-deployment.md` | Phase 1-2 complete, Phase 3 pending |
| `wip-tools-cli.md` | Partially implemented (`WIP-Toolkit/`) |
| `natural-language-interface.md` | Planning |
| `distributable-app-format.md` | Specification only |
| `namespace-strategy.md` | Guide (no implementation needed) |
| `app-gateway.md` | Phase 1 complete, Phase 2-4 pending |
| `mutable-terminologies.md` | Design complete, not started |
| `nl-query-scaffold.md` | Design complete, ready to implement |
| `ontology-browser.md` | Implemented |
| `universal-synonym-resolution.md` | Implemented |
| `wip-nano.md` | Concept only |

---

## Completed (for reference)

These were previously on the roadmap and are now fully implemented:

- DnD Compendium K8s deployment — first WIP app on Pi cluster. Express reverse proxy for auth injection, SSE-mode MCP, sub-path hosting at `/apps/dnd/`, all features working (data, images, AI chat). Exposed Gateway design as top priority (2026-03-28)
- Complete ID pass-through for restore — all entity types (terminologies, terms, templates, documents, files) preserve original IDs. 527/527 verified (2026-03-28)
- Namespace deletion — persistent journal, crash-safe, dry-run, inbound reference checking, MCP tool (2026-03-27)
- Ontology browser — ego-graph with Cytoscape.js, click-to-navigate, cross-namespace traversal (2026-03-27)
- Reporting-sync terminology/term population — startup batch sync + event-driven sync (2026-03-27)
- Windows/WSL platform support — auto-detection, named volume overlays for MongoDB/PostgreSQL/MinIO, NATS_URL fix
- Binary file storage (MinIO) — full CRUD, UI, reference tracking, orphan detection
- Semantic types — 7 types (email, url, lat/lon, percentage, duration, geo_point)
- Ontology support — OBO Graph JSON import, typed relationships, traversal
- Template draft mode — draft status, cascading activation
- MCP server — 69 tools, 4 resources, stdio + SSE + HTTP streamable transport, K8s deployment validated
- @wip/client + @wip/react — TypeScript client and React hooks
- CSV/XLSX import — preview + import endpoints in Document-Store
- Event replay — start, pause, resume, cancel via API and MCP tools
- Bulk-first API convention — all write endpoints accept arrays
- Security hardening — CORS, rate limiting, bcrypt keys, upload limits, security headers
- Information package for app-building AI — slash commands, reference docs, MCP resources
- Universal synonym resolution — auto-synonym registration, resolve layer at API boundary, WIP-Toolkit backfill + namespace rewriting
