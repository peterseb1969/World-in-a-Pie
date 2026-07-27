"""Template identity is the name; versions are coordinates on it.

The template's registered identity is (namespace, value): the name is
immutable (a rename is a fork — a new template — never a new version),
the identity_fields declaration is immutable across versions (document
identity must stay comparable across the whole version catalog), and a
restore onto a target that already owns the value under a different ID
fails loudly instead of silently re-parenting the archive's versions.

Runs against the real in-process Registry (conftest mounts it), so the
composite-key registration and its dedup are exercised for real.
"""

import pytest
from httpx import AsyncClient

TEMPLATES = "/api/template-store/templates"


def _payload(value: str, **overrides) -> dict:
    base = {
        "namespace": "wip",
        "value": value,
        "label": f"{value} label",
        "identity_fields": ["code"],
        "fields": [
            {"name": "code", "label": "Code", "type": "string", "mandatory": True},
            {"name": "note", "label": "Note", "type": "string", "mandatory": False},
        ],
    }
    base.update(overrides)
    return base


async def _create(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    resp = await client.post(TEMPLATES, headers=auth_headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_rename_via_update_is_rejected_as_fork(client: AsyncClient, auth_headers: dict):
    """An update carrying a different value is rejected: the name is the
    template's identity, so a rename is a new template, not a new version."""
    created = await _create(client, auth_headers, _payload("FORK_RENAME"))
    assert created["status"] == "created"

    resp = await client.put(
        TEMPLATES,
        headers=auth_headers,
        json=[{"template_id": created["id"], "value": "FORK_RENAMED", "label": "renamed"}],
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert "fork" in item["error"]

    # The original name still resolves to version 1 — nothing was minted.
    got = await client.get(
        f"{TEMPLATES}/by-value/FORK_RENAME?namespace=wip", headers=auth_headers
    )
    assert got.status_code == 200
    assert got.json()["version"] == 1


@pytest.mark.asyncio
async def test_identity_fields_change_via_update_is_rejected_as_fork(
    client: AsyncClient, auth_headers: dict
):
    """An update carrying different identity_fields is rejected: a changed
    identity declaration would re-key every document on next write (the
    parallel-orphan failure shape), so it is a fork, not a version."""
    created = await _create(client, auth_headers, _payload("FORK_IDENTITY"))
    assert created["status"] == "created"

    resp = await client.put(
        TEMPLATES,
        headers=auth_headers,
        json=[{"template_id": created["id"], "identity_fields": ["note"]}],
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert "identity_fields are immutable" in item["error"]

    # Unchanged identity_fields on an update are fine (no-op merge).
    resp = await client.put(
        TEMPLATES,
        headers=auth_headers,
        json=[{
            "template_id": created["id"],
            "identity_fields": ["code"],
            "label": "new label",
        }],
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "updated"
    assert item["version"] == 2


@pytest.mark.asyncio
async def test_upsert_create_versions_and_reports_diff(client: AsyncClient, auth_headers: dict):
    """Re-creating an existing value with a changed schema mints the next
    version and reports the diff — create is an upsert keyed on the name."""
    first = await _create(client, auth_headers, _payload("UPSERT_DIFF"))
    assert first["status"] == "created"

    changed = _payload("UPSERT_DIFF")
    changed["fields"].append(
        {"name": "extra", "label": "Extra", "type": "string", "mandatory": False}
    )
    second = await _create(client, auth_headers, changed)
    assert second["status"] == "updated"
    assert second["version"] == 2
    assert second["id"] == first["id"]
    assert second["details"]["added_optional"] == ["extra"]

    # Identical re-post is idempotent.
    third = await _create(client, auth_headers, changed)
    assert third["status"] == "unchanged"
    assert third["version"] == 2


@pytest.mark.asyncio
async def test_restore_onto_foreign_owner_of_value_fails_loudly(
    client: AsyncClient, auth_headers: dict
):
    """A restore-mode create (pre-assigned ID + version) whose value is
    already registered under a DIFFERENT ID must error — restoring onto a
    dirty target would silently re-parent the archive's versions."""
    live = await _create(client, auth_headers, _payload("RESTORE_TAKEN"))
    assert live["status"] == "created"

    restore_item = _payload(
        "RESTORE_TAKEN",
        template_id="019f0000-0000-7000-8000-000000000709",
        version=1,
        status="draft",
    )
    resp = await client.post(TEMPLATES, headers=auth_headers, json=[restore_item])
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert "clean target" in item["error"]
