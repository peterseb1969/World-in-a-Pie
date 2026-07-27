"""Timestamp filters must match documents regardless of stored BSON type.

The corpus holds ``created_at``/``updated_at`` in two BSON types: service
writes store dates (the model declares datetime), while the restore engine
inserts archive rows raw — JSON — so restored documents carry the same
fields as ISO strings. MongoDB comparisons are type-bracketed, so a filter
value of one type silently skips rows stored as the other: HTTP 200, rows
missing, no error. These tests pin the contract that a timestamp filter
sees every document, whichever type a row happens to hold, and that an
unparseable timestamp value is rejected loudly instead of matching nothing.
"""

import pytest
from httpx import AsyncClient

from document_store.models.document import Document


async def _create_person(client: AsyncClient, auth_headers: dict, national_id: str, name: str) -> str:
    payload = {
        "namespace": "wip",
        "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": name, "last_name": "Case821"},
    }
    response = await client.post(
        "/api/document-store/documents", headers=auth_headers, json=[payload]
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    bulk = response.json()
    assert bulk["succeeded"] == 1, f"Create failed: {bulk}"
    return bulk["results"][0]["document_id"]


async def _stringify_timestamps(document_id: str) -> None:
    """Rewrite one document's timestamps to ISO strings, as a restore does."""
    coll = Document.get_motor_collection()
    row = await coll.find_one({"document_id": document_id})
    assert row is not None
    result = await coll.update_one(
        {"_id": row["_id"]},
        {"$set": {
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }},
    )
    assert result.modified_count == 1


async def _query_totals(client: AsyncClient, auth_headers: dict, filters: list[dict]) -> dict:
    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={"template_id": "PERSON", "filters": filters},
    )
    return response


@pytest.mark.asyncio
async def test_gte_matches_date_and_string_stored_rows(client: AsyncClient, auth_headers: dict):
    """A past-threshold gte must return BOTH the date-typed and string-typed row."""
    date_doc = await _create_person(client, auth_headers, "821000001", "DateRow")
    string_doc = await _create_person(client, auth_headers, "821000002", "StringRow")
    await _stringify_timestamps(string_doc)

    response = await _query_totals(client, auth_headers, [
        {"field": "updated_at", "operator": "gte", "value": "2020-01-01T00:00:00"},
    ])
    assert response.status_code == 200
    data = response.json()
    ids = {item["document_id"] for item in data["items"]}
    assert {date_doc, string_doc} <= ids, (
        f"gte filter lost rows by BSON type: got {ids}"
    )

    # Same contract on created_at — the field with the known mixed corpus.
    response = await _query_totals(client, auth_headers, [
        {"field": "created_at", "operator": "gte", "value": "2020-01-01T00:00:00"},
    ])
    assert response.status_code == 200
    ids = {item["document_id"] for item in response.json()["items"]}
    assert {date_doc, string_doc} <= ids


@pytest.mark.asyncio
async def test_future_threshold_excludes_both_storage_types(client: AsyncClient, auth_headers: dict):
    """The bound must hold for string rows too — visibility, not a match-everything net."""
    await _create_person(client, auth_headers, "821000003", "DateRow2")
    string_doc = await _create_person(client, auth_headers, "821000004", "StringRow2")
    await _stringify_timestamps(string_doc)

    response = await _query_totals(client, auth_headers, [
        {"field": "updated_at", "operator": "gte", "value": "2030-01-01T00:00:00"},
    ])
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_lt_window_matches_string_stored_row(client: AsyncClient, auth_headers: dict):
    """Range windows (gte + lt) must bound string-stored rows correctly."""
    string_doc = await _create_person(client, auth_headers, "821000005", "StringRow3")
    await _stringify_timestamps(string_doc)

    response = await _query_totals(client, auth_headers, [
        {"field": "created_at", "operator": "gte", "value": "2020-01-01T00:00:00"},
        {"field": "created_at", "operator": "lt", "value": "2030-01-01T00:00:00"},
    ])
    assert response.status_code == 200
    ids = {item["document_id"] for item in response.json()["items"]}
    assert string_doc in ids


