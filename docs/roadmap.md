# WIP Roadmap

Current priorities and planned features. For completed work, see `docs/completed-features.md`. The organizing principle for the next release is the **v1.0 install-test milestone** below — everything else is gated by or deferred behind it.

---

## v1.0 Stable Release — Install-Test Milestone

**Acceptance criterion:** A non-techie colleague installs WIP + a frozen ClinTrial Explorer in under one hour from a single page of instructions and explores real data within that hour. Source: fireside `FRANC-20260407/fireside-wip-v1-stable-release-scope.md` and BE-YAC response `BE-YAC-20260407-2119/response-to-franc-v1-scope.md`.

**Scope discipline:**
- Backend must be **correct**, not merely stable. Small bug fixes and small correctness improvements filed by app-builders are in scope (e.g. CASE-27 page_size cap).
- **No new platform features** beyond the affordance checklist below. v1.1 candidates go to the sections further down, not into v1.0.
- Every v1.0 item has to answer "does this serve the install test?" If it doesn't, it's v1.1 or app-layer work.

**Phase order** (from the BE-YAC response, accepted):

### ~~Phase 0 — PATCH /documents~~ ✅

**Complete** (2026-04-08). Bulk `PATCH /api/document-store/documents` with RFC 7396 merge semantics, per-item `if_match` OCC, identity-field invariance, and machine-readable `error_code` propagated through every client layer. Shipped end-to-end: backend + 26 tests, `@wip/client` 0.10.0, `@wip/react` 0.6.0, MCP `update_document`, wip-toolkit `update-document` CLI, docs + roadmap. Commits: `f6ab013`, `579ad64`, `99fede5`, `cd44417`, `74b3ebd`, `cbd357f`, `4a2e564`.

Unblocks: `useUpdateDocument` wiring in the React Console.

- Design: `docs/design/document-patch.md`

### Phase 1 — Bootstrapping API Gaps (CASE-25) ✅

**Complete** (2026-04-08). Two small API additions that let apps bootstrap themselves cleanly against any WIP instance:

1. **`PUT /api/registry/namespaces/{prefix}`** — upsert. Creates the namespace on missing using platform defaults; updates supplied fields when existing. Replaces the `GET → 404 → POST` dance with a single self-healing call.
2. **`POST /api/template-store/templates?on_conflict=validate`** — schema-aware conflict handling. Identical → `unchanged`; compatible (added optional field only) → `updated` version N+1; incompatible → `error` with `error_code='incompatible_schema'` and a structured diff (`added_required`, `removed`, `changed_type`, `made_required`, `modified_existing`, `identity_changed`).

Shipped end-to-end: backend + 13 new tests, `@wip/client` 0.11.0 (`upsertNamespace`, `createTemplate(s)({ onConflict })`), docs (`api-conventions.md` Idempotent Bootstrap section, MCP `wip://conventions` resource).

Compatibility is intentionally narrow ("added optional field only") so silent guardrail violations are impossible — anything else returns a structured diff the caller can show the human.

Commits: `1b05b7a`, `b7152d5`, `b7d34f2`, _this commit_.

- Case: `yac-discussions/CASE-25-open-app-bootstrapping-api-gaps.md`

### Phase 2 — Observability: `wip-toolkit status` (CASE-26) ✅

**Complete** (2026-04-08). New `wip-toolkit status` aggregator that closes the silent-failure detection gap from the CASE-22 incident.

Default mode is cron-fast: liveness for every required service, plus reporting-sync `/metrics`, reporting-sync `/alerts` (server-side stall detection rides on the existing alerts engine), and ingest-gateway `/metrics`. Pass `--integrity` to also run the aggregated referential integrity scan (heavier — opt-in because the full scan can take minutes on large instances).

Configurable thresholds (`--failed-events-warning`, `--consumer-lag-warning`, `--consumer-lag-critical`) with the CASE-26 defaults. Output modes: human-readable Rich table (default) or `--json`. `--quiet` for cron use suppresses output unless something is wrong. Exit codes: `0` ok, `1` warning, `2` critical, `3` unknown / unreachable.

