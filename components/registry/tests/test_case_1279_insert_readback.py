"""A register whose batch insert fails without naming where it stopped reads
the entries back: the ones it did write answer 'created' with their claims
confirmed, the rest 'error' with their claims released. Before, every entry
answered 'error' — the written ones too — and every claim stayed pending."""

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry


@pytest.mark.asyncio
async def test_written_entries_of_a_failed_insert_answer_created(client, auth_headers, monkeypatch):
    async def first_then_fail(cls_or_entries, *args, **kwargs):
        entries = cls_or_entries if isinstance(cls_or_entries, list) else args[0]
        await entries[0].insert()
        raise RuntimeError("connection reset mid-insert")

    monkeypatch.setattr(RegistryEntry, "insert_many", first_then_fail)
    items = [
        {"namespace": "default", "entity_type": "terms", "composite_key": {"readback1279": i}}
        for i in range(3)
    ]
    r = await client.post("/api/registry/entries/register", json=items, headers=auth_headers)
    assert r.status_code == 200, r.text
    statuses = [row["status"] for row in r.json()["results"]]
    assert statuses == ["created", "error", "error"], r.json()

    written = r.json()["results"][0]["registry_id"]
    pending = await CompositeKeyClaim.get_motor_collection().count_documents(
        {"namespace": "default", "state": "pending", "owner_entry_id": written}
    )
    assert pending == 0, "the written entry's claims are confirmed"
