"""An upsert that loses to a namespace deletion, pinned before
upsert_namespace's refactor.

Provenance: CASE-1161. The suite never reached this branch: the update path
writes only while the namespace is unlocked, and a deletion that locked it
after the upsert's read leaves the write undone. The upsert answers 409 with
the locked message and changes nothing.
"""

import uuid

import pytest

from registry.api import namespaces as namespaces_api
from registry.models.namespace import Namespace


@pytest.mark.asyncio
async def test_an_update_that_loses_to_a_deletion_lock_is_a_409(client, auth_headers, monkeypatch):
    prefix = f"c1161-{uuid.uuid4().hex[:6]}"
    r = await client.put(f"/api/registry/namespaces/{prefix}", headers=auth_headers, json={"description": "before"})
    assert r.status_code == 200, r.text

    async def locked_meanwhile(ns, changes):
        return False

    monkeypatch.setattr(namespaces_api, "set_unless_locked", locked_meanwhile)
    r = await client.put(f"/api/registry/namespaces/{prefix}", headers=auth_headers, json={"description": "after"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == namespaces_api.LOCKED_DETAIL.format(prefix=prefix)
    stored = await Namespace.find_one({"prefix": prefix})
    assert stored is not None and stored.description == "before"