No new service endpoints were added — every data point already existed on reporting-sync and the stores; the toolkit just plumbs and thresholds.

Commits: `add9902`, _this commit_.

- Case: `yac-discussions/CASE-26-open-observability-silent-failure-detection.md`

### Phase 3 — Backup/Restore (CASE-23) ✅

**Complete** (2026-04-09). Direct-read backup engine replaces the toolkit-based HTTP fan-out that failed on large namespaces. Commit `9a7f6d9`.

The redesign (decided in the 2026-04-09 fireside) uses direct MongoDB cursor reads for backup and ID-preserving bulk inserts for restore. Archive format bumped to v2.0 with `registry_entries.jsonl` and `source_install` metadata. Async engine runs on the event loop (no ThreadPoolExecutor). REST surface, BackupJob model, SSE progress, MCP tools all unchanged.

**v1.0 scope (restore mode only):**

1. ~~CASE-32 (file composite key)~~ ✅ `a2dec0c`
2. ~~Dump format spec + manifest~~ ✅ `9a7f6d9` — format v2.0, registry_entries.jsonl, source_install metadata
3. ~~Direct-read backup engine~~ ✅ `9a7f6d9` — motor cursor reads, no HTTP fan-out, no closure phase
4. ~~Restore engine~~ ✅ `9a7f6d9` — upsert namespace from manifest, bulk-insert into empty namespace

Backup smoke-tested on aa (32ms), seed, and clintrial. Restore engine coded and wired; live round-trip test deferred until larger dataset is available.

**Explicitly deferred past v1.0:** fresh mode, target_namespace, cross-install DR, registry_externals.jsonl, draft/activate state machine.

- Case: `yac-discussions/CASE-23-responded-platform-backup-restore.md`
- Design: `docs/design/backup-restore-redesign.md`

### Phase 4 — Container Suite ✅

**Complete** (2026-04-10). Full production deployment pipeline from build to install.

- `scripts/build-release.sh` — builds 8 service images with baked libs, pushes to Gitea
- `docker-compose.production.yml` — pull-only compose with all infra + services
- `.env.production.example` — annotated template with hostname placeholders
- `config/production/` — Caddyfile and Dex config templates with `{{WIP_HOSTNAME}}`
- `scripts/setup-wip.sh` — generates .env (random passwords), configs (bcrypt-hashed users), and start-wip.sh
- `.gitea/workflows/build.yaml` — CI on `v*` tags
- `scripts/setup-wip.sh` dependency check, app scanning, Caddy/Dex generation
- Clean e2e validated: build → push → wipe Pi → setup-wip.sh → compose up → login → restore 3GB backup
- Registry self-bootstraps `wip` namespace + grants on startup
- Dex v2.45+ (required for group claims)
- `build-release.sh --generate-compose` does in-place tag update (no heredoc)

### Phase 5 — App Distribution 🔶

**Framework landed** (2026-04-10, `9ee4b50`). Apps self-package as compose chunks with `wip.app.*` labels. `setup-wip.sh` auto-discovers chunks, generates Caddy routes and Dex OIDC clients. `start-wip.sh` prompts for app approval (Y/n/select, -y for auto).

**Completed:**
1. ~~App contract~~ ✅ — `docker-compose.app.<name>.yml` with `wip.app.route`, `wip.app.port`, optional `wip.app.oidc.*` labels
2. ~~Compose chunks~~ ✅ — React Console (OIDC, port 3010) and CT Explorer (API key, port 3001)
3. ~~setup-wip.sh app scanning~~ ✅ — auto-generates Caddy routes and Dex clients from labels
4. ~~start-wip.sh generation~~ ✅ — interactive app approval, .disabled opt-out
5. ~~Remote mode design~~ ✅ — `WIP_BASE_URL` configurable for app-on-Mac / WIP-on-Pi
6. ~~Dual OIDC redirect URIs~~ ✅ — co-located (hostname:8443) + remote (localhost:port)

**Still needed for v1.0:**
- **Build and push app images** — React Console and CT Explorer images not yet in Gitea
- **Pi e2e test with apps** — WIP core tested, apps not yet running on Pi
- **Update create-app-project.sh** — scaffold should generate compose chunk + build-app.sh

