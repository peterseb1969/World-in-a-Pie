# WIP Roadmap

Future plans, pending features, and design specifications.

---

## v1.1

### Namespace Deletion

Delete an entire namespace and all its data permanently. A `deletion_mode` field on the namespace (`retain` or `full`) controls whether hard-delete is permitted. Deletion uses a persistent journal for crash-safe resumption — lock the namespace, build the journal, execute step-by-step across MongoDB, MinIO, and PostgreSQL. Dry-run mode shows full impact report (entity counts, inbound references from other namespaces) before committing. Completed journals serve as audit trail.

Enables the dev→prod workflow: create a `full` dev namespace, iterate on the data model with AI, export, bootstrap into a `retain` prod namespace, delete the dev namespace cleanly.

- Design: `docs/design/namespace-deletion.md`
- Status: **Implemented** (2026-03-27) — deletion_mode field, persistent journal, dry-run, crash recovery, inbound reference checking, locked namespace status, MCP delete_namespace tool. 163 Registry tests pass.

### Complete ID Pass-Through for Restore

The `wip-toolkit import --mode restore` must preserve **all** original entity IDs when restoring to a different instance. Every entity type — terminologies, terms, templates, documents, and files — must arrive with its original ID intact. This is a hard requirement for backup/restore, cross-instance migration, and the dev→prod namespace workflow.

Current state (tested 2026-03-25):
- **Terminologies:** No API-level ID pass-through. Toolkit builds old→new ID map by value and remaps downstream references (terms, template fields). Works but fragile — value collisions across namespaces would break it.
- **Terms:** Inherit from terminology. Works via the value-based remap.
- **Templates:** ID pass-through works (template_id + version in payload).
- **Documents:** ID pass-through exists in the API but the toolkit isn't triggering it correctly — documents get new IDs, breaking document-to-document references (e.g., `parent_class`). 14/1384 documents failed in testing.
- **Files:** Created with new IDs. File references in documents break.

Fix approach:
1. **Documents:** Debug why document_id pass-through isn't activating (the document-store checks `request.document_id and request.version is not None`). Fix the toolkit to send both fields correctly.
2. **Files:** Add file_id pass-through to the document-store upload endpoint so restored files keep their original IDs. Update the toolkit to use it.
3. **Terminologies:** Add API-level ID pass-through to def-store (matching the template-store pattern) so restore doesn't depend on value-based remapping.
4. **End-to-end test:** Export a fully populated namespace (with doc-to-doc references, file references, and ontology relationships), restore to a clean instance, and verify 100% ID match with zero failures.

Alternative: Registry "draft" entity mode — register all IDs as draft (skip referential integrity), import all data, then promote all to active.

- Status: Partially working, 99% success rate, needs ID pass-through debugging for documents and files

### Cross-Platform Test Suite

Comprehensive end-to-end test of the full WIP universe across supported platforms. Must cover:

- **All services:** Registry, Def-Store, Template-Store, Document-Store, Reporting-Sync, Ingest Gateway, MCP Server
- **WIP-Toolkit:** Export, import (fresh and restore modes), closure computation
- **Client libraries:** @wip/client (TypeScript), @wip/react hooks
- **Scripts:** `setup.sh` (all presets), `quality-audit.sh`, `seed_comprehensive.py`, `dev-delete.py`, `create-app-project.sh`, security scripts
- **WIP Console:** Build, OIDC login flow, CRUD operations (see UI testing below)
- **Platforms:** macOS (Apple Silicon), Linux x86_64, Raspberry Pi 5 (aarch64), Raspberry Pi 4 (armv8.0)
- **Container runtimes:** Rootless Podman, rootful Podman, Docker

#### UI / E2E Testing Approaches

Three complementary options for testing the WIP Console and WIP apps:

1. **Playwright (CI backbone)** — Headless browser automation. Deterministic, fast, runs in CI without a display. Scripts cover: OIDC login flow, CRUD operations on all entity types, namespace switching, permission enforcement (button guards), file upload/download. This is the primary testing approach for the test suite. Available as an MCP server for AI-assisted test authoring.

