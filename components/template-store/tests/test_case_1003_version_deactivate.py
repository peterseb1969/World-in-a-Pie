"""Deactivating one template version counts only the documents on that version.

Retiring an old version after migrating its documents is the platform's
lifecycle (PoNIF #2). The check used to count the template's active
documents across every version, so an unused old version could only be
retired with force=true — which also bypassed the check for a version that
still carries documents.
"""

import pytest


async def _template_with_two_versions(client, auth_headers, value: str) -> str:
    base = {"namespace": "wip", "value": value, "label": value}
    for fields in (
        [{"name": "a", "label": "A", "type": "string"}],
        [{"name": "a", "label": "A", "type": "string"}, {"name": "b", "label": "B", "type": "string"}],
    ):
        r = await client.post(
            "/api/template-store/templates", headers=auth_headers, json=[{**base, "fields": fields}],
        )
        assert r.status_code == 200 and r.json()["succeeded"] == 1, r.text
    return r.json()["results"][0]["id"]


async def _deactivate(client, auth_headers, template_id: str, version: int, force: bool = False) -> dict:
    r = await client.request(
        "DELETE", "/api/template-store/templates", headers=auth_headers,
        json=[{"id": template_id, "version": version, "force": force}],
    )
    assert r.status_code == 200
    return r.json()["results"][0]


@pytest.mark.asyncio
async def test_an_unused_old_version_deactivates_while_the_latest_has_documents(
    client, auth_headers, doc_store,
):
    tid = await _template_with_two_versions(client, auth_headers, "C1003_UNUSED_V1")
    doc_store.active_by_version = {1: 0, 2: 871}
    result = await _deactivate(client, auth_headers, tid, 1)
    assert result["status"] == "deleted", result
    assert {"template_version": "1", "status": "active"}.items() <= doc_store.requests[-1].items()


@pytest.mark.asyncio
async def test_a_version_with_documents_is_refused_with_its_own_count(client, auth_headers, doc_store):
    tid = await _template_with_two_versions(client, auth_headers, "C1003_USED_V1")
    doc_store.active_by_version = {1: 3, 2: 871}
    result = await _deactivate(client, auth_headers, tid, 1)
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_state"
    assert result["details"] == {"rule": "has_documents", "version": 1, "document_count": 3}
    assert "Version 1 has 3 active document(s)" in result["error"]


@pytest.mark.asyncio
async def test_force_still_deactivates_a_version_with_documents(client, auth_headers, doc_store):
    tid = await _template_with_two_versions(client, auth_headers, "C1003_FORCE")
    doc_store.active_by_version = {1: 3}
    assert (await _deactivate(client, auth_headers, tid, 1, force=True))["status"] == "deleted"


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_an_unknown_count_refuses_the_deactivation(client, auth_headers, doc_store, force):
    # Reversed (Peter, 2026-11-09): if Document Store cannot answer, no
    # deletion goes ahead, force included.
    tid = await _template_with_two_versions(client, auth_headers, f"C1003_UNKNOWN_{force}".upper())
    doc_store.unreachable = True
    result = await _deactivate(client, auth_headers, tid, 1, force=force)
    assert result["status"] == "error", result
    assert result["details"] == {"rule": "documents_unverifiable"}, result
