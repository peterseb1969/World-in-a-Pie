# WIP Roadmap

Future plans, pending features, and design specifications.

---

## v1.1

### Namespace Deletion

Delete an entire namespace and all its data permanently. A `deletion_mode` field on the namespace (`retain` or `full`) controls whether hard-delete is permitted. Deletion uses a persistent journal for crash-safe resumption — lock the namespace, build the journal, execute step-by-step across MongoDB, MinIO, and PostgreSQL. Dry-run mode shows full impact report (entity counts, inbound references from other namespaces) before committing. Completed journals serve as audit trail.

Enables the dev→prod workflow: create a `full` dev namespace, iterate on the data model with AI, export, bootstrap into a `retain` prod namespace, delete the dev namespace cleanly.

- Design: `docs/design/namespace-deletion.md`
- Status: Design complete

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
- **WIP Console:** Build, OIDC login flow, CRUD operations
- **Platforms:** macOS (Apple Silicon), Linux x86_64, Raspberry Pi 5 (aarch64), Raspberry Pi 4 (armv8.0)
- **Container runtimes:** Rootless Podman, rootful Podman, Docker

Deliverable: A CI-compatible test matrix script that can be run on each platform, reporting pass/fail per component. Should build on the existing `quality-audit.sh` and `.gitea/workflows/test.yaml` but extend to cover integration tests, toolkit round-trips, and client library type-checking.

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

### Default Synonyms for Portable Referential Integrity

Investigate creating a **default synonym** for every entity at registration time — a stable, human-readable composite key (e.g., `{namespace, entity_type, value}` for terminologies, `{namespace, terminology_value, term_value}` for terms) that survives export/import across instances.

Problem: Today, referential integrity during import depends on either preserving UUIDs (ID pass-through) or remapping by value (fragile). If a terminology is exported from instance A and imported into instance B, the UUID changes, and any external system holding the old UUID loses its reference.

Idea: At entity creation, the Registry automatically registers a deterministic synonym derived from the entity's natural key. This synonym is included in exports and used during import to resolve references — even when UUIDs differ between instances. The synonym acts as a portable, instance-independent identity anchor.

Considerations:
- What constitutes the "natural key" for each entity type? Terminologies have `(namespace, value)`, terms have `(terminology_value, term_value)`, templates have `(namespace, value, version)`, documents have `(namespace, template_value, identity_hash)`.
- Should this be opt-in per namespace or always-on?
- Interaction with the existing synonym system — these would be auto-managed synonyms distinct from user-created ones.
- Performance impact of creating a synonym for every entity.
- How does this interact with namespace deletion and the dev→prod workflow?

- Status: Idea — needs design discussion

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
