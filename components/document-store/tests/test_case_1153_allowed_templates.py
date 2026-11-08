"""A file's allowed_templates restricts which templates' documents may use it (CASE-1153).

The list was stored and echoed back, but validation never passed the
document's template to the check, so it never refused anything. Now:

- upload and metadata PATCH resolve every entry through the Registry (a
  value, ns:VALUE, an id or a synonym) to the template's canonical id, store
  the ids, and refuse an entry that resolves to nothing; an empty list clears
  the restriction;
- validation refuses a file whose list does not name the document's
  template.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from document_store.models.file import File
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

CANDIDATE = "/api/document-store/validation/validate-candidate"
FILES = "/api/document-store/files"


def _candidate() -> dict:
    return {
        "template_id": "candidate", "value": "CANDIDATE_1153", "label": "C",
        "version": 1, "status": "active", "namespace": "wip", "identity_fields": [],
        "fields": [{"name": "f", "label": "f", "type": "file"}], "rules": [],
    }


async def _file_errors(client, headers, file_id: str) -> list[dict]:
    # Validation skips file checks when storage is off; the file_client
    # fixture enables it only for the files API.
    with patch("document_store.services.file_storage_client.is_file_storage_enabled", return_value=True):
        resp = await client.post(CANDIDATE, headers=headers, json={
            "namespace": "wip", "template_definition": _candidate(), "documents": [{"f": file_id}],
        })
    assert resp.status_code == 200, resp.text
    return [e for e in resp.json()["results"][0]["validation"]["errors"] if e["code"] == "invalid_file"]


# ── Validation enforces the list ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_file_restricted_to_another_template_is_refused(file_client, auth_headers):  # noqa: F811
    restricted = await _insert_file(allowed_templates=["some-other-template-id"])

    errors = await _file_errors(file_client, auth_headers, restricted.file_id)

    assert len(errors) == 1, errors
    assert "candidate" in errors[0]["message"]


@pytest.mark.asyncio
async def test_a_file_restricted_to_the_documents_template_is_accepted(file_client, auth_headers):  # noqa: F811
    allowed = await _insert_file(allowed_templates=["candidate"])

    assert await _file_errors(file_client, auth_headers, allowed.file_id) == []


@pytest.mark.asyncio
async def test_an_unrestricted_file_is_accepted(file_client, auth_headers):  # noqa: F811
    open_file = await _insert_file(allowed_templates=None)

    assert await _file_errors(file_client, auth_headers, open_file.file_id) == []


# ── Writes store resolved ids ────────────────────────────────────────────

_KNOWN = {"REPORT": "tpl-report-id", "wip:INVOICE": "tpl-invoice-id", "tpl-report-id": "tpl-report-id"}


async def _fake_resolve(raw_ids, entity_type, namespace, *args, **kwargs):
    assert entity_type == "template" and kwargs.get("bypass_cache") is True
    return {r: _KNOWN[r] for r in raw_ids if r in _KNOWN}


@pytest.mark.asyncio
async def test_patch_stores_canonical_ids_and_refuses_an_unknown_entry(file_client, auth_headers):  # noqa: F811
    f = await _insert_file()
    with patch("document_store.services.file_service.resolve_entity_ids", side_effect=_fake_resolve):
        ok = await file_client.patch(FILES, headers=auth_headers, json=[
            {"file_id": f.file_id, "allowed_templates": ["REPORT", "wip:INVOICE", "tpl-report-id"]},
        ])
        bad = await file_client.patch(FILES, headers=auth_headers, json=[
            {"file_id": f.file_id, "allowed_templates": ["REPORT", "NO_SUCH"]},
        ])

    assert ok.json()["results"][0]["status"] == "updated", ok.text
    stored = await File.find_one({"file_id": f.file_id})
    assert stored.allowed_templates == ["tpl-report-id", "tpl-invoice-id"]
    item = bad.json()["results"][0]
    assert item["status"] == "error" and "NO_SUCH" in item["error"], item
    assert item["error_code"] == "validation_failed"
    assert (await File.find_one({"file_id": f.file_id})).allowed_templates == ["tpl-report-id", "tpl-invoice-id"]


@pytest.mark.asyncio
async def test_patch_with_an_empty_list_clears_the_restriction(file_client, auth_headers):  # noqa: F811
    f = await _insert_file(allowed_templates=["tpl-report-id"])

    resp = await file_client.patch(FILES, headers=auth_headers, json=[
        {"file_id": f.file_id, "allowed_templates": []},
    ])

    assert resp.json()["results"][0]["status"] == "updated", resp.text
    assert (await File.find_one({"file_id": f.file_id})).allowed_templates is None


@pytest.mark.asyncio
async def test_upload_stores_canonical_ids_and_refuses_an_unknown_entry(file_client, auth_headers):  # noqa: F811
    registry = MagicMock(status_code=200)
    registry.json.return_value = {"results": [{"status": "created", "registry_id": "FILE-1153"}]}
    with patch("document_store.services.file_service.httpx.AsyncClient") as http, \
            patch("document_store.services.file_service.resolve_entity_ids", side_effect=_fake_resolve):
        client = AsyncMock()
        client.post = AsyncMock(return_value=registry)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        http.return_value = client

        ok = await file_client.post(FILES, headers=auth_headers,
                                    files={"file": ("a.txt", b"one", "text/plain")},
                                    data={"namespace": "wip", "allowed_templates": "REPORT, wip:INVOICE"})
        bad = await file_client.post(FILES, headers=auth_headers,
                                     files={"file": ("b.txt", b"two", "text/plain")},
                                     data={"namespace": "wip", "allowed_templates": "NO_SUCH"})

    assert ok.status_code == 200, ok.text
    assert ok.json()["allowed_templates"] == ["tpl-report-id", "tpl-invoice-id"]
    assert bad.status_code == 400 and "NO_SUCH" in bad.json()["detail"], bad.text


# ── Files stored before ids are converted ────────────────────────────────


@pytest.mark.asyncio
async def test_stored_values_are_converted_to_ids_and_unknown_ones_kept(file_client):  # noqa: F811
    from document_store.services.allowed_templates_ids import convert_allowed_templates_to_ids

    legacy = await _insert_file(allowed_templates=["REPORT", "NO_SUCH"])
    already = await _insert_file(allowed_templates=["tpl-report-id"])
    empty = await _insert_file(allowed_templates=[])
    with patch("document_store.services.allowed_templates_ids.resolve_entity_ids", side_effect=_fake_resolve):
        summary = await convert_allowed_templates_to_ids()
        again = await convert_allowed_templates_to_ids()

    assert (await File.find_one({"file_id": legacy.file_id})).allowed_templates == ["tpl-report-id", "NO_SUCH"]
    assert (await File.find_one({"file_id": already.file_id})).allowed_templates == ["tpl-report-id"]
    assert (await File.find_one({"file_id": empty.file_id})).allowed_templates is None
    assert summary["converted"] == 2 and summary["unresolved"] == 1
    assert again["converted"] == 0


# ── Restores that re-key templates re-key the restriction ────────────────


def test_a_file_row_follows_its_templates_new_ids():
    from wip_archive.remap import IDRemapper

    from document_store.services.remap_restore import remap_file

    remapper = IDRemapper()
    remapper.add_template_mapping("old-tpl", "new-tpl")
    row = {"file_id": "F", "allowed_templates": ["old-tpl", "outside-tpl"]}

    assert remap_file(row, remapper)["allowed_templates"] == ["new-tpl", "outside-tpl"]
    assert remap_file({"file_id": "G", "allowed_templates": None}, remapper)["allowed_templates"] is None


def test_the_merge_rewrites_a_file_rows_restriction():
    from wip_archive.remap import IDRemapper

    from document_store.services.backup_engine import DirectRestoreEngine

    remapper = IDRemapper()
    remapper.add_template_mapping("old-tpl", "new-tpl")
    engine = DirectRestoreEngine(MagicMock(), None, lambda _: None)

    rows, changed = engine._rewrite_entities(
        "files", [{"file_id": "F", "allowed_templates": ["old-tpl"]}], remapper,
    )

    assert changed == 1 and rows[0]["allowed_templates"] == ["new-tpl"]
