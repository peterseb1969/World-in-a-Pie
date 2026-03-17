# Distributed Deployment Design

**Status:** Phase 1 Complete, Phase 2-3 Planning
**Date:** 2026-03-17

## Goal

Enable flexible deployment of WIP services across multiple hosts:
- Console optional and deployable independently (e.g., Mac connecting to Pi)
- Reporting backend on a separate device
- Any service relocatable to a different host
- Reduced footprint when not all services are needed

## Current State

### Already Distributed-Friendly
- All service-to-service URLs configurable via environment variables (`REGISTRY_URL`, `DEF_STORE_URL`, `TEMPLATE_STORE_URL`, `DOCUMENT_STORE_URL`, `NATS_URL`, `MONGO_URI`, `POSTGRES_HOST`)
- No hardcoded localhost in production code (only in defaults for local dev)
- Modular docker-compose system (composable modules: oidc, reporting, files, dev-tools)
- API key authentication works across networks

### Needs Work
1. **Console assumes co-located Caddy/Dex** — OIDC authority (`VITE_OIDC_AUTHORITY`) baked in at Vue build time
2. **Console's nginx.conf uses docker-compose DNS** — `wip-def-store:8002` etc. won't resolve from another host
3. **setup.sh only generates single-host configs** — no multi-host scenario
4. **No "headless" preset** — can't easily deploy without console
5. **OIDC issuer URL** must match across Dex config, `.env`, and console build — tricky with multiple hosts

## Service Dependency Map

```
MongoDB ← Registry ← Def-Store ← Template-Store ← Document-Store
                                                         ↑
NATS ←──────────────────────────── Reporting-Sync ───────┘
PostgreSQL ←─────────────────────┘
MinIO ←──── Document-Store (file storage)
Dex ←────── Console (OIDC)
Caddy ←──── Console (TLS + proxy)
```

### Key Dependencies
| Service | Depends On |
|---------|-----------|
| Registry | MongoDB |
| Def-Store | MongoDB, Registry |
| Template-Store | MongoDB, Registry |
| Document-Store | MongoDB, Registry, Template-Store, Def-Store, NATS, MinIO (optional) |
| Reporting-Sync | MongoDB, NATS, PostgreSQL |
| Ingest-Gateway | NATS, Document-Store |
| MCP Server | All API services (via HTTP) |
| Console | Caddy, Dex (optional), all API services (via proxy) |

### Natural Split Points

| Host | Services | Rationale |
|------|----------|-----------|
| **Core (Pi)** | MongoDB, NATS, Registry, Def-Store, Template-Store, Document-Store, MinIO | Core data path, low latency between services |
| **Reporting (separate)** | PostgreSQL, Reporting-Sync | Heavy queries don't compete with core, can use more powerful hardware |
| **Frontend (Mac/desktop)** | Console, Caddy, Dex | UI close to user, or skip entirely for API/MCP-only use |
| **MCP** | mcp-server | Runs wherever Claude Code runs, connects to core via API |

## Deliverables

### 1. Make Console Optional (Priority 1) — DONE

**What:** Add `console` as a composable module instead of always deploying it.

**Implementation:**
- `console` added as a virtual module (same pattern as `ingest` — no overlay file, conditionally started by `start_services()`)
- New `headless` preset = base services only, no console, no OIDC
- `console` added to all existing presets (backward compatible)
- `setup.sh` conditionally skips: console startup, nginx config generation, Caddy console proxy, console override files
- `print_status()` and `show_confirmation()` reflect headless mode
- Usage: `./scripts/setup.sh --preset headless --localhost`
- Or remove from any preset: `./scripts/setup.sh --preset standard --remove console --localhost`

**Value:** Immediate footprint reduction on constrained devices. MCP-only workflows don't need the console.

### 2. Console Remote Mode (Priority 2)

**What:** Console deployable on a different host, connecting to remote WIP services.

**Changes:**
- Make nginx.conf backend URLs configurable (not just docker-compose DNS names)
- Option A: Generate nginx.conf with external URLs (e.g., `http://wip-pi.local:8001`)
- Option B: Runtime config.js injected at container start (avoids rebuild)
- Handle OIDC: either share Dex on core host (console proxies to it), or run Dex alongside console
- `setup.sh --remote-wip http://wip-pi.local` generates console-only config pointing at remote core

**OIDC considerations:**
- Simplest: keep Dex on the core host, console's Caddy proxies `/dex` to remote Dex
- Alternative: run Dex alongside console, configure it with same issuer URL
- API-key-only mode avoids OIDC entirely (simplest for personal use)

### 3. Distributed Setup Support (Priority 3)

**What:** `setup.sh` support for multi-host deployment.

**Changes:**
- `setup.sh --role core --hostname wip-pi.local` — generates core-only config
- `setup.sh --role reporting --core-host wip-pi.local` — generates reporting config pointing at core
- `setup.sh --role console --core-host wip-pi.local` — generates console config pointing at core
- Per-role compose file generation (only relevant services)
- Document the distributed deployment scenario

**Environment variable template per role:**

Core host:
```bash
# Services bind to 0.0.0.0 (accessible from network)
REGISTRY_URL=http://wip-registry:8001        # internal
DEF_STORE_URL=http://wip-def-store:8002      # internal
TEMPLATE_STORE_URL=http://wip-template-store:8003  # internal
DOCUMENT_STORE_URL=http://wip-document-store:8004  # internal
MONGO_URI=mongodb://wip-mongodb:27017/       # internal
NATS_URL=nats://wip-nats:4222               # internal
```

Reporting host:
```bash
MONGO_URI=mongodb://wip-pi.local:27017/     # remote
NATS_URL=nats://wip-pi.local:4222           # remote
POSTGRES_HOST=localhost                      # local
```

Console host:
```bash
# nginx.conf proxies to remote core
DEF_STORE_HOST=wip-pi.local:8002
TEMPLATE_STORE_HOST=wip-pi.local:8003
DOCUMENT_STORE_HOST=wip-pi.local:8004
REGISTRY_HOST=wip-pi.local:8001
```

## Security Considerations

- Cross-host communication should use TLS in production (Caddy can terminate)
- API keys travel over the network — use HTTPS or VPN
- MongoDB and NATS need auth enabled when exposed beyond localhost
- Firewall rules: only expose needed ports per host
- OIDC tokens must be valid across hosts (same issuer URL)

## Migration Path

1. **Phase 1:** Make console optional — no breaking changes, just a new preset
2. **Phase 2:** Console remote mode — new setup option, existing deployments unaffected
3. **Phase 3:** Full distributed support — new `--role` flag in setup.sh
4. All phases backward-compatible — single-host deployment continues to work exactly as before

## Open Questions

- Should MongoDB/NATS ports be exposed by default, or only when `--distributed` is set?
- Is TLS between services needed for home network use, or only for cloud?
- Should we support partial distribution (e.g., only reporting on a separate host) before full distribution?
- Kubernetes manifests in `k8s/` — update them to match distributed model, or separate effort?
