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

### Ontology Browser — UX Refinements

Implemented (2026-03-27). Known issues remaining:
- Some nodes show UUID7 instead of human-readable labels
- Document list in detail panel needs rethinking

- Design: `docs/design/ontology-browser.md`

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
- **SSH stdio proxy:** Validated (2026-03-28) — SSH pipes the MCP server's stdio from a remote host to the local Claude Code client. Tested Mac→Pi with all 68 tools functional. Config: `"command": "ssh", "args": ["user@host", "cd /path && ... python -m wip_mcp"]` in `.mcp.json`. Requires service URLs overridden to `localhost` (container-internal hostnames don't resolve outside the container network).
- **Authentication:** How the MCP server forwards API keys to WIP services, configuring keys per client, OIDC token pass-through considerations
- **Tool filtering:** Documenting the planned `WIP_MCP_MODE=readonly` option (see MCP Read-Only Mode above) and how it affects tool registration per client
- **Troubleshooting:** Common failure modes (port conflicts, TLS issues with SSE, environment variables not propagated, tool timeouts)

Testing deliverable: End-to-end test of SSE transport — start MCP server in SSE mode, connect from at least two different clients, verify tool invocation, event streaming, and graceful disconnection.

- Status: stdio tested and working, SSH stdio proxy validated (Mac→Pi), SSE untested

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
| `universal-synonym-resolution.md` | Implemented |
| `wip-nano.md` | Concept only |

---

## Completed (for reference)

These were previously on the roadmap and are now fully implemented:

- Complete ID pass-through for restore — all entity types (terminologies, terms, templates, documents, files) preserve original IDs. 527/527 verified (2026-03-28)
- Namespace deletion — persistent journal, crash-safe, dry-run, inbound reference checking, MCP tool (2026-03-27)
- Ontology browser — ego-graph with Cytoscape.js, click-to-navigate, cross-namespace traversal (2026-03-27)
- Reporting-sync terminology/term population — startup batch sync + event-driven sync (2026-03-27)
- Windows/WSL platform support — auto-detection, named volume overlays for MongoDB/PostgreSQL/MinIO, NATS_URL fix
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
- Universal synonym resolution — auto-synonym registration, resolve layer at API boundary, WIP-Toolkit backfill + namespace rewriting