2. **Claude Desktop computer use (exploratory + doc verification)** — Claude sees the screen and interacts visually like a human. Two use cases:
   - *Exploratory testing:* "Log in, create a template with these fields, verify it appears in the list, try creating a document against it." Catches visual/UX regressions that programmatic tests miss.
   - *Documentation verification:* Follow the setup guide, WIP_AppSetup_Guide, or any tutorial step by step — clicking through the actual UI and reporting where docs don't match reality. Screenshots as evidence. This automates what was done manually in the documentation audit (78 files, curl examples) but extends it to UI workflows: "open the console, click Login, verify what you see matches what the docs say."

   Non-deterministic, requires a display, not CI-friendly — but uniquely valuable for both purposes.

3. **Cypress / other browser frameworks** — Alternative to Playwright. Cypress has a built-in test runner UI, time-travel debugging, and automatic screenshots on failure. Heavier dependency but better DX for writing/debugging UI tests interactively. Consider if the team has Cypress experience; otherwise Playwright is lighter and sufficient.

**Recommendation:** Playwright for the CI test matrix (headless, all platforms). Claude Desktop computer use for manual validation sessions on Mac. Cypress only if Playwright proves insufficient.

Deliverable: A CI-compatible test matrix script that can be run on each platform, reporting pass/fail per component. Should build on the existing `quality-audit.sh` and `.gitea/workflows/test.yaml` but extend to cover integration tests, toolkit round-trips, client library type-checking, and Playwright UI tests.

- Status: Not started

### Dev-Namespace Workflow for Slash Commands

Update the 12 AI-assisted development slash commands (`/explore`, `/design-model`, `/implement`, `/build-app`, `/add-app`, `/improve`, `/bootstrap`, `/export-model`, `/document`, `/analyst`, `/resume`, `/wip-status`) to use a **disposable dev namespace** for data modeling, with transfer to a clean production namespace on completion.

Workflow:
1. `/explore` and `/design-model` create terminologies and templates in a dev namespace (e.g., `dev-<app-name>`)
2. `/implement` populates seed data in the dev namespace for validation
3. On completion, `/export-model` exports the finalized data model from the dev namespace
4. `/bootstrap` imports into a fresh production namespace (e.g., `<app-name>`)
5. The dev namespace is deleted via namespace deletion (see above)

This requires:
- Namespace deletion (v1.1 deliverable above) to be implemented first
- Complete ID pass-through for restore (v1.1 deliverable above) so bootstrap preserves references
- **MCP `create_namespace` tool** — currently the MCP server only has `list_namespaces` and `get_namespace_stats` (read-only). A `create_namespace` tool is needed so slash commands can create dev namespaces without the AI having to call the Registry API directly via curl.
- ~~**MCP namespace audit**~~ (Done) — systematic review of all 68 MCP tools completed. Fixed 13 tools: ontology tools (`create_relationships`, `list_relationships`, `get_term_hierarchy`, `delete_relationships`), terminology tools (`get_terminology_by_value`, `import_terminology`), unified `search`, bulk create tools (`create_terminologies_bulk`, `create_template`, `create_templates_bulk`, `create_document`, `create_documents_bulk`), and `get_template_versions`. All namespace-aware API endpoints now have namespace pass-through in the MCP layer.
- Updates to all slash command prompts to default to dev namespace during phases 1-3
- A new `/promote-namespace` or `/finalize` slash command (or extend `/bootstrap`) that orchestrates export→import→delete

Benefits: AI can iterate freely during design without polluting the production namespace. Failed experiments are cleaned up completely. The production namespace only ever contains the validated, final data model.

- Depends on: Namespace Deletion, Complete ID Pass-Through
- Status: Not started

---

## Near-Term

### ~~dev-delete.py: Namespace and Prefix Support~~ (Done)

Implemented 2026-03-25. The script now supports:
- `--namespace` — delete all entities in a namespace (with impact report, namespace record + ID counters cleanup)
- `--prefix` — delete by value prefix (e.g., `--prefix DND_ --type terminology`)
- Full recursive cascade: terminology→terms→relationships, template→child templates (recursive via `extends`)→documents→files (via `file_references`)
- Terminology→template reference warnings (not auto-deleted, but flagged)
- PostgreSQL `doc_*` table DROP on template cascade deletion
- Namespace record and ID counter cleanup after all entities are removed

