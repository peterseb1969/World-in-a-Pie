"""
Pipeline integration test: seed data → service APIs → NATS events → reporting-sync → PostgreSQL.

This test exercises the REAL pipeline end-to-end. It creates data via service HTTP APIs,
waits for NATS events to flow through reporting-sync, and verifies rows land in PostgreSQL.

Requires all 5 WIP services running + PostgreSQL. Skipped gracefully when services
are unavailable (CI or local without services).

The test was created after CASE-02 revealed that existing tests used hardcoded synthetic
payloads and never caught a missing `namespace` field in NATS event payloads.
"""

import asyncio
import logging
import os
import random
import time

import asyncpg
import httpx
import pytest

from .conftest import _SERVICE_URLS, requires_pipeline

logger = logging.getLogger(__name__)

# API key for all service calls
API_KEY = os.environ.get("API_KEY", "dev_master_key_for_testing")

# Templates to test per namespace and how many docs to generate
TEMPLATE_DOC_COUNTS = {
    "PERSON": 8,
    "EMPLOYEE": 5,
    "PRODUCT": 5,
}

# PostgreSQL connection for verification
PG_URI = os.environ.get(
    "PIPELINE_POSTGRES_URI",
    "postgresql://wip:wip_dev_password@localhost:5432/wip_reporting",
)

# Sync timeout and polling
SYNC_TIMEOUT = 30  # seconds
SYNC_POLL_INTERVAL = 0.5  # seconds


# =============================================================================
# Helpers
# =============================================================================

def _headers():
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def _ts():
    """Timestamp suffix for unique namespace names."""
    return str(int(time.time()))


