# Reporting-Sync Test Plan

## Current State

**Coverage: 33%** (155 tests, all unit-level with full mocking)

The existing tests verify internal logic:
- **transformer.py** — document flattening, term references, nested objects, arrays, semantic types
- **schema_manager.py** — DDL generation for all field types, ALTER TABLE statements
- **worker.py** — event routing, template fetching, error handling, metrics recording
- **metrics.py** — latency stats, alert thresholds, webhook notifications

Everything external is mocked: asyncpg pool/connection, NATS JetStream, httpx calls to Template Store. The tests confirm "given this input, does the code produce the right output?" but never verify that the output actually works against real infrastructure.

### What's Not Tested

| Gap | Risk |
|-----|------|
| Generated DDL executing on real PostgreSQL | Syntax errors, type mismatches, index conflicts |
| Transformed rows INSERTing successfully | Column count mismatch, type casting failures, NULL handling |
| ALTER TABLE on tables with existing data | Column additions failing, default value issues |
| UPSERT conflict resolution (latest_only vs all_versions) | Version comparison logic, partial updates |
| Semantic type columns (NUMERIC precision, JSONB structure) | PostgreSQL rejecting values that look fine in Python |
| Batch sync pagination and template inheritance resolution | Off-by-one, field merge conflicts |
| NATS message acknowledgment lifecycle | Messages stuck, redelivery loops |

---

## Approach A: Integration Tests with Real PostgreSQL

### Goal

Test that the SchemaManager and DocumentTransformer produce SQL that actually executes correctly against PostgreSQL. No NATS, no Template Store, no MongoDB — just the two components that generate and execute SQL, wired to a real database.

### Infrastructure

**Single additional container:** PostgreSQL (same image used in production: `postgres:16-alpine`)

```
┌─────────────────────────────────────────────┐
│  Test process (pytest)                      │
│                                             │
│  ┌──────────────┐    ┌──────────────────┐   │
│  │ SchemaManager│───▶│ Real PostgreSQL   │   │
│  └──────────────┘    │ (test database)   │   │
│  ┌──────────────┐    │                   │   │
│  │ Transformer  │───▶│                   │   │
│  └──────────────┘    └──────────────────┘   │
└─────────────────────────────────────────────┘
```

### Test Database Setup

```python
# tests/conftest_integration.py

import asyncpg
import pytest_asyncio

POSTGRES_DSN = "postgresql://wip:wip_test@localhost:5433/wip_reporting_test"

@pytest_asyncio.fixture(scope="session")
async def pg_pool():
    """Create a real asyncpg pool for integration tests."""
    pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=5)
    yield pool
    await pool.close()

@pytest_asyncio.fixture(autouse=True)
async def clean_tables(pg_pool):
    """Drop all doc_* tables and migration tracking between tests."""
    async with pg_pool.acquire() as conn:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND (tablename LIKE 'doc_%' OR tablename LIKE '_wip_%')"
        )
        for row in tables:
            await conn.execute(f'DROP TABLE IF EXISTS "{row["tablename"]}" CASCADE')
    yield
```

Use port 5433 to avoid conflicts with a running production PostgreSQL on 5432. The test database is ephemeral — created at session start, tables dropped between tests.

### CI Setup

Add a `test-postgres` container alongside the existing `test-mongo`:

```yaml
# In .gitea/workflows/test.yaml, before the reporting-sync test job:
- name: Start test PostgreSQL
  run: |
    podman run -d --name test-postgres \
      -e POSTGRES_USER=wip \
      -e POSTGRES_PASSWORD=wip_test \
      -e POSTGRES_DB=wip_reporting_test \
      -p 5433:5432 \
      postgres:16-alpine
    # Wait for ready
    for i in $(seq 1 30); do
      podman exec test-postgres pg_isready -U wip && break
      sleep 1
    done
```

### Test Categories

#### Category 1: DDL Execution