### Bug: No Namespace Validation on Entity Creation

Entities (terminologies, terms, templates, documents) can be created in a namespace that does not exist in the Registry. The services accept any namespace string without checking whether it has been registered via `POST /api/registry/namespaces`. This means typos or unregistered namespaces silently succeed, leading to orphaned data that doesn't appear in namespace-scoped queries or stats.

Fixed in the Registry's `register_keys` and `reserve_ids` endpoints (commit 1d37656). A batch namespace lookup rejects items targeting nonexistent or inactive namespaces with a per-item error. The `id_generator.py` fallback (silent UUID7 for unknown namespaces) now raises `ValueError` as defense in depth. The `provision` endpoint already validated namespace existence.

- Status: Fixed (2026-03-27)

### ~~Lint: RUF005 in Ontology Service~~ (Fixed)

Fixed in commit 4fb8829. Used `[*path, next_id]` instead of list concatenation.

- Status: Fixed (2026-03-27)

### ~~Bug: Registry Tests Broken — Beanie/Motor Incompatibility~~ (Fixed)

All 162 Registry tests failed in CI with `TypeError: MotorDatabase object is not callable` during `init_beanie()`. Root cause: Registry's `requirements.txt` had no upper bounds on `beanie` and `motor`, unlike def-store which pinned `<2.0.0` and `<4.0.0`. CI resolved to an incompatible version combination. Also fixed: `NamespaceGrant` model was missing from test conftest's `document_models` list, and ruff lint errors (unsorted imports, unused `asyncio` import, `typing.AsyncGenerator` → `collections.abc`).

- Status: Fixed (2026-03-27)

### ~~Bug: Dashboard File Count Shows Zero~~ (Fixed)

`HomeView.vue` `loadDashboard()` fetched terminologies, templates, and documents but never fetched files. Added `fileStoreClient.listFiles({ page_size: 1 })` call (guarded by `isFilesEnabled()`) to populate `entityCounts.files` from the API `total`.

- Status: Fixed (2026-03-27)

### Bug: Reporting Sync Not Populating Terminologies/Terms Tables

The PostgreSQL `terminologies` and `terms` tables exist with correct schemas but contain 0 rows, even when WIP has active terminologies and terms. The `term_relationships` table syncs correctly (99 rows observed).

**Investigation findings (2026-03-26):**

Code review confirms the full pipeline is implemented and logically correct on both sides:
- **Def-Store publishes events** — `publish_terminology_event()` and `publish_term_event()` in `nats_client.py:180-275` are called from every CRUD operation in `terminology_service.py`. Event payloads include `event_type: "terminology.created"` etc. and a `terminology` / `term` dict with all fields including `terminology_id` / `term_id`.
- **Reporting-Sync subscribes and routes** — `worker.py:481-484` routes on `event_type.startswith("terminology.")` and `event_type.startswith("term.")`. The handlers (`_process_terminology_event` at line 200, `_process_term_event` at line 282) perform correct PostgreSQL upserts.
- **NATS stream subjects match** — both def-store and reporting-sync configure `WIP_EVENTS` with `wip.terminologies.>` and `wip.terms.>`.
- **Batch sync endpoints exist** — `POST /sync/batch/terminologies` and `POST /sync/batch/terms` work by fetching from the Def-Store API.

**Root cause — most likely a timing/lifecycle issue:**

1. **No startup batch sync.** When reporting-sync starts (`main.py:251-277`), it initializes the `BatchSyncService` but never calls `batch_sync_terminologies()` or `batch_sync_terms()`. It only starts the NATS event worker. Contrast with relationships: those 99 rows likely arrived via NATS events during ontology import (which happened while reporting-sync was running), not via batch sync.
2. **Terminologies/terms were likely created before NATS was configured**, or before the durable consumer was established. The consumer uses `DeliverPolicy.ALL` (`worker.py:520`), which replays from stream start — but only if the events were captured by the stream in the first place.
3. **`start_batch_sync_all()` only syncs documents.** It iterates templates and syncs their documents (`batch_sync.py:343-372`). It does NOT call `batch_sync_terminologies()` or `batch_sync_terms()`. So even a "sync everything" operation misses terminologies and terms.
4. **Manual batch sync was never triggered.** The `/sync/batch/terminologies` and `/sync/batch/terms` endpoints exist but there's no evidence they were ever called.

