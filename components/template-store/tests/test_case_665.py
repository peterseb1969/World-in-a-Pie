"""Restore-mode creates must leave the template resolvable BY VALUE.

The restore-mode create branch (template_id + version both provided) used to
register the pre-assigned ID but skip the value auto-synonym, assuming the
archive's synonym replay would supply it. That replay runs after template
activation in the restore sequence, and activation must resolve value-form
references (edge types resolve source_templates / target_templates there) —
so activation could never succeed for an archive carrying an edge type: the
value had no Registry synonym yet. Restore-mode creates now register the
value auto-synonym like any create; repeat registrations (multi-version
archives, the archive's later synonym replay) are absorbed by the Registry's
idempotent already_exists path.

These tests run against the real in-process Registry (conftest mounts it), so
they exercise the exact value-resolution path that failed live.
"""

import pytest

TEMPLATES = "/api/template-store/templates"

SRC_ID = "019f0000-0000-7000-8000-0000000665aa"
TGT_ID = "019f0000-0000-7000-8000-0000000665bb"
EDGE_ID = "019f0000-0000-7000-8000-0000000665cc"
MULTI_ID = "019f0000-0000-7000-8000-0000000665dd"


def _restore_entity(value: str, template_id: str, version: int = 1) -> dict:
    """A create request the toolkit's restore mode sends: id + version."""
    return {
        "value": value,
        "label": value,
        "namespace": "test-ns",
        "template_id": template_id,
        "version": version,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
        ],
        "status": "draft",
        "created_by": "wip-toolkit-restore",
    }


def _restore_edge_type(source_value: str, target_value: str) -> dict:
    """The restore shape for an edge type: value-form endpoint lists, which
    activation must resolve through the Registry."""
    return {
        "value": "RESTORE_665_EDGE",
        "label": "Restore 665 edge",
        "namespace": "test-ns",
        "template_id": EDGE_ID,
        "version": 1,
        "usage": "relationship",
        "versioned": False,
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": [source_value],
        "target_templates": [target_value],
        "fields": [
            {
                "name": "source_ref",
                "label": "Source",
                "type": "reference",
                "reference_type": "document",
                "target_templates": [source_value],
                "mandatory": True,
            },
            {
                "name": "target_ref",
                "label": "Target",
                "type": "reference",
                "reference_type": "document",
                "target_templates": [target_value],
                "mandatory": True,
            },
        ],
        "status": "draft",
        "created_by": "wip-toolkit-restore",
    }


async def _create_ok(client, auth_headers, payload: dict) -> None:
    resp = await client.post(TEMPLATES, headers=auth_headers, json=[payload])
    assert resp.status_code == 200
    r = resp.json()["results"][0]
    assert r["status"] == "created", r


async def _activate_ok(client, auth_headers, template_id: str) -> None:
    act = await client.post(
        f"{TEMPLATES}/{template_id}/activate?namespace=test-ns",
        headers=auth_headers,
    )
    assert act.status_code == 200, act.text


@pytest.mark.asyncio
async def test_restored_edge_type_activates_before_synonym_replay(
    client, auth_headers
):
    """The live failure: the edge type's activation resolves its value-form
    source/target_templates through the Registry, before the archive's
    synonym replay has run — the restore-created entity templates must
    already be resolvable by value."""
    await _create_ok(client, auth_headers, _restore_entity("RESTORE_665_SRC", SRC_ID))
    await _create_ok(client, auth_headers, _restore_entity("RESTORE_665_TGT", TGT_ID))
    await _create_ok(
        client, auth_headers, _restore_edge_type("RESTORE_665_SRC", "RESTORE_665_TGT")
    )

    # Toolkit restore activates in archive order: entities first, then the
    # edge type. The edge-type activation is the call that 400'd live
    # ("No template found for identifier: <source value>").
    await _activate_ok(client, auth_headers, SRC_ID)
    await _activate_ok(client, auth_headers, TGT_ID)
    await _activate_ok(client, auth_headers, EDGE_ID)

    got = await client.get(
        f"{TEMPLATES}/{EDGE_ID}?namespace=test-ns", headers=auth_headers
    )
    assert got.status_code == 200
    assert got.json()["status"] == "active"


@pytest.mark.asyncio
async def test_restore_of_nonfirst_version_registers_value(client, auth_headers):
    """A latest-only archive restore-creates a template whose only version is
    N > 1. The normal path registers the value auto-synonym only for
    version 1 — restore mode must register it regardless of the version
    number, or the restored template is never resolvable by value.

    (The toolkit restore-POSTs only the FIRST archived version per template;
    later versions go through the normal PUT/new-version path, where the
    synonym already exists. A second restore-POST of the same ID is rejected
    by the Registry's clean-target contract — pinned in test_case_660.py.)"""
    await _create_ok(
        client, auth_headers, _restore_entity("RESTORE_665_MULTI", MULTI_ID, version=3)
    )

    # Resolve BY VALUE — the path that stays dead when the synonym is
    # version-gated instead of restore-aware.
    got = await client.get(
        f"{TEMPLATES}/RESTORE_665_MULTI?namespace=test-ns", headers=auth_headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["template_id"] == MULTI_ID
    assert got.json()["version"] == 3

    await _activate_ok(client, auth_headers, MULTI_ID)
