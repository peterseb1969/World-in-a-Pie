# Plan: Unify Dev and Prod Docker Configuration

## Overview

Remove unnecessary differences between dev and prod deployments to reduce complexity and maintenance burden while preserving essential production features.

**Risk Level:** High - affects all services and deployment workflows

**Estimated Effort:** 2-3 days implementation + 1 day testing

---

## Current State

### Files to Modify/Remove

| Component | Dev File | Prod File | Action |
|-----------|----------|-----------|--------|
| Registry | `docker-compose.dev.yml` | `docker-compose.yml` | Merge → single file |
| Def-Store | `docker-compose.dev.yml` | `docker-compose.yml` | Merge → single file |
| Template-Store | `docker-compose.dev.yml` | `docker-compose.yml` | Merge → single file |
| Document-Store | `docker-compose.dev.yml` | `docker-compose.yml` | Merge → single file |
| Reporting-Sync | `docker-compose.dev.yml` | `docker-compose.yml` | Merge → single file |
| Console | `docker-compose.dev.yml` | `docker-compose.yml` | Merge → single file |
| Console | `Dockerfile.dev` | `Dockerfile` | Merge → single file |
| Infra | `docker-compose.infra.yml` | `docker-compose.infra.prod.yml` | Merge → single file |

### Current Differences Summary

```
Container names:     wip-registry-dev  vs  wip-registry
Database names:      wip_registry_dev  vs  wip_registry
Source mounting:     Yes               vs  No (baked in)
Uvicorn reload:      --reload          vs  (none)
Restart policy:      (none)            vs  unless-stopped
Healthchecks:        (none)            vs  configured
CORS:                *                 vs  (empty)
API keys:            hardcoded         vs  from .env
Console server:      Vite (HMR)        vs  Nginx (static)
```

---

## Target State

### Single docker-compose.yml per component

Behavior controlled by environment variables:

| Variable | Default | Dev Override | Effect |
|----------|---------|--------------|--------|
| `WIP_VARIANT` | `prod` | `dev` | Master switch |
| `WIP_DEV_RELOAD` | `false` | `true` | Uvicorn --reload |
| `WIP_DEV_MOUNT` | `false` | `true` | Mount source volumes |
| `WIP_RESTART_POLICY` | `unless-stopped` | `no` | Container restart |

### Unified container/database names

- Container: `wip-registry` (always, no suffix)
- Database: `wip_registry` (always, no suffix)

### Console decision: Nginx for both

Use Nginx for both dev and prod:
- Simpler configuration
- Same behavior in both environments
- Dev gets live reload via mounted `dist/` from local `npm run dev`

Alternative: Keep Vite for dev (more complexity but better DX)

---

## Implementation Phases

### Phase 1: Preparation (Low Risk)

**Goal:** Add infrastructure for unified config without breaking existing setup

1. **Update .env generation in setup.sh**
   ```bash
   # Add new variables
   WIP_VARIANT=${VARIANT:-prod}
   WIP_DEV_RELOAD=${DEV_RELOAD:-false}
   WIP_DEV_MOUNT=${DEV_MOUNT:-false}
   ```

2. **Create unified Dockerfile for console**
   - Multi-stage build that works for both
   - Build arg to skip production build when dev

3. **Add docker-compose override support**
   - `docker-compose.yml` - base config
   - `docker-compose.override.yml` - dev-specific (auto-loaded)

   This is Docker's native way to handle dev/prod differences.

**Verification:**
- Existing dev/prod workflows still work
- New variables are set but not yet used

### Phase 2: Unify Python Services (Medium Risk)

**Goal:** Single docker-compose.yml for each Python service

**Order:** Registry → Def-Store → Template-Store → Document-Store → Reporting-Sync

For each service:

1. **Create unified docker-compose.yml**
   ```yaml
   services:
     registry:
       container_name: wip-registry  # No -dev suffix
       environment:
         - DATABASE_NAME=wip_registry  # No _dev suffix
         # Conditional reload handled in command
       volumes:
         # Dev mounts handled via override file
         - ../../libs/wip-auth:/app/libs/wip-auth:ro
       command: >
         bash -c "pip install -q /app/libs/wip-auth &&
         uvicorn registry.main:app --host 0.0.0.0 --port 8001
         ${WIP_DEV_RELOAD:+--reload}"
       restart: ${WIP_RESTART_POLICY:-unless-stopped}
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
         interval: 30s
         timeout: 10s
         retries: 3
   ```

2. **Create docker-compose.override.yml for dev**
   ```yaml
   # Auto-loaded when running docker-compose up
   services:
     registry:
       volumes:
         - ./src:/app/src:ro
         - ./tests:/app/tests:ro
   ```

3. **Remove old docker-compose.dev.yml**

4. **Update setup.sh**
   - Remove logic that selects between dev/prod compose files
   - Add logic to create/remove override file based on variant

**Verification per service:**
- [ ] Dev deployment works with live reload
- [ ] Prod deployment works without reload
- [ ] Container connects to correct database
- [ ] Health checks pass
- [ ] API authentication works
- [ ] Tests pass

### Phase 3: Unify Console (High Risk)

**Goal:** Single docker-compose.yml and Dockerfile for console

**Decision: Nginx for both**