Verify that generated CREATE TABLE statements execute without error on real PostgreSQL.

```
tests/test_integration_ddl.py
```

| Test | What it verifies |
|------|-----------------|
| `test_create_table_string_fields` | Basic TEXT columns |
| `test_create_table_all_field_types` | Every FieldType → correct PostgreSQL type |
| `test_create_table_term_fields` | Dual columns: value TEXT + term_id TEXT |
| `test_create_table_semantic_duration` | JSONB + _seconds NUMERIC + _unit_term_id TEXT |
| `test_create_table_semantic_geo_point` | JSONB + _latitude NUMERIC(9,6) + _longitude NUMERIC(10,6) |
| `test_create_table_file_fields` | Single file (3 columns) vs multiple files (JSONB) |
| `test_create_table_indexes` | All 6 standard indexes exist after creation |
| `test_create_table_unique_active_identity_index` | Partial unique index on (namespace, identity_hash) WHERE status='active' |
| `test_create_table_custom_table_name` | ReportingConfig.table_name override |
| `test_create_table_idempotent` | Running CREATE twice doesn't error (IF NOT EXISTS) |
| `test_migration_tracking_table_created` | `_wip_schema_migrations` exists and has correct row |

#### Category 2: Schema Evolution (ALTER TABLE)

Verify that ALTER TABLE statements work on tables with existing data.

```
tests/test_integration_schema_evolution.py
```

| Test | What it verifies |
|------|-----------------|
| `test_add_string_column` | New TEXT column added, existing rows get NULL |
| `test_add_term_column` | Both value and term_id columns added |
| `test_add_semantic_column` | All semantic sub-columns added |
| `test_add_column_preserves_data` | Existing rows unchanged after ALTER |
| `test_add_column_idempotent` | Running same evolution twice doesn't error |
| `test_migration_recorded` | New version recorded in `_wip_schema_migrations` |
| `test_multiple_versions_sequential` | v1 → v2 → v3 schema evolution chain |

#### Category 3: Row Insertion (Transform → INSERT)

Verify that transformed rows actually INSERT into the table created by SchemaManager.

```
tests/test_integration_insert.py
```

| Test | What it verifies |
|------|-----------------|
| `test_insert_simple_document` | Basic INSERT succeeds, row readable |
| `test_insert_all_field_types` | String, number, integer, boolean, date, datetime all persist correctly |
| `test_insert_term_field` | Both display value and term_id persisted |
| `test_insert_nested_object_as_jsonb` | JSONB column stores and retrieves nested structure |
| `test_insert_null_optional_fields` | NULL values for non-mandatory fields |
| `test_insert_semantic_duration` | JSONB + seconds + unit_term_id all correct |
| `test_insert_semantic_geo_point` | JSONB + latitude + longitude all correct |
| `test_insert_data_json_column` | Full original data preserved in data_json |
| `test_insert_term_references_json` | Full term refs preserved in term_references_json |

#### Category 4: Upsert Strategy

Verify that conflict resolution works correctly for both sync strategies.

```
tests/test_integration_upsert.py
```

| Test | What it verifies |
|------|-----------------|
| `test_latest_only_insert_new` | First version inserts |
| `test_latest_only_update_newer_version` | v2 overwrites v1 |
| `test_latest_only_ignore_older_version` | v1 after v2 is no-op (WHERE version < check) |
| `test_latest_only_same_version_no_op` | Same version twice is idempotent |
| `test_all_versions_insert_multiple` | v1 and v2 both exist as separate rows |
| `test_all_versions_duplicate_no_op` | Same (doc_id, version) twice is DO NOTHING |
| `test_latest_only_identity_hash_uniqueness` | Two active docs with same identity_hash rejected by unique partial index |
| `test_archived_doc_allows_same_identity` | Archived + active with same hash allowed (WHERE status='active') |

#### Category 5: End-to-End Flow (SchemaManager + Transformer + PostgreSQL)