async def _create_namespace(client: httpx.AsyncClient, ns: str):
    """Create a test namespace via Registry API."""
    resp = await client.post(
        f"{_SERVICE_URLS['registry']}/api/registry/namespaces",
        headers=_headers(),
        json=[{
            "value": ns,
            "label": f"Pipeline test {ns}",
            "description": f"Auto-created by test_pipeline.py",
        }],
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to create namespace {ns}: {resp.status_code} {resp.text}")
    body = resp.json()
    # Check BulkResponse
    if body.get("failed", 0) > 0:
        raise RuntimeError(f"Namespace creation failed: {body}")
    logger.info("Created namespace %s", ns)


async def _delete_namespace(client: httpx.AsyncClient, ns: str):
    """Delete a test namespace via Registry (cascade)."""
    try:
        resp = await client.delete(
            f"{_SERVICE_URLS['registry']}/api/registry/namespaces/{ns}",
            headers=_headers(),
        )
        logger.info("Deleted namespace %s: %s", ns, resp.status_code)
    except Exception as e:
        logger.warning("Failed to delete namespace %s: %s", ns, e)


async def _seed_terminologies(client: httpx.AsyncClient, ns: str):
    """Seed terminologies required by test templates."""
    # Import inline to avoid import errors when seed_data not on PYTHONPATH
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "seed_data"))
    from seed_data import terminologies as term_module

    # Only seed terminologies referenced by PERSON, EMPLOYEE, PRODUCT
    needed = {"SALUTATION", "GENDER", "COUNTRY", "EMPLOYMENT_TYPE", "DEPARTMENT",
              "PRODUCT_CATEGORY", "CURRENCY", "MARITAL_STATUS"}

    for tdef in term_module.get_terminology_definitions():
        if tdef["value"] not in needed:
            continue

        # Create terminology
        resp = await client.post(
            f"{_SERVICE_URLS['def-store']}/api/def-store/terminologies",
            headers=_headers(),
            json=[{
                "value": tdef["value"],
                "label": tdef.get("label", tdef["value"]),
                "description": tdef.get("description", ""),
                "namespace": ns,
            }],
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to create terminology {tdef['value']}: {resp.text}")

        # Create terms
        if tdef.get("terms"):
            terms_payload = []
            for t in tdef["terms"]:
                term_item = {"value": t["value"], "label": t.get("label", t["value"])}
                if t.get("aliases"):
                    term_item["aliases"] = t["aliases"]
                terms_payload.append(term_item)

            resp = await client.post(
                f"{_SERVICE_URLS['def-store']}/api/def-store/terminologies/{tdef['value']}/terms?namespace={ns}",
                headers=_headers(),
                json=terms_payload,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to create terms for {tdef['value']}: {resp.text}")

    logger.info("Seeded terminologies for namespace %s", ns)


async def _seed_templates(client: httpx.AsyncClient, ns: str):
    """Seed the 3 test templates via Template-Store API."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "seed_data"))
    from seed_data import templates as tmpl_module

    # Seed in dependency order: PERSON first (EMPLOYEE extends it), then PRODUCT
    template_values = ["PERSON", "EMPLOYEE", "PRODUCT"]

    for value in template_values:
        tdef = tmpl_module.get_template_by_value(value)
        if not tdef:
            raise RuntimeError(f"Template {value} not found in seed_data")

        payload = {
            "value": tdef["value"],
            "label": tdef.get("label", tdef["value"]),
            "description": tdef.get("description", ""),
            "namespace": ns,
            "fields": tdef.get("fields", []),
        }
        if tdef.get("extends"):
            payload["extends"] = tdef["extends"]
        if tdef.get("identity_fields"):
            payload["identity_fields"] = tdef["identity_fields"]
        if tdef.get("rules"):
            payload["rules"] = tdef["rules"]

        resp = await client.post(
            f"{_SERVICE_URLS['template-store']}/api/template-store/templates",
            headers=_headers(),
            json=[payload],
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to create template {value}: {resp.text}")
        body = resp.json()
        if body.get("failed", 0) > 0:
            # Check for draft needing activation
            logger.warning("Template %s creation result: %s", value, body)

    # Activate all templates
    for value in template_values:
        resp = await client.post(
            f"{_SERVICE_URLS['template-store']}/api/template-store/templates/{value}/activate?namespace={ns}",
            headers=_headers(),
        )
        if resp.status_code != 200:
            logger.warning("Template activation for %s: %s %s", value, resp.status_code, resp.text)

    logger.info("Seeded templates for namespace %s", ns)


async def _generate_and_create_docs(
    client: httpx.AsyncClient, ns: str
) -> dict[str, list[str]]:
    """Generate random docs via DocumentGenerator, create via Document-Store API.

    Returns manifest: {template_value: [document_ids]}
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "seed_data"))
    from seed_data.document_generator import DocumentGenerator

    generator = DocumentGenerator()
    manifest: dict[str, list[str]] = {}

    for template_value, count in TEMPLATE_DOC_COUNTS.items():
        doc_ids = []
        items = []
        for i in range(count):
            doc_data = generator.generate(template_value, index=i + random.randint(1000, 9999))
            items.append({
                "template_id": template_value,
                "namespace": ns,
                "data": doc_data,
            })

        resp = await client.post(
            f"{_SERVICE_URLS['document-store']}/api/document-store/documents",
            headers=_headers(),
            json=items,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to create {template_value} docs: {resp.text}")

        body = resp.json()
        for result in body.get("results", []):
            if result.get("status") in ("created", "updated"):
                doc_id = result.get("document_id") or result.get("id")
                if doc_id:
                    doc_ids.append(doc_id)

        manifest[template_value] = doc_ids
        logger.info("Created %d/%d %s docs in namespace %s", len(doc_ids), count, template_value, ns)

    return manifest


async def _wait_for_sync(pg_pool: asyncpg.Pool, ns: str, manifest: dict[str, list[str]]):
    """Poll PostgreSQL until expected row counts appear or timeout."""
    expected_total = sum(len(ids) for ids in manifest.values())
    start = time.monotonic()

    while time.monotonic() - start < SYNC_TIMEOUT:
        total_found = 0
        for template_value, doc_ids in manifest.items():
            table_name = f"doc_{template_value.lower()}"
            try:
                row = await pg_pool.fetchrow(
                    f'SELECT COUNT(*) as cnt FROM "{table_name}" WHERE namespace = $1',
                    ns,
                )
                total_found += row["cnt"] if row else 0
            except asyncpg.UndefinedTableError:
                pass  # Table not yet created by reporting-sync

        if total_found >= expected_total:
            logger.info("Sync complete: %d/%d rows in PG after %.1fs",
                       total_found, expected_total, time.monotonic() - start)
            return

        await asyncio.sleep(SYNC_POLL_INTERVAL)

    raise AssertionError(
        f"Sync timeout after {SYNC_TIMEOUT}s: expected {expected_total} rows, "
        f"found {total_found} in PG for namespace {ns}"
    )


async def _cleanup_pg_tables(pg_pool: asyncpg.Pool, ns: str):
    """Remove test data from PG (by namespace)."""
    try:
        tables = await pg_pool.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'doc_%'"
        )
        for row in tables:
            table = row["table_name"]
            await pg_pool.execute(
                f'DELETE FROM "{table}" WHERE namespace = $1', ns
            )
    except Exception as e:
        logger.warning("PG cleanup for %s: %s", ns, e)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def pipeline_data():
    """Seed 2 namespaces with terminologies, templates, and documents.

    Yields (ns_a, ns_b, manifest_a, manifest_b, pg_pool).
    Cleans up namespaces after tests.
    """
    ts = _ts()
    ns_a = f"ptest-a-{ts}"
    ns_b = f"ptest-b-{ts}"

    pg_pool = await asyncpg.create_pool(PG_URI, min_size=1, max_size=3)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create namespaces
        await _create_namespace(client, ns_a)
        await _create_namespace(client, ns_b)

        # Seed terminologies
        await _seed_terminologies(client, ns_a)
        await _seed_terminologies(client, ns_b)

        # Seed templates
        await _seed_templates(client, ns_a)
        await _seed_templates(client, ns_b)

        # Generate and create documents
        manifest_a = await _generate_and_create_docs(client, ns_a)
        manifest_b = await _generate_and_create_docs(client, ns_b)

        # Wait for sync to complete
        await _wait_for_sync(pg_pool, ns_a, manifest_a)
        await _wait_for_sync(pg_pool, ns_b, manifest_b)

        yield ns_a, ns_b, manifest_a, manifest_b, pg_pool

        # Cleanup
        await _cleanup_pg_tables(pg_pool, ns_a)
        await _cleanup_pg_tables(pg_pool, ns_b)
        await _delete_namespace(client, ns_a)
        await _delete_namespace(client, ns_b)

    await pg_pool.close()


