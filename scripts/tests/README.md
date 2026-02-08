# WIP Test Framework

Modular test framework for validating WIP deployments. Mirrors `setup.sh` module structure for conditional test execution.

## Quick Start

```bash
# Run all tests
./run-tests.sh

# Quick validation (deployment, auth, core-apis only)
./run-tests.sh --quick

# Run specific suites
./run-tests.sh deployment auth

# Skip seeding (use existing data)
./run-tests.sh --skip-seed

# List available suites
./run-tests.sh --list
```

## Test Suites

| Suite | Description | Conditional |
|-------|-------------|-------------|
| `01-deployment` | Container health, service availability | Always |
| `02-auth` | API key, OIDC authentication | OIDC tests require `oidc` module |
| `03-core-apis` | CRUD operations on all services | Always |
| `04-seeding` | Seed script, data verification | Always |
| `05-reporting` | PostgreSQL sync | Requires `reporting` module |
| `06-files` | MinIO file storage | Requires `files` module |
| `07-ingest` | NATS events and streaming | Always |
| `08-integration` | End-to-end workflows | Always |

## Architecture

```
scripts/tests/
├── run-tests.sh           # Main test runner
├── lib/
│   ├── common.sh          # Colors, logging, test state
│   ├── config.sh          # Module detection, port configuration
│   ├── api.sh             # HTTP client, JSON helpers
│   └── assertions.sh      # Test assertions
└── suites/
    ├── 01-deployment.sh   # Container health tests
    ├── 02-auth.sh         # Authentication tests
    ├── 03-core-apis.sh    # API CRUD tests
    ├── 04-seeding.sh      # Seed verification tests
    ├── 05-reporting.sh    # PostgreSQL sync tests
    ├── 06-files.sh        # MinIO tests
    ├── 07-ingest.sh       # NATS tests
    └── 08-integration.sh  # E2E workflow tests
```

## Module Detection

Tests automatically detect active modules from:
1. Running containers (e.g., `wip-dex` → `oidc` module)
2. Environment variables
3. Saved config file (`config/last-install.conf`)

Conditional tests are skipped when their module is not active.

## Writing Tests

### Test Function Pattern

```bash
test_my_feature() {
    # Make API call
    api_get "http://localhost:$PORT_DEF_STORE/api/def-store/terminologies"

    # Assert response
    assert_status 200 && assert_has_field "items"
}
```

### Running a Test

```bash
run_test "Description of test" test_my_feature
```

### Conditional Execution

```bash
if has_module "oidc"; then
    run_test "OIDC discovery" test_dex_discovery
fi
```

### Available Assertions

**Status Assertions:**
- `assert_status 200` - Exact status code
- `assert_success` - Any 2xx status
- `assert_auth_failure` - 401 or 403

**JSON Assertions:**
- `assert_has_field "items"` - Field exists in response
- `assert_json_field "status" "healthy"` - Field equals value
- `assert_min_count "items" 5` - Array has at least N items

**Body Assertions:**
- `assert_body_contains "healthy"` - Body contains string

**Container Assertions:**
- `assert_container_running "wip-mongodb"` - Container is running
- `assert_container_healthy "wip-mongodb"` - Container passes healthcheck

**Database Assertions:**
- `assert_mongo_has_docs "db" "collection" 10` - MongoDB has documents
- `assert_pg_has_rows "table" 10` - PostgreSQL has rows

### API Helpers

```bash
# Authenticated requests
api_get "http://localhost:8001/api/..."
api_post "http://localhost:8001/api/..." '{"key": "value"}'
api_put "http://localhost:8001/api/..." '{"key": "value"}'
api_delete "http://localhost:8001/api/..."

# Unauthenticated (for testing auth rejection)
api_get_noauth "http://localhost:8001/api/..."
api_get_badkey "http://localhost:8001/api/..."

# JSON extraction
json_field "items[0].name"
json_count "items"
json_has_field "items"
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DEBUG=1` | Enable debug output |
| `API_KEY` | Override default API key |
| `CONFIG_FILE` | Use specific config file |

## Running Individual Suites

```bash
# Run suite directly
./scripts/tests/suites/01-deployment.sh

# Or via runner
./run-tests.sh deployment
```

## Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

## Example Output

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WIP Test Suite
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Date:      2024-02-15 10:30:00
  Host:      mac-studio
  Platform:  Darwin arm64

  Modules:   oidc reporting
  API Key:   dev_master...

  Suites:    deployment auth core-apis

━━━ Deployment Health ━━━

  Core Infrastructure
    ✓ MongoDB container running
    ✓ MongoDB healthy
    ✓ NATS container running

  Core Services
    ✓ Registry container running
    ✓ Registry health endpoint
    ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Test Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Suites:     3 run, ALL PASSED
  Tests:      45 total
              45 passed

  Duration:   12s

  ✓ All tests passed
```