Verify the complete in-process pipeline without mocks.

```
tests/test_integration_pipeline.py
```

| Test | What it verifies |
|------|-----------------|
| `test_full_pipeline_create_and_insert` | ensure_table → transform → execute upsert → verify row |
| `test_full_pipeline_schema_evolution` | Create with v1 template → insert v1 doc → evolve to v2 → insert v2 doc → both readable |
| `test_full_pipeline_multiple_templates` | Two different templates → two different tables → correct isolation |
| `test_full_pipeline_batch_insert` | 100 documents inserted, all queryable |
| `test_full_pipeline_mixed_versions` | Insert v3, then v1, then v2 — only v3 visible (latest_only) |

### Test Pattern

Each integration test follows the same structure:

```python
@pytest.mark.asyncio
@pytest.mark.integration
async def test_insert_simple_document(pg_pool):
    """Transform a document and INSERT into a real PostgreSQL table."""
    sm = SchemaManager(pg_pool)
    transformer = DocumentTransformer()

    # 1. Create table from template
    template = make_template(fields=[
        {"name": "first_name", "type": "string"},
        {"name": "age", "type": "integer"},
    ])
    table_name = await sm.ensure_table_for_template(template)

    # 2. Transform document
    document = make_document(data={"first_name": "Alice", "age": 30})
    rows = transformer.transform(document, template)

    # 3. Execute upsert
    sql, values = transformer.generate_upsert_sql(table_name, rows[0])
    async with pg_pool.acquire() as conn:
        await conn.execute(sql, *values)

    # 4. Verify in PostgreSQL
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT * FROM "{table_name}" WHERE document_id = $1',
            document["document_id"],
        )
        assert row["first_name"] == "Alice"
        assert row["age"] == 30
```

### Pytest Marker

Integration tests are marked separately so they can be skipped when PostgreSQL isn't available:

```ini
# pyproject.toml or pytest.ini
[tool:pytest]
markers =
    integration: requires real PostgreSQL (deselect with -m "not integration")
```

Local dev without PostgreSQL: `pytest tests/ -m "not integration"`
CI with PostgreSQL: `pytest tests/ -v`

### File Layout

```
components/reporting-sync/tests/
├── test_transformer.py              # Existing unit tests (mocked)
├── test_schema_manager.py           # Existing unit tests (mocked)
├── test_worker.py                   # Existing unit tests (mocked)
├── test_metrics.py                  # Existing unit tests (mocked)
├── conftest_integration.py          # PostgreSQL pool, cleanup fixtures
├── test_integration_ddl.py          # Category 1
├── test_integration_schema_evolution.py  # Category 2
├── test_integration_insert.py       # Category 3
├── test_integration_upsert.py       # Category 4
└── test_integration_pipeline.py     # Category 5
```

### Estimated Scope

~40-50 integration tests. Execution time ~5-10 seconds (PostgreSQL operations are fast, no network hops to other services).

### What This Catches That Unit Tests Don't

- SQL syntax errors in generated DDL
- PostgreSQL type casting failures (e.g., Python float → NUMERIC precision loss)
- Index creation failures or conflicts
- UPSERT conflict clause correctness
- ALTER TABLE on populated tables
- NULL handling differences between Python None and PostgreSQL NULL
- JSONB serialization edge cases
- Partial unique index behavior (WHERE status = 'active')

---

## Approach B: Full E2E Smoke Tests

### Goal

Deploy the entire WIP stack, seed data through the normal API, wait for reporting-sync to process events, then compare what's in PostgreSQL to what was seeded. This validates the complete data path:

```
Seed Script → Document-Store API → MongoDB → NATS Event → Reporting-Sync → PostgreSQL
```

### Infrastructure

All services running (either local dev or a dedicated test environment):