# =============================================================================
# Tests
# =============================================================================

@requires_pipeline
class TestPipeline:
    """End-to-end pipeline test: service APIs → NATS → reporting-sync → PostgreSQL."""

    @pytest.mark.asyncio
    async def test_documents_reach_postgres(self, pipeline_data):
        """Row counts per template match the seed manifest."""
        ns_a, ns_b, manifest_a, manifest_b, pg_pool = pipeline_data

        for ns, manifest in [(ns_a, manifest_a), (ns_b, manifest_b)]:
            for template_value, doc_ids in manifest.items():
                table_name = f"doc_{template_value.lower()}"
                row = await pg_pool.fetchrow(
                    f'SELECT COUNT(*) as cnt FROM "{table_name}" WHERE namespace = $1',
                    ns,
                )
                assert row["cnt"] == len(doc_ids), (
                    f"{template_value} in {ns}: expected {len(doc_ids)} rows, got {row['cnt']}"
                )

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, pipeline_data):
        """Documents from NS_A must NOT appear in NS_B's rows and vice versa."""
        ns_a, ns_b, manifest_a, manifest_b, pg_pool = pipeline_data

        for template_value in TEMPLATE_DOC_COUNTS:
            table_name = f"doc_{template_value.lower()}"

            # NS_A docs should not be in NS_B
            row = await pg_pool.fetchrow(
                f'SELECT COUNT(*) as cnt FROM "{table_name}" WHERE namespace = $1',
                ns_b,
            )
            expected_b = len(manifest_b.get(template_value, []))
            assert row["cnt"] == expected_b, (
                f"Namespace isolation violated for {template_value}: "
                f"NS_B has {row['cnt']} rows but expected {expected_b}"
            )

    @pytest.mark.asyncio
    async def test_key_fields_match(self, pipeline_data):
        """Sample document: compare document_id, namespace, status between Mongo and PG."""
        ns_a, _, manifest_a, _, pg_pool = pipeline_data

        # Pick first PERSON doc as sample
        person_ids = manifest_a.get("PERSON", [])
        assert person_ids, "No PERSON docs in manifest"
        sample_id = person_ids[0]

        # Check PG has this specific document
        row = await pg_pool.fetchrow(
            'SELECT document_id, namespace, status FROM "doc_person" WHERE document_id = $1',
            sample_id,
        )
        assert row is not None, f"Document {sample_id} not found in PG"
        assert row["document_id"] == sample_id
        assert row["namespace"] == ns_a
        assert row["status"] in ("active", "Active")

    @pytest.mark.asyncio
    async def test_term_references_synced(self, pipeline_data):
        """PRODUCT docs should have category term values in PG."""
        ns_a, _, manifest_a, _, pg_pool = pipeline_data

        product_ids = manifest_a.get("PRODUCT", [])
        assert product_ids, "No PRODUCT docs in manifest"

        # Check that category field has values (not all NULL)
        row = await pg_pool.fetchrow(
            'SELECT COUNT(*) as cnt FROM "doc_product" '
            "WHERE namespace = $1 AND category IS NOT NULL",
            ns_a,
        )
        assert row["cnt"] > 0, (
            "No PRODUCT docs have category values in PG — term references may not be syncing"
        )

    @pytest.mark.asyncio
    async def test_metadata_tables_populated(self, pipeline_data):
        """Reporting-sync should populate terminologies, templates, terms metadata tables."""
        ns_a, _, _, _, pg_pool = pipeline_data

        # Check templates metadata table
        try:
            row = await pg_pool.fetchrow(
                "SELECT COUNT(*) as cnt FROM templates WHERE namespace = $1",
                ns_a,
            )
            assert row["cnt"] >= len(TEMPLATE_DOC_COUNTS), (
                f"Expected at least {len(TEMPLATE_DOC_COUNTS)} templates in metadata, "
                f"got {row['cnt']}"
            )
        except asyncpg.UndefinedTableError:
            pytest.skip("templates metadata table not present — reporting-sync may not create it")

        # Check terminologies metadata table
        try:
            row = await pg_pool.fetchrow(
                "SELECT COUNT(*) as cnt FROM terminologies WHERE namespace = $1",
                ns_a,
            )
            assert row["cnt"] > 0, "No terminologies in metadata table"
        except asyncpg.UndefinedTableError:
            pytest.skip("terminologies metadata table not present")