**Fix approach:**
1. **Immediate:** Trigger manual batch sync via `POST /api/reporting-sync/sync/batch/terminologies` and `POST /api/reporting-sync/sync/batch/terms` to backfill existing data.
2. **Permanent:** Add terminology and term batch sync to the startup sequence in `main.py` (after schema creation at line 246), so reporting-sync self-heals on restart.
3. **Belt-and-suspenders:** Include terminology and term sync in `start_batch_sync_all()` so the "sync everything" endpoint actually syncs everything.

- Status: **Fixed** (2026-03-27) — Made `batch_sync_terminologies` and `batch_sync_terms` namespace-optional (fetches all namespaces), added initial terminology/term sync at startup, and included terminology/term sync in `start_batch_sync_all()`

### Ontology Browser

Interactive ego-graph browser for exploring ontology relationships in the WIP Console. Focus on one term, see all its relationships (all types, configurable depth), click any neighbour to refocus. Uses Cytoscape.js for force-directed graph rendering with concentric layout (focus term at centre, neighbours in rings by depth).

Key features:
- Terminology selector (dropdown) + term search with autocomplete (dropdown + type-ahead filter, capped at 20 results)
- Ego-graph showing all relationship types (colour-coded edges)
- Click-to-navigate: click any node to refocus the graph on it
- Depth slider (1-3, default 2)
- Relationship type filter checkboxes
- Cross-namespace traversal: BFS tracks namespace per node, follows relationships into other namespaces automatically
- Detail panel with term info, link to term detail view, and documents referencing the focused term
- Namespace-aware: honours the global namespace selector
- Relationship API enriched with `source_term_value/label`, `target_term_value/label` (batch term lookup, no N+1)

New files: `OntologyBrowserView.vue`, `EgoGraph.vue`. New dependency: `cytoscape`.

