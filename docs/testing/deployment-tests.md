# Deployment Integration Tests

End-to-end testing of WIP deployment configurations on Mac.

## Overview

The `scripts/test-deployments.sh` script validates that actual deployments work correctly by:

1. **Clean Start** - Stops all containers, removes data
2. **Deploy** - Runs `wip-deploy install` with specific configuration
3. **Health Check** - Waits for all services to become healthy
4. **Initialize** - Creates WIP namespaces
5. **Seed Data** - Populates test data using seed script
6. **Verify APIs** - Confirms all API endpoints respond correctly
7. **Document** - Records results, container stats, and timing
8. **Cleanup** - Prepares for next test

## Test Matrix

Tests are selected for Mac compatibility (all use `--hostname localhost --tls internal`):

| # | Name | Preset | Key Features |
|---|------|--------|--------------|
| 1 | core | core | API-key only, no OIDC, minimal |
| 2 | standard | standard | OIDC, console, MinIO, MCP server |
| 3 | analytics | analytics | OIDC + PostgreSQL reporting |
| 4 | full | full | All features including ingest-gateway |

### Why These Tests?

- **core** validates the minimal deployment works
- **standard** is the most common configuration
- **analytics** adds PostgreSQL (different stack)
- **full** validates all modules together (including the ingest-gateway)

The historical `dev` vs `prod` variants from v1 were dropped: v2 always generates random secrets via the secrets backend, and the security tier is set with `--tls internal | letsencrypt | external | self-signed` rather than a single `--prod` flag.

### Excluded from Mac Tests

- Network-host deployments (`--hostname wip.local`) - require DNS/hosts setup
- Let's Encrypt (`--tls letsencrypt`) - requires a public domain
- `--target k8s` - covered by k8s-specific tests; not part of this Mac-local matrix

## Usage

```bash
# List all available tests
./scripts/test-deployments.sh --list

# Run all tests (~60-90 minutes)
./scripts/test-deployments.sh

# Run quick subset (core-dev + standard-dev, ~20 minutes)
./scripts/test-deployments.sh --quick

# Run single test
./scripts/test-deployments.sh --test 3

# Resume from specific test
./scripts/test-deployments.sh --continue-from 4
```

## Results

Results are saved to `testdata/deployment-tests/results_YYYYMMDD_HHMMSS.md` with:

- Pass/fail status for each test
- Duration
- Container resource usage (CPU%, Memory)
- Error notes if failed

## Test Phases Detail

### 1. Clean Start

```bash
# Stops all wip-* containers and removes the data + secrets backend
wip-deploy nuke --remove-data --remove-secrets
```

`wip-deploy nuke` is the v2 equivalent of v1's `setup.sh --clean`. The `--remove-data` flag drops named volumes (databases, file storage). `--remove-secrets` clears the secrets backend so the next install regenerates them. Without these flags, `wip-deploy nuke` just tears down the stack and leaves volumes for re-use.

### 2. Run Setup

```bash
wip-deploy install --preset <preset> --target compose --hostname localhost --tls internal
```

Each test variant maps preset + target options. There is no longer a `dev` vs `prod` flag — the legacy `--prod` distinction is now expressed by the secrets backend (always-generated random secrets at install) and the `--tls` mode (`internal` for self-signed home, `letsencrypt` for internet-exposed). For Mac-local tests, `--hostname localhost --tls internal` is the standard combination.

### 3. Wait for Services

Polls health endpoints every 5 seconds:

| Service | Endpoint |
|---------|----------|
| Registry | `http://localhost:8001/health` |
| Def-Store | `http://localhost:8002/health` |
| Template-Store | `http://localhost:8003/health` |
| Document-Store | `http://localhost:8004/health` |
| Reporting-Sync | `http://localhost:8005/health` |
| Ingest-Gateway | `http://localhost:8006/health` (when included via `full` preset) |
| Console | `http://localhost:3000` or `https://localhost:8443` |

Timeout: 180 seconds

### 4. Initialize Namespaces

```bash
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"
```

Creates the 5 standard WIP ID pools.

### 5. Seed Data

```bash
python3 scripts/seed_comprehensive.py --profile <profile>
```

Profiles:
- `minimal` - 50 documents (fast, used for core tests)
- `standard` - 500 documents (comprehensive)
- `full` - 2000 documents (thorough)
- `performance` - 100k documents (stress test)

### 6. Verify APIs

Tests these API operations:

1. `GET /api/def-store/terminologies` - List terminologies
2. `GET /api/template-store/templates` - List templates
3. `POST /api/document-store/documents/query` - Query documents (the canonical endpoint; the test script previously used `/search`, which was renamed)
4. `POST /api/registry/entries/lookup/by-id` - Registry lookup
5. PostgreSQL health (if reporting enabled)

### 7. Document Outcome

Records to markdown:
- Test name and parameters
- Pass/Fail status
- Duration in seconds
- Container resource usage
- Error details if failed

## CI Integration

For CI/CD, use the `--quick` flag for PR validation:

```yaml
- name: Run deployment tests
  run: ./scripts/test-deployments.sh --quick
  timeout-minutes: 30
```

Full test suite should run nightly or on release branches.

## Troubleshooting

### Test Timeout

If services don't start within 180s:
```bash
# Check container logs
podman logs wip-registry
podman logs wip-def-store
```

### Seeding Fails

Check API connectivity:
```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
```

### Container Resource Issues

On Mac, ensure Podman machine has enough resources:
```bash
podman machine inspect | grep -E "(CPUs|Memory)"
```

Recommend: 4 CPUs, 8GB RAM for full tests.