```
┌──────────┐    ┌───────────────┐    ┌─────────┐    ┌────────────────┐    ┌────────────┐
│  Seed    │───▶│ Document-Store│───▶│  NATS   │───▶│ Reporting-Sync │───▶│ PostgreSQL │
│  Script  │    │   + MongoDB   │    │JetStream│    │                │    │            │
└──────────┘    └───────────────┘    └─────────┘    └────────────────┘    └────────────┘
       │                                                     │
       │        ┌───────────────┐                            │
       └───────▶│ Template-Store│◀───────────────────────────┘
                │   + MongoDB   │
                └───────────────┘
```

### How It Works

#### Phase 1: Setup

```bash
# Deploy full stack (or verify already running)
./scripts/setup.sh --preset standard --localhost

# Create a dedicated test namespace
curl -X POST http://localhost:8001/api/registry/namespaces \
  -H "X-API-Key: $API_KEY" \
  -d '[{"prefix": "e2e-test", "description": "E2E smoke test", "isolation_mode": "open"}]'
```

#### Phase 2: Seed Known Data

Seed a controlled dataset with known values — not random Faker data, but deterministic fixtures that can be verified exactly.

```python
# e2e/seed_fixtures.py

FIXTURES = {
    "terminologies": [
        {"value": "E2E_GENDER", "terms": [
            {"value": "Male"},
            {"value": "Female"},
        ]},
    ],
    "templates": [
        {
            "value": "E2E_PERSON",
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "age", "type": "integer"},
                {"name": "gender", "type": "term", "terminology_ref": "E2E_GENDER"},
                {"name": "email", "type": "string", "semantic_type": "email"},
            ],
            "reporting": {"sync_enabled": True, "sync_strategy": "latest_only"},
        },
    ],
    "documents": [
        {"template": "E2E_PERSON", "data": {"name": "Alice", "age": 30, "gender": "Female", "email": "alice@test.com"}},
        {"template": "E2E_PERSON", "data": {"name": "Bob", "age": 25, "gender": "Male", "email": "bob@test.com"}},
        # ... 10-20 deterministic documents
    ],
}
```

#### Phase 3: Wait for Sync

Poll the reporting-sync metrics or status endpoint until all documents are synced:

```python
async def wait_for_sync(expected_count: int, timeout: float = 30.0):
    """Poll /metrics until documents_synced >= expected_count."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get("http://localhost:8005/metrics")
        metrics = resp.json()
        synced = sum(t["documents_synced"] for t in metrics.get("per_template", {}).values())
        if synced >= expected_count:
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Sync not complete after {timeout}s")
```

#### Phase 4: Compare

Query all three data stores and verify consistency:

```python
async def test_e2e_data_consistency():
    """Compare seeded data across MongoDB, PostgreSQL, and the API."""

    # 1. What we seeded (ground truth)
    expected = FIXTURES["documents"]

    # 2. What Document-Store has (MongoDB via API)
    api_docs = await fetch_all_documents("E2E_PERSON", namespace="e2e-test")

    # 3. What PostgreSQL has (direct query)
    pg_rows = await pg_pool.fetch('SELECT * FROM "doc_e2e_person" WHERE namespace = $1', "e2e-test")

    # Compare counts
    assert len(api_docs) == len(expected)
    assert len(pg_rows) == len(expected)

    # Compare values
    for seed, api_doc, pg_row in zip_by_identity(expected, api_docs, pg_rows):
        # API matches seed
        assert api_doc["data"]["name"] == seed["data"]["name"]
        assert api_doc["data"]["age"] == seed["data"]["age"]

        # PostgreSQL matches seed
        assert pg_row["name"] == seed["data"]["name"]
        assert pg_row["age"] == seed["data"]["age"]

        # PostgreSQL has correct term resolution
        assert pg_row["gender"] == seed["data"]["gender"]
        assert pg_row["gender_term_id"] is not None  # Resolved by document-store

        # Metadata consistent
        assert pg_row["document_id"] == api_doc["document_id"]
        assert pg_row["identity_hash"] == api_doc["identity_hash"]
```