Known issues:
- Some nodes still show UUID7 instead of human-readable labels (e.g., terms with no relationships return no label enrichment from the API — the term's own value/label is not fetched separately)
- Document list in the detail panel is a simple list; UX needs rethinking (user feedback: not what was envisioned)

- Design: `docs/design/ontology-browser.md`
- Status: Implemented (2026-03-27), UX refinements pending

### Namespace Authorization — UX Polish

Core permission system is implemented (grant model, CRUD API, service enforcement). Remaining work: ~50 button guards in the Console detail views (`v-if="namespaceStore.canWrite"`). The API already rejects unauthorized requests — this is cosmetic polish.

- Design: `docs/design/namespace-authorization.md`

### Registry Entry Reactivation

`POST /entries/{id}/reactivate` for reversible merges. Currently, merged/deactivated entries cannot be restored. Not yet implemented.

### MCP Read-Only Mode

`WIP_MCP_MODE=readonly` env var that prevents registration of `create_*`, `import_*`, `archive_*`, `deactivate_*` tools. Same server, same code — the MCP protocol already handles tool visibility. Pairs with the `/analyst` slash command (already implemented) to create a Query Claude that physically cannot modify the data model. Prerequisite for the NL Query Scaffold (below).

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

### Container Runtime Support

Currently tested with rootless Podman only. Need to test and document:
- Standard Docker
- Rootful Podman (`sudo podman`)

### Kubernetes Deployment — Validated and Tested

Early K8s manifests exist in `k8s/` (image build scripts, StatefulSets for infrastructure, Deployments for services, NGINX Ingress) but are not production-ready. This deliverable turns them into a validated, documented installation path.

Scope:
- **Manifests:** Complete and test all K8s manifests for every service and infrastructure component (MongoDB, PostgreSQL, NATS, MinIO, Dex, Caddy/Ingress)
- **Helm chart or Kustomize:** Package manifests for configurable deployment (presets, secrets, TLS, storage classes)
- **Secret management:** Integrate with K8s Secrets (and document Vault/External Secrets Operator options)
- **Storage:** PersistentVolumeClaims for all stateful services, document StorageClass recommendations
- **Networking:** Ingress configuration with TLS termination, service mesh considerations
- **OIDC:** Validate Dex issuer URL configuration works with Ingress hostnames
- **Testing:** Deploy to a local cluster (k3s/minikube/kind) and run the cross-platform test suite against it
- **Documentation:** Step-by-step installation guide covering single-node (k3s on Pi) through multi-node cloud clusters

Existing work: `k8s/build-images.sh` bakes wip-auth into self-contained images. Initial manifests exist but haven't been validated end-to-end.

- Status: Early manifests exist, not validated

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

### MCP Server Configuration Guide & SSE Transport Testing

Detailed instructions for configuring the WIP MCP server across different AI clients and transports. The MCP server currently supports both stdio and SSE transport, but only stdio has been tested in practice.

Must cover:

- **Client configuration:** Step-by-step MCP config for Claude Code (`.mcp.json`), Claude Desktop (`claude_desktop_config.json`), Cursor, Windsurf, and other MCP-compatible clients
- **stdio transport:** Configuration examples, environment variable pass-through, working directory considerations
- **SSE transport:** Configuration and testing — currently implemented but **untested**. Verify connection lifecycle, reconnection behaviour, authentication (API key header pass-through), and concurrent client sessions
- **Network scenarios:** Local (same host), LAN (e.g., app dev machine connecting to MCP server on a Pi), and remote (over SSH tunnel or reverse proxy)
- **Authentication:** How the MCP server forwards API keys to WIP services, configuring keys per client, OIDC token pass-through considerations
- **Tool filtering:** Documenting the planned `WIP_MCP_MODE=readonly` option (see MCP Read-Only Mode above) and how it affects tool registration per client
- **Troubleshooting:** Common failure modes (port conflicts, TLS issues with SSE, environment variables not propagated, tool timeouts)

Testing deliverable: End-to-end test of SSE transport — start MCP server in SSE mode, connect from at least two different clients, verify tool invocation, event streaming, and graceful disconnection.

- Status: stdio tested and working, SSE untested

---

## Medium-Term

### Gateway & Portal

Caddy-based reverse proxy for multi-app routing. Required before deploying a second app alongside WIP Console. Includes app-manifest.json registration, landing page, path-based routing.

- Design: `docs/WIP_DevGuardrails.md` (Guide 1)
- Status: Not yet implemented

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

### Universal Synonym Resolution

Allow any registered synonym to be used wherever a canonical ID is accepted. Services resolve non-canonical identifiers through the Registry before processing. This solves three problems at once: cross-instance migration (references survive because portable synonyms resolve on both instances), app development roundtrips (use human-readable identifiers directly in API calls), and portable identity (instance-independent entity references for external systems).

Subsumes the earlier "Default Synonyms" idea — portable synonyms are one application of universal resolution, not a separate feature.

- Design: `docs/design/universal-synonym-resolution.md`
- Status: Proposed — needs discussion

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
| `nl-query-scaffold.md` | Design complete, ready to implement |
| `ontology-browser.md` | Implemented |
| `universal-synonym-resolution.md` | Proposed — needs discussion |
| `wip-nano.md` | Concept only |

---

## Completed (for reference)

These were previously on the roadmap and are now fully implemented:

- Binary file storage (MinIO) — full CRUD, UI, reference tracking, orphan detection
- Semantic types — 7 types (email, url, lat/lon, percentage, duration, geo_point)
- Ontology support — OBO Graph JSON import, typed relationships, traversal
- Template draft mode — draft status, cascading activation
- MCP server — 68 tools, 4 resources, stdio + SSE transport
- @wip/client + @wip/react — TypeScript client and React hooks
- CSV/XLSX import — preview + import endpoints in Document-Store
- Event replay — start, pause, resume, cancel via API and MCP tools
- Bulk-first API convention — all write endpoints accept arrays
- Security hardening — CORS, rate limiting, bcrypt keys, upload limits, security headers
- Information package for app-building AI — slash commands, reference docs, MCP resources