- Case: `yac-discussions/CASE-24-open-app-distribution.md`
- Related design: `docs/design/distributable-app-format.md`

### Phase 6 — Human Install Guide

**Owner:** Peter (or a dedicated docs YAC). Not BE-YAC work.

Written *against* the actual Phase 4+5 flow, after everything else works. Targets a non-techie persona with zero Kubernetes literacy. Covers: Docker Desktop prereq, two-file download, one env var, two docker commands, admin bootstrap, first namespace, install CT, explore data.

**Validation:** A real non-techie at work attempts the install from the docs with no Peter intervention beyond the docs themselves. If they can't do it in under an hour, v1.0 has not shipped.

### Frozen for the install test

To hold the scope discipline, specific versions are pinned as the install-test targets. Anything that drifts past these versions is v1.1:

- **ClinTrial Explorer vX.Y** — pinned when CT declares "frozen for v1.0" (commit SHA recorded here at that time)
- **WIP ReactConsole vX.Y** — pinned when RC declares feature-complete (see separate workstream below)

### Separate workstream: Console for v1.0

**Decision needed:** Which console ships in the v1.0 install test?

**Option A: Fix the Vue Console (wip-console)**
The Vue console is already containerized and deployed. It has a group claims bug — OIDC tokens include group membership but the Console doesn't pass groups to the API permission check, so users see "No Namespace Access" despite having grants. This is BE-YAC work (the Vue console was built by BE-YAC). Fixing the group claims bug is probably a small change.

**Option B: Package the React Console**
The React Console is closer to feature-complete and is the long-term replacement. However, it's not currently in the container family — there's no Dockerfile, no entry in `build-release.sh`, and no service in `docker-compose.production.yml`. Packaging it requires:
- A Dockerfile (multi-stage Node build → nginx, similar to Vue console)
- Adding it to `build-release.sh`
- Adding it to the production compose
- Verifying OIDC integration works with the same Dex setup
- The React Console is APP-YAC work; BE-YAC would only handle the containerization.

**Option C: Ship both** — Vue console as the default (fix the bug), React Console as an optional add-on. Unnecessary complexity for v1.0.

### React Console (parallel workstream)

React Console is tracked outside v1.0 phases because it is close to feature-complete but not yet frozen.

- `useUpdateDocument` wiring (now unblocked by Phase 0) is ongoing polish, done by APP-YAC, not BE-YAC.
- Permission-aware UI polish (Auth Phase 2 Console UX) is ongoing polish.
- **Missing from v1.0 pipeline:** Dockerfile, build-release.sh entry, production compose service. These are needed before it can replace the Vue console in the install test.
- When RC declares complete: pin the version, record the commit SHA, stop accepting feature PRs into the v1.0 test target.

---

## Design Document Gaps (action required for v1.0)

### `docs/design/image-based-distribution.md` — needs update before Phase 4

Written 2026-04-01 as "high-level design, needs fireside." The fireside has happened (2026-04-07) and changed several decisions. Deltas to apply:

| Area | Current design doc | v1.0 fireside decision |
|---|---|---|
| **Registry** | Gitea-first (`gitea.local:3000/...`) | **GHCR-first** (`ghcr.io/peterseb1969/...`) for the human install path. Gitea registry stays for dev builds. Rationale: install test runs off-network of Peter's Gitea; GHCR is public and free. |
| **Architectures** | "ARM-first is fine for now"; amd64 deferred | **amd64 + arm64 from day one.** Non-techie colleagues have mixed hardware. Multi-arch build via buildx or parallel CI runners is required, not deferred. |
| **Phase order** | Phase A (Dockerfiles+CI) → B (Kustomize) → C (Helm) → D (App lifecycle) | **Phase A only for v1.0.** Phase B (Kustomize) and Phase C (Helm) are v1.1 — they don't serve the install test. Phase D is partially CASE-24 and partially v1.1. |
| **Canonical compose file** | "Compose file variant that uses `image:` instead of `build:`" (side note) | **First-class deliverable.** `docker-compose.production.yml` is the single canonical human install artifact. Needs its own spec. |
| **Admin bootstrap on fresh install** | Not covered | **Needs a paragraph.** How does a fresh WIP container get its first admin user and first runtime API key? Decision from the BE-YAC response: Dex default admin → RC-Console → create namespace-scoped runtime key → paste into app setup form. The design doc should document this flow explicitly. |
| **"Subsumes" list** | Claims to subsume "App Development & Deployment Framework", "Container Runtime Support", "K8s Remaining", "Guides (partially)" | Partially correct. **App Development & Deployment Framework merges with CASE-24 (v1.0 Phase 5), not into this doc.** Container Runtime Support is obsoleted (see below). K8s Remaining is v1.1. |

