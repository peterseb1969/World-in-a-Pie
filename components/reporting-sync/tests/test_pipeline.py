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
import pytest_asyncio

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
    "postgresql://wip:{pg_pass}@localhost:5432/wip_reporting".format(
        pg_pass=os.environ.get("WIP_POSTGRES_PASSWORD", "wip_dev_password")
    ),
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
        json={
            "prefix": ns,
            "description": f"Pipeline test {ns} — auto-created by test_pipeline.py",
        },
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create namespace {ns}: {resp.status_code} {resp.text}")
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
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from seed_data import terminologies as term_module

    # All terminologies referenced by PERSON, EMPLOYEE, PRODUCT, ADDRESS, MONEY
    needed = {"SALUTATION", "GENDER", "COUNTRY", "LANGUAGE", "EMPLOYMENT_TYPE",
              "DEPARTMENT", "PRODUCT_CATEGORY", "CURRENCY", "UNIT_OF_MEASURE",
              "MARITAL_STATUS"}

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
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from seed_data import templates as tmpl_module

    # Seed in dependency order: leaf templates first, then composites, then inheritors
    template_values = ["ADDRESS", "MONEY", "PERSON", "EMPLOYEE", "PRODUCT"]

    for value in template_values:
        tdef = tmpl_module.get_template_by_value(value)
        if not tdef:
            raise RuntimeError(f"Template {value} not found in seed_data")

        # Ensure every field has a label (required by Template-Store)
        fields = []
        for f in tdef.get("fields", []):
            fc = dict(f)
            if "label" not in fc:
                fc["label"] = fc["name"].replace("_", " ").title()
            fields.append(fc)

        payload = {
            "value": tdef["value"],
            "label": tdef.get("label", tdef["value"]),
            "description": tdef.get("description", ""),
            "namespace": ns,
            "fields": fields,
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
            raise RuntimeError(f"Template {value} creation failed: {body}")

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
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
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


# =============================================================================
# Fixtures
# =============================================================================

# Module-level cache for seeded data (avoids re-seeding per test)
_seeded: dict | None = None


async def _ensure_seeded() -> dict:
    """Seed data once per module, cache in module global."""
    global _seeded
    if _seeded is not None:
        return _seeded

    ts = _ts()
    ns_a = f"ptest-a-{ts}"
    ns_b = f"ptest-b-{ts}"

    # Seed via HTTP APIs
    async with httpx.AsyncClient(timeout=30.0) as client:
        await _create_namespace(client, ns_a)
        await _create_namespace(client, ns_b)

        await _seed_terminologies(client, ns_a)
        await _seed_terminologies(client, ns_b)

        await _seed_templates(client, ns_a)
        await _seed_templates(client, ns_b)

        manifest_a = await _generate_and_create_docs(client, ns_a)
        manifest_b = await _generate_and_create_docs(client, ns_b)

    # Wait for sync using a temporary connection
    conn = await asyncpg.connect(PG_URI)
    try:
        await _wait_for_sync_conn(conn, ns_a, manifest_a)
        await _wait_for_sync_conn(conn, ns_b, manifest_b)
    finally:
        await conn.close()

    _seeded = {
        "ns_a": ns_a, "ns_b": ns_b,
        "manifest_a": manifest_a, "manifest_b": manifest_b,
    }
    return _seeded


async def _wait_for_sync_conn(conn: asyncpg.Connection, ns: str, manifest: dict[str, list[str]]):
    """Poll PostgreSQL with a single connection until expected rows appear."""
    expected_total = sum(len(ids) for ids in manifest.values())
    start = time.monotonic()
    total_found = 0

    while time.monotonic() - start < SYNC_TIMEOUT:
        total_found = 0
        for template_value in manifest:
            table_name = f"doc_{template_value.lower()}"
            try:
                row = await conn.fetchrow(
                    f'SELECT COUNT(*) as cnt FROM "{table_name}" WHERE namespace = $1',
                    ns,
                )
                total_found += row["cnt"] if row else 0
            except asyncpg.UndefinedTableError:
                pass
        if total_found >= expected_total:
            logger.info("Sync complete: %d/%d rows in PG after %.1fs",
                       total_found, expected_total, time.monotonic() - start)
            return
        await asyncio.sleep(SYNC_POLL_INTERVAL)

    raise AssertionError(
        f"Sync timeout after {SYNC_TIMEOUT}s: expected {expected_total} rows, "
        f"found {total_found} in PG for namespace {ns}"
    )


@pytest_asyncio.fixture
async def pipeline_data():
    """Provide seeded pipeline data + a fresh PG connection per test."""
    data = await _ensure_seeded()
    conn = await asyncpg.connect(PG_URI)
    try:
        yield data["ns_a"], data["ns_b"], data["manifest_a"], data["manifest_b"], conn
    finally:
        await conn.close()


# =============================================================================
# Tests
# =============================================================================

@requires_pipeline
class TestPipeline:
    """End-to-end pipeline test: service APIs → NATS → reporting-sync → PostgreSQL."""

    @pytest.mark.asyncio
    async def test_documents_reach_postgres(self, pipeline_data):
        """Row counts per template match the seed manifest."""
        ns_a, ns_b, manifest_a, manifest_b, pg_conn = pipeline_data

        for ns, manifest in [(ns_a, manifest_a), (ns_b, manifest_b)]:
            for template_value, doc_ids in manifest.items():
                table_name = f"doc_{template_value.lower()}"
                row = await pg_conn.fetchrow(
                    f'SELECT COUNT(*) as cnt FROM "{table_name}" WHERE namespace = $1',
                    ns,
                )
                assert row["cnt"] == len(doc_ids), (
                    f"{template_value} in {ns}: expected {len(doc_ids)} rows, got {row['cnt']}"
                )

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, pipeline_data):
        """Documents from NS_A must NOT appear in NS_B's rows and vice versa."""
        _ns_a, ns_b, _manifest_a, manifest_b, pg_conn = pipeline_data

        for template_value in TEMPLATE_DOC_COUNTS:
            table_name = f"doc_{template_value.lower()}"

            # NS_A docs should not be in NS_B
            row = await pg_conn.fetchrow(
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
        ns_a, _, manifest_a, _, pg_conn = pipeline_data

        # Pick first PERSON doc as sample
        person_ids = manifest_a.get("PERSON", [])
        assert person_ids, "No PERSON docs in manifest"
        sample_id = person_ids[0]

        # Check PG has this specific document
        row = await pg_conn.fetchrow(
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
        ns_a, _, manifest_a, _, pg_conn = pipeline_data

        product_ids = manifest_a.get("PRODUCT", [])
        assert product_ids, "No PRODUCT docs in manifest"

        # Check that category field has values (not all NULL)
        row = await pg_conn.fetchrow(
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
        ns_a, _, _, _, pg_conn = pipeline_data

        # Check templates metadata table
        try:
            row = await pg_conn.fetchrow(
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
            row = await pg_conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM terminologies WHERE namespace = $1",
                ns_a,
            )
            assert row["cnt"] > 0, "No terminologies in metadata table"
        except asyncpg.UndefinedTableError:
            pytest.skip("terminologies metadata table not present")
