# WIP Roadmap

Future plans, pending features, and design specifications.

---

## v1.1

### Namespace Deletion

Delete an entire namespace and all its data permanently. A `deletion_mode` field on the namespace (`retain` or `full`) controls whether hard-delete is permitted. Deletion uses a persistent journal for crash-safe resumption — lock the namespace, build the journal, execute step-by-step across MongoDB, MinIO, and PostgreSQL. Dry-run mode shows full impact report (entity counts, inbound references from other namespaces) before committing. Completed journals serve as audit trail.

Enables the dev→prod workflow: create a `full` dev namespace, iterate on the data model with AI, export, bootstrap into a `retain` prod namespace, delete the dev namespace cleanly.

- Design: `docs/design/namespace-deletion.md`
- Status: Design complete

### WIP-Toolkit: Cross-Instance Restore with ID Preservation

The `wip-toolkit import --mode restore` must preserve all original entity IDs when restoring to a different instance. The backend services (template-store, document-store) already support ID pass-through via `entry_id` in the Registry API — the toolkit needs to use it correctly.

Current state (tested 2026-03-25):
- **Terminologies:** No API-level ID pass-through. Toolkit builds old→new ID map by value and remaps downstream references (terms, template fields). Works.
- **Templates:** ID pass-through works (template_id + version in payload).
- **Documents:** ID pass-through exists in the API but the toolkit isn't triggering it correctly — documents get new IDs, breaking document-to-document references (e.g., `parent_class`). 14/1384 documents failed in testing.
- **Files:** Created with new IDs. File references in documents break.

Fix approach: Debug why document_id pass-through isn't activating (the document-store checks `request.document_id and request.version is not None`). For terminologies, consider adding API-level pass-through to def-store (matching the template-store pattern). For files, add file_id pass-through to document-store upload endpoint.

Alternative: Registry "draft" entity mode — register all IDs as draft (skip referential integrity), import all data, then promote all to active. This was discussed previously but may not have been implemented.

- Status: Partially working, 99% success rate, needs ID pass-through debugging

---

## Near-Term

### dev-delete.py: Namespace and Prefix Support

Add `--namespace` and `--prefix` flags to `scripts/dev-delete.py` for bulk deletion without needing individual entity IDs. Currently the script only accepts explicit WIP IDs, which is impractical for cleaning up entire namespaces (today requires a raw pymongo one-liner).

Examples:
- `python scripts/dev-delete.py --namespace dnd --force` — delete all entities in namespace `dnd` across MongoDB, MinIO, and PostgreSQL
- `python scripts/dev-delete.py --namespace dnd --cascade --force` — same, with cascade to Registry entries
- `python scripts/dev-delete.py --prefix DND_ --type terminology --force` — delete all terminologies matching a value prefix

Should reuse the existing `ENTITY_MAP` and cascade logic, and respect `--no-minio` / `--no-postgres` flags.

- Status: Not started

### Namespace Authorization — UX Polish

Core permission system is implemented (grant model, CRUD API, service enforcement). Remaining work: ~50 button guards in the Console detail views (`v-if="namespaceStore.canWrite"`). The API already rejects unauthorized requests — this is cosmetic polish.

- Design: `docs/design/namespace-authorization.md`

### Registry Entry Reactivation

`POST /entries/{id}/reactivate` for reversible merges. Currently, merged/deactivated entries cannot be restored. Not yet implemented.

### MCP Read-Only Mode

`WIP_MCP_MODE=readonly` env var that prevents registration of `create_*`, `import_*`, `archive_*`, `deactivate_*` tools. Same server, same code — the MCP protocol already handles tool visibility. Pairs with the `/analyst` slash command (already implemented) to create a Query Claude that physically cannot modify the data model. Also add a `--preset query` option to `create-app-project.sh` that generates a project with the readonly MCP config and only the query-focused slash commands.

### Container Runtime Support

Currently tested with rootless Podman only. Need to test and document:
- Standard Docker
- Rootful Podman (`sudo podman`)

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

### Natural Language Interface

Conversational data query UI powered by MCP tools. BYOK (bring your own key) model, optional deployment. Blocked on namespace authorization completion.

- Design: `docs/design/natural-language-interface.md`

### `/init-nl-interface` Command — Data Model Snapshot

A forced refresh command that reads all templates, field names, terminology values, and document counts before answering any question. `/wip-status` on steroids. Motivation: the D&D Claude lost template awareness across compaction (missed 5 templates, gave wrong answers on Q6 and Q11). This command would build the Claude's working memory of the data model at the start of every query session, ensuring complete and accurate answers regardless of prior context.

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
| `namespace-deletion.md` | Design complete, implementation pending (v1.1) |
| `reference-fields.md` | Phase 1-2 complete, doc-to-doc references pending |
| `distributed-deployment.md` | Phase 1-2 complete, Phase 3 pending |
| `wip-tools-cli.md` | Partially implemented (`WIP-Toolkit/`) |
| `natural-language-interface.md` | Planning |
| `distributable-app-format.md` | Specification only |
| `namespace-strategy.md` | Guide (no implementation needed) |
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