**Open questions** (section 9 of the design doc) that still need answers before Phase 4 can start:
1. Version coordination — monorepo release (all services same version) vs independent. **Recommended:** monorepo — the install test pulls one set of images pinned to one tag.
2. Database migrations on upgrade — v1.0 declared no-migrations, but a fresh install still needs init (create indexes, bootstrap system terminologies). Document as init-container or startup-migration.
3. Secrets management — `.env` for Compose (fine for v1.0). K8s secrets (deferred to v1.1 Helm phase).
4. WIP Console build-time config — Vue bakes `VITE_*` at build time. Needs runtime `/config.json` injection pattern from `distributable-app-format.md`. **Same pattern as apps — adopt for Console.**

**Action:** Update the design doc before starting Phase 4. Can be done in parallel with Phase 1-3 by the next BE-YAC.

---

## Recently Completed (pre-v1.0 window)

### ~~Default Seed Missing System Terminologies~~ ✅
Complete (2026-04-06). Commit `9840401`.

### ~~Audit: Remove Stale ID Format References (TPL-, TERM-, T-)~~ ✅
Phase 1 + Phase 2 complete (2026-04-06). ~720 lines across 49 test files replaced. All suites pass.

### ~~Runtime API Key Management~~ ✅
Complete (2026-04-06). Full CRUD REST API at `/api/registry/api-keys`, KeySyncService, MCP tools, `@wip/client` 0.9.0, `create-app-project.sh` auto-provisioning. Commits: `371780e`, `8f81ecf`, `efd6c9b`, `0d410bc`, `d741d84`, `b59566a`. Docs: `docs/api-key-management.md`.

### ~~Namespace-Scoped API Keys & Implicit Namespace Resolution~~ ✅
Complete (2026-04-05). Unscoped keys killed, single-namespace keys get implicit namespace derivation, docs updated across 8 files.

### ~~Cross-Namespace Read Mode~~ ✅
Complete (2026-04-04). All list endpoints accept namespace as optional.

### ~~Test Suites Cover Non-UUID ID Formats~~ ✅
Complete (2026-04-04). All three suites use transport injection with real Registry.

### ~~Auth Phase 2: Namespace Permissions — Backend~~ ✅
Complete (0e548f3). Console UX polish deferred to React Console (separate workstream).

### ~~Auth Phase 3: Audit Trail Verification~~ ✅
Verified by code review (2026-04-01).

---

## v1.1 Candidates (Near-Term, not v1.0-blocking)

These are known-useful items that explicitly do not serve the install test and are deferred until v1.0 ships.

### Audit: identity_hash Lookups Without Template Scope (CASE-36 followup)

**Critical fix landed (2026-04-09, `0021e50`):** Document-store's upsert path used `identity_hash` for existing-document lookup without `template_id` scoping. Two templates with the same identity fields in one namespace could silently re-parent documents. Fixed in both single-create and bulk-create paths.

**All document-store instances fixed** (`0021e50`, `0050361`):

- Write paths: single-create and bulk-create now use `document_id` from Registry
- `hash:` prefix reference lookup: now routes through Registry
- Inactive→active chain follow: now uses `document_id`
- `get_document_by_identity()` API: now accepts `namespace` + `template_id` filters

**Audit complete (2026-04-10).** Def-store: zero `identity_hash` references. Template-store: one reference (response model field, not a lookup). Neither component does identity resolution — the bug was document-store-only. All components clean.

### Audit: Synonym Resolution Across All Endpoints (CASE-40/41)

