# Deployment Integration Tests

End-to-end testing of WIP deployment configurations on Mac.

## Overview

The `scripts/test-deployments.sh` script validates that actual deployments work correctly by:

1. **Clean Start** - Stops all containers, removes data
2. **Deploy** - Runs `setup.sh` with specific configuration
3. **Health Check** - Waits for all services to become healthy
4. **Initialize** - Creates WIP namespaces
5. **Seed Data** - Populates test data using seed script
6. **Verify APIs** - Confirms all API endpoints respond correctly
7. **Document** - Records results, container stats, and timing
8. **Cleanup** - Prepares for next test

## Test Matrix

Tests are selected for Mac compatibility (all use `--localhost`):

| # | Name | Preset | Variant | Key Features |
|---|------|--------|---------|--------------|
| 1 | core-dev | core | dev | API-key only, no OIDC, minimal |
| 2 | core-prod | core | prod | Same, no dev-tools |
| 3 | standard-dev | standard | dev | OIDC, dev-tools, console |
| 4 | standard-prod | standard | prod | OIDC, no dev-tools |
| 5 | analytics-dev | analytics | dev | OIDC + PostgreSQL reporting |
| 6 | full-dev | full | dev | All features enabled |

### Why These Tests?

- **core** validates the minimal deployment works
- **standard** is the most common configuration
- **analytics** adds PostgreSQL (different stack)
- **full** validates all modules together
- **dev vs prod** variants ensure both work correctly

### Excluded from Mac Tests

- `--hostname wip.local` (network deployments) - require DNS/hosts setup
- `--acme-staging` (Let's Encrypt) - requires public domain
- Platform-specific (`--platform pi4`) - different hardware

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
# Stops all wip-* containers
podman stop wip-registry wip-def-store ...
podman rm -f wip-registry wip-def-store ...

# Data is cleaned via setup.sh --clean
```

### 2. Run Setup

```bash
./scripts/setup.sh --preset <preset> <flags> --clean -y
```

The `--clean` flag ensures fresh data directories.
The `-y` flag skips confirmation prompts.

### 3. Wait for Services

Polls health endpoints every 5 seconds:

| Service | Endpoint |
|---------|----------|
| Registry | `http://localhost:8001/health` |
| Def-Store | `http://localhost:8002/health` |
| Template-Store | `http://localhost:8003/health` |
| Document-Store | `http://localhost:8004/health` |
| Reporting-Sync | `http://localhost:8005/health` |
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
3. `POST /api/document-store/documents/search` - Search documents
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
