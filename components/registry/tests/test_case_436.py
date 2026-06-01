"""CASE-436 — rollback_uncommitted: privileged release of a just-allocated entry.

document-store's strict-mode synonym-conflict rollback must release the doc's
own registry entry even in a retain-mode namespace (where normal hard-delete is
refused). The registry gates a `rollback_uncommitted` bypass to privileged
callers (wip-admins / wip-services): it hard-deletes the entry + releases its
claims while skipping the deletion_mode='full' gate.

These tests use the master key (wip-admins → privileged) against the default
(retain-mode) namespace.
"""

import pytest
from httpx import AsyncClient

from registry.models.composite_key_claim import CompositeKeyClaim


async def _register(client: AsyncClient, auth_headers: dict, value: str) -> str:
    resp = await client.post(
        "/api/registry/entries/register",
        headers=auth_headers,
        json=[{
            "namespace": "default",
            "entity_type": "documents",
            "composite_key": {"value": value},
        }],
    )
    assert resp.status_code == 200, resp.text
    res = resp.json()["results"][0]
    assert res["status"] == "created", res
    return res["registry_id"]


async def _delete(client, auth_headers, entry_id, *, hard, rollback=False):
    resp = await client.request(
        "DELETE",
        "/api/registry/entries",
        headers=auth_headers,
        json=[{
            "entry_id": entry_id,
            "hard_delete": hard,
            "rollback_uncommitted": rollback,
        }],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
class TestRollbackUncommitted:
    async def test_plain_hard_delete_blocked_in_retain(self, client: AsyncClient, auth_headers: dict):
        """Regression guard: a normal hard-delete in a retain namespace is still
        refused — the deletion_mode gate stands for non-rollback deletes."""
        entry_id = await _register(client, auth_headers, "CASE436_GATED")
        res = await _delete(client, auth_headers, entry_id, hard=True, rollback=False)
        assert res["status"] == "error", res
        assert "deletion_mode" in (res.get("error") or "")

    async def test_rollback_uncommitted_bypasses_gate_for_privileged(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A privileged caller (master key = wip-admins) can release a
        just-allocated entry in a retain namespace via rollback_uncommitted."""
        entry_id = await _register(client, auth_headers, "CASE436_ROLLBACK")

        # The entry's primary claim exists before rollback.
        claims_before = await CompositeKeyClaim.find(
            {"owner_entry_id": entry_id}
        ).to_list()
        assert len(claims_before) >= 1

        res = await _delete(client, auth_headers, entry_id, hard=False, rollback=True)
        assert res["status"] == "deleted", res

        # Entry is gone — a by-id lookup no longer finds it.
        lookup = await client.post(
            "/api/registry/entries/lookup/by-id",
            headers=auth_headers,
            json=[{"entry_id": entry_id}],
        )
        assert lookup.json()["results"][0]["status"] == "not_found"

        # Claims released (CASE-427 release_for_owner on hard delete).
        claims_after = await CompositeKeyClaim.find(
            {"owner_entry_id": entry_id}
        ).to_list()
        assert claims_after == []