@pytest.mark.asyncio
async def test_unparseable_timestamp_value_is_422(client: AsyncClient, auth_headers: dict):
    """A timestamp filter that cannot be parsed fails loud, not silently empty."""
    response = await _query_totals(client, auth_headers, [
        {"field": "updated_at", "operator": "gte", "value": "not-a-timestamp"},
    ])
    assert response.status_code == 422
    assert "updated_at" in response.json()["detail"]


@pytest.mark.asyncio
async def test_data_field_filters_stay_verbatim(client: AsyncClient, auth_headers: dict):
    """data.* values are stored and compared as submitted — no coercion there."""
    await _create_person(client, auth_headers, "821000006", "Verbatim")
    response = await _query_totals(client, auth_headers, [
        {"field": "data.first_name", "operator": "eq", "value": "Verbatim"},
    ])
    assert response.status_code == 200
    assert response.json()["total"] == 1


# ---------------------------------------------------------------------------
# Restore-side: archive rows must re-hydrate timestamps to BSON dates
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402

from document_store.services.backup_engine import (  # noqa: E402
    DirectRestoreEngine,
    _rehydrate_timestamps,
)


class TestRehydrateTimestamps:
    def test_iso_strings_become_datetime(self):
        row = {
            "created_at": "2026-06-25T21:51:03.470000",
            "updated_at": "2026-06-25T21:51:03Z",
            "document_id": "abc",
        }
        _rehydrate_timestamps(row)
        assert isinstance(row["created_at"], datetime)
        assert isinstance(row["updated_at"], datetime)
        assert row["document_id"] == "abc"

    def test_nested_payloads_and_non_timestamp_keys_untouched(self):
        row = {
            "data": {"expires_at": "2026-01-01T00:00:00"},
            "metadata": {"custom": {"loaded_at": "2026-01-01T00:00:00"}},
            "format": "not-a-timestamp-key",
        }
        _rehydrate_timestamps(row)
        assert row["data"]["expires_at"] == "2026-01-01T00:00:00"
        assert row["metadata"]["custom"]["loaded_at"] == "2026-01-01T00:00:00"
        assert row["format"] == "not-a-timestamp-key"

    def test_unparseable_string_left_as_is(self):
        row = {"created_at": "garbage"}
        _rehydrate_timestamps(row)
        assert row["created_at"] == "garbage"

    def test_existing_datetime_left_as_is(self):
        now = datetime(2026, 6, 25, 21, 51, 3)
        row = {"created_at": now}
        _rehydrate_timestamps(row)
        assert row["created_at"] is now


@pytest.mark.asyncio
async def test_insert_batch_stores_bson_dates(client: AsyncClient, auth_headers: dict):
    """Rows inserted by the restore path carry datetime, not strings.

    Drives _insert_batch itself — the choke point every restore insert
    flows through — against the real test collection, with rows shaped
    like parsed archive JSON (ISO-string timestamps).
    """
    coll = Document.get_motor_collection()
    engine = DirectRestoreEngine(coll.database.client, None, lambda _: None)
    batch = [{
        "document_id": "case821-restore-row",
        "namespace": "wip",
        "template_id": "case821-tpl",
        "version": 1,
        "status": "active",
        "data": {"submitted_at": "2026-01-01T00:00:00"},
        "created_at": "2026-06-25T21:51:03.470000+00:00",
        "updated_at": "2026-07-26T12:00:00Z",
    }]
    try:
        await engine._insert_batch(coll, batch, "documents")
        stored = await coll.find_one({"document_id": "case821-restore-row"})
        assert isinstance(stored["created_at"], datetime)
        assert isinstance(stored["updated_at"], datetime)
        # Caller content stays as submitted.
        assert stored["data"]["submitted_at"] == "2026-01-01T00:00:00"
    finally:
        await coll.delete_many({"document_id": "case821-restore-row"})
