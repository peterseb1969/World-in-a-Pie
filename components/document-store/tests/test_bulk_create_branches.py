"""Branch coverage for DocumentService.bulk_create (CASE-336 test bed).

These tests exist to pin the behaviour of bulk_create's high-complexity
branches BEFORE the CC=63 decomposition, so the refactor is safe-by-tests.

Key constraint: the create endpoint has a single-item fast path
(`len(items) == 1` → create_document). Only batches of >= 2 items reach
bulk_create. Every test here therefore POSTs >= 2 items.

Branches targeted (line refs against document_service.py at filing):
- continue_on_error=False sequential stop + skip-remaining (2013-2066)
- continue_on_error=True concurrent per-item error collection (1995-2012)
- update-existing: unchanged detection (2192-2212)
- update-existing: changed data → deactivate-old + version bump (2214-2220)
- mixed new + update in one batch (Stage 4 existing_by_doc_id bookkeeping)
"""

import pytest
from httpx import AsyncClient

DOCS_URL = "/api/document-store/documents"


def _person(national_id: str, **overrides) -> dict:
    """A valid PERSON payload (national_id must match ^\\d{9}$)."""
    data = {
        "national_id": national_id,
        "first_name": "John",
        "last_name": "Doe",
        "age": 34,
    }
    data.update(overrides)
    return {"namespace": "wip", "template_id": "PERSON", "data": data}


@pytest.mark.asyncio
async def test_bulk_continue_on_error_false_halts_on_first_validation_failure(
    client: AsyncClient, auth_headers: dict
):
    """continue_on_error=false halts the WHOLE batch on the first validation
    failure, before the create stage runs.

    Pins CURRENT behaviour (CASE-336 test bed): validation runs for every
    item up front, then the sequential path early-returns the moment an item
    fails. Consequences worth knowing:
      - The failing item (index 1) is reported 'error'.
      - Items AFTER it (index 2) are reported 'skipped'.
      - Items BEFORE it that already validated (index 0) are NOT created and
        DO NOT appear in results at all — total=3 but only 2 result rows.

    The index-0 disappearance is a latent silent-data wart filed as CASE-413
    (not fixed here — fixing it is a behaviour change, kept separate from the
    decomposition refactor this test bed protects). When CASE-413 lands, the
    `0 not in by_index` / `succeeded == 0` assertions below flip.
    """
    items = [
        _person("510000001"),
        # national_id violates ^\d{9}$ → validation failure on item 1
        _person("not-a-valid-id"),
        _person("510000003"),
    ]
    response = await client.post(
        f"{DOCS_URL}?continue_on_error=false",
        headers=auth_headers,
        json=items,
    )
    assert response.status_code == 200
    bulk = response.json()
    by_index = {r["index"]: r for r in bulk["results"]}

    assert bulk["total"] == 3
    # Index 0 validated cleanly but is dropped entirely on the early return.
    assert 0 not in by_index
    assert by_index[1]["status"] == "error"
    assert by_index[2]["status"] == "skipped"
    assert bulk["failed"] == 1
    # Nothing was created — all-or-nothing on validation failure.
    assert bulk["succeeded"] == 0


@pytest.mark.asyncio
async def test_bulk_continue_on_error_true_collects_per_item_errors(
    client: AsyncClient, auth_headers: dict
):
    """Default continue_on_error=true: an invalid item fails on its own;
    valid siblings still succeed (concurrent error-collection path)."""
    items = [
        _person("520000001"),
        _person("bad"),  # invalid national_id
        _person("520000003"),
    ]
    response = await client.post(
        DOCS_URL, headers=auth_headers, json=items
    )
    assert response.status_code == 200
    bulk = response.json()
    by_index = {r["index"]: r for r in bulk["results"]}

    assert by_index[0]["status"] == "created"
    assert by_index[1]["status"] == "error"
    assert by_index[2]["status"] == "created"
    assert bulk["succeeded"] == 2
    assert bulk["failed"] == 1


@pytest.mark.asyncio
async def test_bulk_unchanged_detection(client: AsyncClient, auth_headers: dict):
    """Re-submitting an identical batch yields 'unchanged' for every item —
    no new version is created when the data hasn't changed."""
    items = [_person("530000001"), _person("530000002")]

    first = (await client.post(DOCS_URL, headers=auth_headers, json=items)).json()
    assert {r["status"] for r in first["results"]} == {"created"}
    assert all(r["version"] == 1 for r in first["results"])

    second = (await client.post(DOCS_URL, headers=auth_headers, json=items)).json()
    assert {r["status"] for r in second["results"]} == {"unchanged"}
    # version stays at 1 — no new version on a no-op write
    assert all(r["version"] == 1 for r in second["results"])
    assert second["succeeded"] == 2


@pytest.mark.asyncio
async def test_bulk_update_existing_bumps_version(
    client: AsyncClient, auth_headers: dict
):
    """Re-submitting with a changed non-identity field deactivates the old
    version and creates version 2 ('updated')."""
    items = [_person("540000001", age=30), _person("540000002", age=40)]

    first = (await client.post(DOCS_URL, headers=auth_headers, json=items)).json()
    assert {r["status"] for r in first["results"]} == {"created"}

    changed = [_person("540000001", age=31), _person("540000002", age=41)]
    second = (await client.post(DOCS_URL, headers=auth_headers, json=changed)).json()
    by_index = {r["index"]: r for r in second["results"]}

    assert by_index[0]["status"] == "updated"
    assert by_index[0]["version"] == 2
    assert by_index[0]["is_new"] is False
    assert by_index[1]["status"] == "updated"
    assert by_index[1]["version"] == 2


@pytest.mark.asyncio
async def test_bulk_mixed_new_and_update(client: AsyncClient, auth_headers: dict):
    """A single batch can mix an update of an existing identity with a
    brand-new document — Stage 4 must branch per-item correctly."""
    seed = [_person("550000001", age=20)]
    # seed is single-item → create_document path; create it, then batch-update.
    await client.post(DOCS_URL, headers=auth_headers, json=seed + [_person("550000002", age=21)])

    batch = [
        _person("550000001", age=99),  # existing identity, changed → updated
        _person("550000009", age=22),  # new identity → created
    ]
    resp = (await client.post(DOCS_URL, headers=auth_headers, json=batch)).json()
    by_index = {r["index"]: r for r in resp["results"]}

    assert by_index[0]["status"] == "updated"
    assert by_index[0]["version"] == 2
    assert by_index[1]["status"] == "created"
    assert by_index[1]["version"] == 1
