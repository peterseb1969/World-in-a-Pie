"""CASE-434/436 — inline synonym conflict handling on document create.

Default is strict (on_synonym_conflict="fail"): a synonym already owned by a
different entry makes the whole create fail, the document is NOT created, and
its just-allocated entry is rolled back (CASE-436 rollback_uncommitted). "warn"
creates the document and surfaces the refusal in `warnings`. Re-supplying a
synonym the document already owns (idempotent re-create) is never a conflict.
"""

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry

NS = "wip"


async def _create(client, auth_headers, national_id, *, synonyms=None, on_conflict=None):
    payload = {
        "namespace": NS,
        "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": "A", "last_name": "B"},
    }
    if synonyms is not None:
        payload["synonyms"] = synonyms
    if on_conflict is not None:
        payload["on_synonym_conflict"] = on_conflict
    resp = await client.post(
        "/api/document-store/documents", headers=auth_headers, json=[payload]
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _doc_entry_count() -> int:
    return await RegistryEntry.find(
        {"entity_type": "documents", "status": "active"}
    ).count()


@pytest.mark.asyncio
class TestInlineSynonymConflict:
    async def test_strict_default_fails_and_rolls_back_entry(
        self, client: AsyncClient, auth_headers: dict
    ):
        # doc-A owns the synonym.
        a = await _create(client, auth_headers, "100000001", synonyms=[{"value": "CASE-9001"}])
        assert a["results"][0]["status"] == "created", a
        count_after_a = await _doc_entry_count()

        # doc-B (different identity) claims the SAME synonym, default strict.
        b = await _create(client, auth_headers, "100000002", synonyms=[{"value": "CASE-9001"}])
        item = b["results"][0]
        assert b["succeeded"] == 0 and b["failed"] == 1, b
        assert item["status"] == "error", item
        assert item["error_code"] == "synonym_conflict", item

        # doc-B's document was NOT created …
        listing = await client.get(
            "/api/document-store/documents",
            headers=auth_headers,
            params={"namespace": NS, "template_value": "PERSON"},
        )
        ids = [d["data"]["national_id"] for d in listing.json()["items"]]
        assert "100000002" not in ids

        # … and its registry entry was rolled back (no net new doc entry).
        assert await _doc_entry_count() == count_after_a

    async def test_warn_mode_creates_and_warns(self, client: AsyncClient, auth_headers: dict):
        await _create(client, auth_headers, "200000001", synonyms=[{"value": "CASE-7001"}])
        c = await _create(
            client, auth_headers, "200000002",
            synonyms=[{"value": "CASE-7001"}], on_conflict="warn",
        )
        item = c["results"][0]
        assert item["status"] == "created", item
        assert any("CASE-7001" in w or "not registered" in w for w in item["warnings"]), item

    async def test_idempotent_recreate_same_synonym_ok(
        self, client: AsyncClient, auth_headers: dict
    ):
        first = await _create(client, auth_headers, "300000001", synonyms=[{"value": "CASE-6001"}])
        assert first["results"][0]["status"] == "created"
        # Same document + same synonym again — self-owned, not a conflict.
        again = await _create(client, auth_headers, "300000001", synonyms=[{"value": "CASE-6001"}])
        assert again["failed"] == 0, again
        # Identical content → the single-create API reports "skipped" (no new
        # version); the key point is the self-owned synonym is NOT a conflict.
        assert again["results"][0]["status"] in ("skipped", "unchanged", "updated", "created")

    async def test_bulk_per_item_conflict_isolated(self, client: AsyncClient, auth_headers: dict):
        # Pre-own a synonym.
        await _create(client, auth_headers, "400000001", synonyms=[{"value": "CASE-5001"}])

        # Bulk: item-0 conflicts on CASE-5001, item-1 uses a fresh synonym.
        resp = await client.post(
            "/api/document-store/documents",
            headers=auth_headers,
            json=[
                {"namespace": NS, "template_id": "PERSON",
                 "data": {"national_id": "400000002", "first_name": "A", "last_name": "B"},
                 "synonyms": [{"value": "CASE-5001"}]},
                {"namespace": NS, "template_id": "PERSON",
                 "data": {"national_id": "400000003", "first_name": "A", "last_name": "B"},
                 "synonyms": [{"value": "CASE-5002"}]},
            ],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_index = {r["index"]: r for r in body["results"]}
        assert by_index[0]["status"] == "error"
        assert by_index[0]["error_code"] == "synonym_conflict"
        assert by_index[1]["status"] == "created"  # sibling unaffected