#### Phase 5: Verify Schema

```python
async def test_e2e_schema_correct():
    """Verify PostgreSQL table schema matches the template."""
    columns = await pg_pool.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'doc_e2e_person' ORDER BY ordinal_position"
    )
    col_map = {r["column_name"]: r["data_type"] for r in columns}

    # System columns
    assert col_map["document_id"] == "text"
    assert col_map["namespace"] == "character varying"
    assert col_map["version"] == "integer"

    # Data columns
    assert col_map["name"] == "text"
    assert col_map["age"] == "integer"
    assert col_map["gender"] == "text"
    assert col_map["gender_term_id"] == "text"
    assert col_map["email"] == "text"

    # JSON backup columns
    assert col_map["data_json"] == "jsonb"
```

#### Phase 6: Cleanup

```bash
# Delete test namespace and all associated data
# Or: drop all doc_e2e_* tables, delete e2e-test namespace
```

### Test Scenarios

| Scenario | What it validates |
|----------|------------------|
| **Basic sync** | Seeded documents appear in PostgreSQL with correct values |
| **Term resolution** | gender "Male" → display value + term_id both in PostgreSQL |
| **Version update** | Update a document via API → PostgreSQL reflects latest version |
| **Archive** | Archive a document → PostgreSQL status changes to "archived" |
| **Schema evolution** | Add a field to the template → PostgreSQL table gets new column → new documents use it |
| **Multiple templates** | Two template types → two separate PostgreSQL tables |
| **Batch sync** | Trigger `/sync/batch/` for a template → all existing documents backfilled |
| **Integrity check** | Call `/health/integrity` → no mismatches reported |

### File Layout

```
tests/
└── e2e/
    ├── conftest.py            # Stack health checks, namespace setup/teardown
    ├── seed_fixtures.py       # Deterministic test data definitions
    ├── helpers.py             # API clients, PostgreSQL query helpers, sync waiter
    ├── test_basic_sync.py     # Documents appear in PostgreSQL
    ├── test_term_resolution.py # Term values and term_ids correct
    ├── test_versioning.py     # Updates and archive reflected
    ├── test_schema_evolution.py # Template changes → table changes
    └── test_batch_sync.py     # Backfill via batch endpoint
```

### Running

```bash
# Requires full stack running
pytest tests/e2e/ -v -m e2e --timeout=60

# Or as part of a deployment validation script
./scripts/smoke-test.sh
```

### When to Run

- After deployment to a new environment
- After infrastructure upgrades (PostgreSQL version, NATS version)
- Nightly on CI (if a full stack is available)
- Before releases, as a final gate

Not in the normal test suite — too slow, too many dependencies, and flaky by nature (timing, service startup order, resource contention).

### Estimated Scope

~15-20 E2E tests. Execution time ~30-60 seconds (dominated by sync wait times and service startup).

---

## Comparison

| Dimension | Approach A (Integration) | Approach B (E2E Smoke) |
|-----------|-------------------------|----------------------|
| **Infrastructure** | PostgreSQL only | Full stack (6 services + 3 infra) |
| **What it tests** | SQL correctness, schema evolution, upsert logic | Complete data path from API to reporting |
| **What it skips** | NATS, Template Store, MongoDB | Nothing — full pipeline |
| **Speed** | ~5-10 seconds | ~30-60 seconds |
| **Flakiness** | Low (deterministic, no async waits) | Medium (timing-sensitive, service dependencies) |
| **CI cost** | One extra container (PostgreSQL) | Full deployment or dedicated test environment |
| **Catches** | DDL bugs, type mismatches, upsert logic errors | Integration mismatches, event format drift, config errors |
| **When to build** | Soon — highest value per effort | Later — deployment validation |
| **Maintenance** | Low (isolated, fast) | Higher (stack changes affect tests) |