**Systemic fix landed (2026-04-10).** All 31 `resolve_bulk_ids` and `resolve_or_404` calls across all services passed `namespace=None`, silently skipping synonym resolution for unscoped API keys. Value codes (e.g., `"DND_CLASS"`, `"CT_ORGANIZATION"`) failed to resolve on write and read endpoints.

Fixed: every endpoint now accepts an optional `namespace` query parameter. MCP client passes `default_namespace` automatically. `resolve_or_404` and `resolve_bulk_ids` log warnings when namespace is unavailable. Zero `namespace=None` calls remain.

**Broader audit still needed:** A full review of all endpoints to verify that synonym handling is consistent — not just the resolution calls, but also whether returned IDs, error messages, and documentation consistently support human-readable values. This is a quality pass, not a bug fix.

### PostgreSQL Password Desync Risk

PostgreSQL stores its initial password in the data volume and ignores the `POSTGRES_PASSWORD` env var on subsequent starts. If `.env` is regenerated with a new password (e.g., user deletes `.env` and re-runs `setup-wip.sh`), PostgreSQL keeps the old password and reporting-sync fails with `password authentication failed`.

Discovered during Pi deployment. No automated fix yet. Options: (a) `setup-wip.sh` detects existing PostgreSQL volume and warns; (b) startup init script runs `ALTER USER` to sync the password; (c) document "never delete .env without wiping volumes." At minimum, `setup-wip.sh` should warn when `.env` is regenerated that volume data may be inconsistent.

### Reporting-Sync: File Event Handling Gap

**Audit note (2026-04-09):** Reporting-sync's `worker._process_message` routes `document.*`, `template.*`, `terminology.*`, `term.*`, and `relation.*` events — each with explicit deleted / hard_deleted / deprecated branches. **`file.*` events are not handled at all** — they fall into the "Unknown event type" branch and are silently acked.

Concrete consequences:

- File metadata (filename, content_type, metadata) is denormalized onto a document's PostgreSQL row at document-event time. If the file is later edited via `PATCH /files`, the document row shows stale values until the next document update re-syncs.
- File soft-delete and hard-delete (orphan cleanup, mutable-namespace cascade) do not propagate — reporting-sync's document row still references the old file_id.
- There is no `files` table in PostgreSQL, so there is no "list all files for namespace X" reporting query path today. Files only show up as denormalized columns on their referencing documents.

Needs a decision: (a) add a `file.*` event handler in reporting-sync that updates denormalized columns on referencing documents and maintains a `files` table; or (b) declare denormalized file metadata intentionally snapshot-at-reference-time and document the limitation. CASE-32 (now implemented) makes option (a) easier because file identity is content-addressed — the same checksum in a namespace is a stable key.

