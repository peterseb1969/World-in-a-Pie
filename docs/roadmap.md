# WIP Roadmap

Future plans, pending features, and design specifications.

---

## Near-Term

### Namespace Authorization — UX Polish

Core permission system is implemented (grant model, CRUD API, service enforcement). Remaining work: ~50 button guards in the Console detail views (`v-if="namespaceStore.canWrite"`). The API already rejects unauthorized requests — this is cosmetic polish.

- Design: `docs/design/namespace-authorization.md`

### Registry Entry Reactivation

`POST /entries/{id}/reactivate` for reversible merges. Currently, merged/deactivated entries cannot be restored. Not yet implemented.

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

---

## Longer-Term / Ideas

### Distributable App Format

Standard packaging for apps built on WIP. Container image contract, `app-manifest.json`, bootstrap flow. Would enable community app distribution and one-click install.

- Design: `docs/design/distributable-app-format.md`

### WIP Nano

Ultra-lightweight variant for Pi Zero and embedded systems. Minimal footprint, subset of features. Design only — future consideration.

- Design: `docs/design/wip-nano.md`

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
