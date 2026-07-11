"""Pre-assigned template IDs must be registered with the Registry.

The restore-mode create branch (template_id + version both provided) used to
insert the template into MongoDB without registering the ID in the Registry.
Every later resolution of that ID — activation first among them — then 404'd:
the template existed but was unusable, and the toolkit's restore mode died at
its activation step. The create path now registers the pre-assigned ID exactly
as it registers generated ones. (The value auto-synonym is also registered on
restore creates — activation needs value resolution before the archive's
synonym replay runs; see test_case_665.py.)

These tests run against the real in-process Registry (conftest mounts it), so
they exercise the exact resolution path that failed live.
"""

import pytest

TEMPLATES = "/api/template-store/templates"

PREASSIGNED_ID = "019f0000-0000-7000-8000-00000000c660"


def _restore_shape(value: str = "RESTORE_660") -> dict:
    """A create request the toolkit's restore mode sends: id + version."""
    return {
        "value": value,
        "label": "Restore 660",
        "namespace": "test-ns",
        "template_id": PREASSIGNED_ID,
        "version": 1,
        "identity_fields": ["code"],
        "fields": [
            {"name": "code", "label": "Code", "type": "string", "mandatory": True},
        ],
        "status": "draft",
        "created_by": "wip-toolkit-restore",
    }


@pytest.mark.asyncio
async def test_preassigned_id_create_then_activate(client, auth_headers):
    """The live failure: create-with-id+version succeeded, activate 404'd."""
    resp = await client.post(TEMPLATES, headers=auth_headers, json=[_restore_shape()])
    assert resp.status_code == 200
    r = resp.json()["results"][0]
    assert r["status"] == "created", r
    assert r["id"] == PREASSIGNED_ID

    # Activation resolves the template_id through the Registry — this is the
    # call that 404'd ("Could not resolve template_id") before the fix.
    act = await client.post(
        f"{TEMPLATES}/{PREASSIGNED_ID}/activate?namespace=test-ns",
        headers=auth_headers,
    )
    assert act.status_code == 200, act.text

    got = await client.get(
        f"{TEMPLATES}/{PREASSIGNED_ID}?namespace=test-ns", headers=auth_headers
    )
    assert got.status_code == 200
    assert got.json()["status"] == "active"


@pytest.mark.asyncio
async def test_preassigned_id_double_restore_errors_cleanly(client, auth_headers):
    """Re-registering an existing entry_id must fail per-item, not silently
    duplicate — the Registry's clean-target contract for restores."""
    first = await client.post(TEMPLATES, headers=auth_headers, json=[_restore_shape("RESTORE_660B")])
    assert first.json()["results"][0]["status"] == "created"

    again = await client.post(TEMPLATES, headers=auth_headers, json=[_restore_shape("RESTORE_660B")])
    assert again.status_code == 200
    r = again.json()["results"][0]
    assert r["status"] == "error"
    assert "already exists" in (r.get("error") or "")