1. **Create unified Dockerfile**
   ```dockerfile
   # Build stage (skipped in dev with --target)
   FROM node:20-alpine AS builder
   WORKDIR /app
   COPY package*.json ./
   RUN npm ci
   COPY . .
   ARG VITE_OIDC_AUTHORITY=/dex
   # ... other build args
   RUN npm run build

   # Production stage
   FROM nginx:alpine AS production
   COPY --from=builder /app/dist /usr/share/nginx/html
   COPY nginx.conf /etc/nginx/conf.d/default.conf
   EXPOSE 80

   # Dev stage - nginx serving mounted dist
   FROM nginx:alpine AS development
   # dist mounted as volume, rebuilt by local npm run dev
   EXPOSE 80
   ```

2. **Create unified docker-compose.yml**
   ```yaml
   services:
     console:
       build:
         context: .
         target: ${WIP_CONSOLE_TARGET:-production}
       container_name: wip-console
       volumes:
         - ../../config/console/nginx.conf:/etc/nginx/conf.d/default.conf:ro
   ```

3. **Create docker-compose.override.yml for dev**
   ```yaml
   services:
     console:
       build:
         target: development
       volumes:
         - ./dist:/usr/share/nginx/html:ro
   ```

4. **Dev workflow change**
   - Run `npm run dev` locally (or in separate container)
   - Nginx serves the `dist/` directory
   - Changes reflected on refresh (not HMR)

**Verification:**
- [ ] Console loads in browser
- [ ] OIDC login works
- [ ] API calls work through nginx proxy
- [ ] Dev changes are reflected (after rebuild/refresh)

### Phase 4: Unify Infrastructure (Medium Risk)

**Goal:** Single docker-compose.infra.yml

1. **Merge infra compose files**
   - Keep all services in one file
   - Use environment variables for conditional config

   ```yaml
   services:
     mongodb:
       # Same for both

     nats:
       # Same for both

     mongo-express:
       # Only start if WIP_DEV_TOOLS=true
       profiles:
         - dev-tools
   ```

2. **Remove docker-compose.infra.prod.yml**

3. **Update setup.sh**
   - Use `--profile dev-tools` for dev deployments
   - Remove file selection logic

**Verification:**
- [ ] MongoDB starts and is accessible
- [ ] NATS starts and consumers connect
- [ ] Mongo-express only starts in dev
- [ ] PostgreSQL starts (analytics profile)

### Phase 5: Update Scripts and Documentation (Low Risk)

1. **Update setup.sh**
   - Remove all dev/prod compose file selection logic
   - Add profile-based service selection
   - Generate override files based on variant

2. **Update test-deployments.sh**
   - Remove VARIANT-based compose file paths
   - Update container name references (remove -dev suffix)

3. **Update run-tests.sh and test suites**
   - Remove container name suffix logic
   - Simplify configuration detection

4. **Update documentation**
   - CLAUDE.md quick start
   - docs/architecture.md
   - README.md

5. **Update seed scripts**
   - Remove any dev/prod container name handling

---

## Rollback Plan

If issues are discovered after deployment:

1. **Git revert** - All changes in separate commits per phase
2. **Keep old files** - Don't delete until verified (rename to .bak)
3. **Feature flag** - `WIP_LEGACY_CONFIG=true` to use old file structure

---

## Testing Strategy

### Per-Phase Testing

After each phase, run the full test matrix:

```bash
# Local tests
./scripts/test-deployments.sh --localhost

# Remote tests (on Pi)
./scripts/test-deployments.sh --remote wip-pi.local
```

### Regression Checklist

- [ ] Fresh install works (no existing data)
- [ ] Upgrade from v0.3 works (existing data preserved)
- [ ] All 16 deployment scenarios pass
- [ ] Seeding works (direct and via proxy)
- [ ] Console OIDC login works
- [ ] API authentication works (API key and JWT)
- [ ] Live reload works in dev (Python services)
- [ ] Console changes reflected in dev
- [ ] Container cleanup works (no permission issues)
- [ ] Data persists across container restarts

### Performance Verification

- [ ] Startup time not significantly increased
- [ ] Memory usage similar
- [ ] API response times similar

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Database name change loses data | Medium | High | Migration script, backup first |
| Container name change breaks scripts | High | Medium | Search/replace, thorough testing |
| OIDC config breaks in console | Medium | High | Test login flow explicitly |
| Volume mount paths wrong | Medium | Medium | Test on both Mac and Linux |
| Compose override not loaded | Low | High | Explicit documentation, CI test |
| Healthcheck breaks service startup | Low | Medium | Increase start_period |

---

## Migration: Existing Data

### Database Rename (if keeping data)

```bash
# MongoDB - rename databases
podman exec wip-mongodb mongosh --eval '
  db.adminCommand({
    renameCollection: "wip_registry_dev.namespaces",
    to: "wip_registry.namespaces"
  })
'
# Repeat for all collections in all databases
```

### Recommended: Fresh Start

For simplicity, recommend fresh deployment:
1. Export any important data
2. Run `./scripts/setup.sh --clean ...`
3. Re-seed

---

## Decisions (Confirmed)

1. **Console: Nginx for both dev and prod**
   - Simpler configuration, same behavior everywhere
   - For active UI development, run `npm run dev` locally for HMR

2. **Database names: Drop `_dev` suffix**
   - Always use `wip_registry`, `wip_def_store`, etc.
   - Container isolation prevents accidents

3. **Override files: Auto-generate**
   - setup.sh creates `docker-compose.override.yml` for dev
   - setup.sh removes it for prod
   - Clear header comment explaining the file is auto-generated

---

## Success Criteria

1. Single docker-compose.yml per component (no .dev.yml files)
2. Single Dockerfile per component (no .dev files)
3. `WIP_VARIANT=dev|prod` controls all differences
4. All 16 deployment test scenarios pass
5. Documentation updated
6. No data loss for existing deployments (migration path)