Not v1.0-blocking on current evidence (CT install test doesn't exercise file metadata updates post-reference). But it should be audited alongside any other entity type whose delete/update cascade has gaps — there may be more.

### Sync-Aware Helpers for Reporting Reads

Apps need guidance on when to read from API (MongoDB) vs reporting (PostgreSQL). Two helper approaches under consideration: explicit wait (`{ waitForSync: true, timeout: 5000 }`) vs versioned reads (`sync_version` column from NATS sequence). Needs a design decision.

### Dev-Namespace Workflow for Slash Commands

Update slash commands to use disposable dev namespaces during data modeling, with transfer to prod on completion. Mostly `create-app-project.sh` updates + minor slash-command prompt changes.

### App Scaffold: Zero-Friction Dev Setup — Remaining Polish

The mainline `create-app-project.sh` improvements. Some items already done (TLS reject override, dev namespace, auto-provisioned keys). Remaining: auto-detect next free port, generate `.env` with live values from running WIP instance, MCP resource `wip://app-dev-checklist` with live status.

### Guides — Non-Install

The install guide is v1.0 Phase 6. These are the other four:
- Data Migration Guide
- App Migration Guide
- App Deployment & Distribution Guide (Phase 1 manual / Phase 2 image-based)
- MCP Server Configuration Guide

### End-to-End UI Testing with Claude Desktop

10 workflows + auth smoke tests. BE-YAC generates YAML scripts from Console source; Peter + Claude Desktop executes them. Integration with Gitea CI is an open question.

### Ontology Graph Explorer

Interactive graph UI replacing the current tree view. Data layer is ready (hierarchy traversal, reporting SQL, `useReportQuery`). Gap is entirely UI/visualization (Cytoscape.js, D3, or similar). Needs a fireside to pick the tech stack and decide Console-native vs standalone app.

- Design: `docs/design/ontology-browser.md`

### App Gateway Phase 2-3 — Multi-App Routing

Caddy or NGINX `/apps/{name}/*` routing, portal landing page. Not needed to install ONE app; needed when deploying multiple apps on one hostname.

- Design: `docs/design/app-gateway.md`

### Development Workflow Against WIP — Define in Detail

CASE-55 starts unblocking the dev loop: `wip-deploy install --target dev --app-source <name>=<path>` will let app developers hot-iterate a single app against a full WIP stack. That's a foot-in-the-door fix; the broader workflow story is undefined.

Questions that need a spec before they bite us:

- **Adding an app to a running WIP instance.** Today: rerun `wip-deploy install` — works, but the compose-up restarts touched services. What's the zero-downtime path for "install app X while WIP is live"? Does it need to be zero-downtime, or is a rolling restart acceptable?
- **Removing an app from a running instance.** The `--prune` plumbing in `apply_k8s` already supports it; compose doesn't. Data retention: archive the app's namespace or delete it? Defaults vs. opt-in?
- **Updating an app's image without redeploying the stack.** `wip-deploy install --app clintrial --image-tag v1.3.0` equivalent — replace just that container, leave everything else alone.
- **Multiple apps iterating concurrently** (rc + clintrial + dnd, all source-mounted). Resource cost on a dev machine, port conflicts, auth flow working across all three at once.
- **Relation to the pluggable-apps vision** (`docs/design/pluggable-apps.md`). That design makes apps managed entities *inside* WIP (App Manager service + dynamic Caddy routing). Partial overlap with the "add/remove app from running instance" question above. Decide whether the CLI path and the App Manager path converge or diverge.

This is a "write a design doc before the next round of implementation" item, not a code task. Sequencing-wise, the CLI `--app-source` flag ships first (small, unblocks immediate dev); the broader workflow spec then informs what comes next.

- Related designs: `docs/design/pluggable-apps.md`, `docs/design/distributable-app-format.md`, `docs/design/app-gateway.md`
- Related cases: CASE-55 (dev target app hot-reload), CASE-25 (bootstrapping API gaps)

### OpenAPI Schema Refresh — Cleaner Path Through Caddy

`scripts/update-schemas.sh` was updated (2026-04-25) to fetch via `podman exec` because wip-deploy v2 no longer publishes service ports to the host. Works, but a cleaner long-term fix is to **route `/openapi.json` per service through Caddy at e.g. `/api/<svc>/openapi.json`**. Then the script becomes a plain `curl` again, no container runtime knowledge needed, and any external client (a docs site, a TypeScript SDK generator, a third-party tool) can pull the live spec without `podman exec`.

Scope: a small deployer change to add the route in `deployer/src/wip_deploy/config_gen/caddy.py` (or wherever the per-service routing lives), plus reverting `update-schemas.sh` to URL mode. Same direction as `/api/<svc>/health` — the api-prefix is the public surface, anything else is implementation detail.

Optional: the same refactor lets the MCP-server generator regen schemas during CI/dev without needing the wip stack on the same host.

- Related: commit `8d8cddf` (seed script v1.3 has the same shape — assumed direct ports, switched to a different access pattern); CASE-60 (api-prefixed `/health` requires API key — same access-pattern family of cleanups).

---

## Medium-Term

### Distributed Deployment

Make services independently deployable across multiple hosts. 80% ready — all service URLs are env vars. Main gaps: OIDC issuer URL is build-time, Console nginx.conf uses compose DNS, `setup.sh` assumes single-host.

- Design: `docs/design/distributed-deployment.md`
- Status: Phase 1-2 complete, Phase 3 pending

### ~~Natural Language Interface~~ ✅

Complete. Integrated into the React Console as a built-in conversational data query interface.

- Design: `docs/design/natural-language-interface.md`

### Deterministic SQL Dashboard App

Saved SQL queries against the PostgreSQL reporting backend. Reproducibility, performance, shareability, debuggability.

### K8s Remaining Work

Helm chart / Kustomize packaging (Phase B/C of image-based distribution), network policies, production hardening, testing on other K8s distributions. **v1.1+** — the v1.0 install path is Compose-only.

---

## Longer-Term / Ideas

### Distributable App Format

Standard packaging for apps. Container image contract, `app-manifest.json`, bootstrap flow. One-click install. Partially absorbed into v1.0 Phase 5 and Phase 4 work; the remainder is v1.1+.

- Design: `docs/design/distributable-app-format.md`

### WIP Nano

Ultra-lightweight variant for Pi Zero and embedded systems. Concept only.

- Design: `docs/design/wip-nano.md`

### Domain-Specific Ontology Relations

Namespace-scoped relation type terminologies. Likely overkill — the extensible global terminology works fine — but worth considering for multi-domain instances.

### Metabase Pre-Built Dashboards

Out-of-the-box analytics dashboards for common WIP data patterns.

### Auth Phase 4: Constellation

Per-app user pools, gateway-level auth (Caddy plugin or oauth2-proxy), WIP as auth provider. See `docs/design/authentication-authorization.md`.

---

## Obsolete / Folded Away

### ~~Container Runtime Support~~

Previously said: "Test and document setup.sh with standard Docker and rootful Podman." **Obsoleted by the v1.0 fireside decision:** `setup.sh` is the developer install path, not the human install path. The human path is `docker compose pull && up` against published images. Container runtime compatibility is validated as a side-effect of Phase 4, not as a separate task.

### ~~App Development & Deployment Framework~~

Previously said: Dockerfile template, compose integration, `wip-toolkit deploy-app`, K8s manifest template. **Folded into v1.0 Phase 5 (CASE-24 — App distribution).** The Dockerfile template + compose chunk are Phase 5 deliverables. `wip-toolkit deploy-app` and K8s manifest templating are v1.1.

---

## Design Documents

All feature designs live in `docs/design/`. Status of each:

| Document | Status |
|----------|--------|
| `ontology-support.md` | Implemented |
| `template-draft-mode.md` | Implemented |
| `template-reference-pinning.md` | Implemented |
| `event-replay.md` | Implemented |
| `namespace-scoped-data.md` | Phase 1-2 complete, Phase 3-5 pending |
| `namespace-authorization.md` | Backend complete (0e548f3), Console UX in React Console workstream |
| `namespace-deletion.md` | Implemented |
| `reference-fields.md` | Phase 1-2 complete, doc-to-doc references pending |
| `distributed-deployment.md` | Phase 1-2 complete, Phase 3 pending |
| `wip-tools-cli.md` | Partially implemented (`WIP-Toolkit/`) |
| `natural-language-interface.md` | Implemented (integrated into React Console) |
| `distributable-app-format.md` | Partially in v1.0 Phase 5 (CASE-24); remainder v1.1+ |
| `namespace-strategy.md` | Guide (no implementation needed) |
| `authentication-authorization.md` | Phase 1 + 1.5 + 2 backend + 3 complete. Phase 2 Console UX in React Console workstream. Phase 4 (Constellation) is longer-term. |
| `app-gateway.md` | Phase 1 complete, Phase 2-4 v1.1+ |
| `mutable-terminologies.md` | Implemented |
| `nl-query-scaffold.md` | Implemented |
| `ontology-browser.md` | Implemented (tree view); graph explorer is v1.1 |
| `universal-synonym-resolution.md` | Implemented |
| `image-based-distribution.md` | **Needs update before v1.0 Phase 4** — see Design Document Gaps above |
| `wip-nano.md` | Concept only |
| `document-patch.md` | Implemented (2026-04-08) |
| `backup-restore-redesign.md` | v1.0 implemented (2026-04-09); restore live test pending larger dataset |
| `terminology-mutability-model.md` | Discussion draft — open, blocked on alias/synonym resolution |
