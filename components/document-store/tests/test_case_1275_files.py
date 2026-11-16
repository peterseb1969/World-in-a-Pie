"""A file's restriction enforced from what it names, not how it is spelled.

Provenance: CASE-1275 (eecee8d0 R1, R2, and the R5/R6 lows).

- A row stored before restrictions held canonical ids — or restored from an
  older archive — used to match no template at all, refusing the very template
  the entry named. Enforcement now resolves what is stored and writes the
  repaired list back.
- Re-uploading stored bytes returns that file; if it carries a different
  restriction from the one asked for, the upload says so instead of reporting
  success.
- The candidate what-if door no longer refuses restricted files for a
  placeholder template id: a definition of a brand-new template has no
  canonical id to compare against, and says the check was not made.
"""

from unittest.mock import patch

import pytest

from document_store.models.file import File
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

CANDIDATE = "/api/document-store/validation/validate-candidate"


def _person_id() -> str:
    from .conftest import SAMPLE_TEMPLATES

    return SAMPLE_TEMPLATES["PERSON"]["template_id"]


def _candidate(template_id: str) -> dict:
    return {
        "template_id": template_id, "value": "CANDIDATE_1275", "label": "C",
        "version": 1, "status": "active", "namespace": "wip", "identity_fields": [],
        "fields": [{"name": "f", "label": "f", "type": "file"}], "rules": [],
    }


async def _validate(client, headers, file_id: str, template_id: str | None = None) -> dict:
    with patch(
        "document_store.services.file_storage_client.is_file_storage_enabled", return_value=True
    ):
        resp = await client.post(CANDIDATE, headers=headers, json={
            "namespace": "wip",
            "template_definition": _candidate(template_id or _person_id()),
            "documents": [{"f": file_id}],
        })
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["validation"]


@pytest.mark.asyncio
async def test_a_row_that_still_holds_a_template_value_is_enforced_by_its_file(
    file_client, auth_headers  # noqa: F811
):
    legacy = await _insert_file(allowed_templates=["PERSON"])

    result = await _validate(file_client, auth_headers, legacy.file_id)

    assert [e["code"] for e in result["errors"]] == []
    stored = await File.find_one({"file_id": legacy.file_id})
    assert stored.allowed_templates == [_person_id()]


@pytest.mark.asyncio
async def test_a_value_that_names_no_template_matches_nothing(
    file_client, auth_headers  # noqa: F811
):
    """An entry the Registry cannot resolve stays as it is: it then names no
    template, so the file remains restricted instead of opening up."""
    restricted = await _insert_file(allowed_templates=["NO_SUCH_TEMPLATE"])

    result = await _validate(file_client, auth_headers, restricted.file_id)

    assert [e["code"] for e in result["errors"]] == ["invalid_file"]
    stored = await File.find_one({"file_id": restricted.file_id})
    assert stored.allowed_templates == ["NO_SUCH_TEMPLATE"]


@pytest.mark.asyncio
async def test_a_placeholder_candidate_reports_the_restriction_check_as_not_made(
    file_client, auth_headers  # noqa: F811
):
    restricted = await _insert_file(allowed_templates=[_person_id()])

    result = await _validate(file_client, auth_headers, restricted.file_id, template_id="candidate")

    assert [e["code"] for e in result["errors"]] == []
    assert any("allowed_templates" in w for w in result["warnings"]), result["warnings"]


@pytest.mark.asyncio
async def test_re_uploading_stored_bytes_with_another_restriction_is_refused(
    file_client, auth_headers  # noqa: F811
):
    from document_store.services import file_service as mod

    stored = await _insert_file(allowed_templates=["tpl-a"])

    # Not addressed, or addressed exactly as stored: the file comes back.
    await mod._refuse_unmet_restriction(stored, None)
    await mod._refuse_unmet_restriction(stored, ["tpl-a"])

    with pytest.raises(mod.FileServiceError) as refused:
        await mod._refuse_unmet_restriction(stored, ["tpl-b"])
    assert refused.value.code == "invalid_state"
    assert (refused.value.details or {}).get("rule") == "restriction_mismatch"
    assert stored.file_id in str(refused.value)


@pytest.mark.asyncio
async def test_an_entry_naming_several_templates_is_reported_as_ambiguous(auth_headers):  # noqa: F811
    """Not "name no template": the name is there, several times, and nothing
    picks one."""
    from unittest.mock import AsyncMock

    from document_store.services import file_service as mod
    from wip_auth.resolve import AmbiguousEntityError

    error = AmbiguousEntityError(
        "PERSON", "template", [{"entry_id": "tpl-1"}, {"entry_id": "tpl-2"}], 2,
    )

    async def fake_resolve(raw_ids, entity_type, namespace, *args, **kwargs):
        kwargs["ambiguous"]["PERSON"] = error
        return {}

    with (
        patch.object(mod, "resolve_entity_ids", new=AsyncMock(side_effect=fake_resolve)),
        pytest.raises(mod.FileServiceError) as refused,
    ):
        await mod.resolve_allowed_templates(["PERSON"], "wip")

    message = str(refused.value)
    assert "ambiguous" in message and "PERSON" in message
    assert "name no template" not in message


@pytest.mark.asyncio
async def test_the_repaired_list_is_written_from_the_registry_not_the_cache(
    file_client  # noqa: F811
):
    """The read may answer from the cache; the write-back may not. A cached
    answer that has gone stale (the name now belongs to another template)
    must not be pinned into the row."""
    from unittest.mock import AsyncMock

    import document_store.services.file_service as mod

    legacy = await _insert_file(allowed_templates=["PERSON"])
    calls: list[bool] = []

    async def fake_resolve(wanted, entity_type, namespace, *, bypass_cache=False, strict=True):
        calls.append(bypass_cache)
        return {"PERSON": "fresh-id" if bypass_cache else "stale-cached-id"}

    with patch.object(mod, "resolve_entity_ids", new=AsyncMock(side_effect=fake_resolve)):
        ids = await mod.stored_allowed_templates(legacy)

    assert calls == [False, True]
    assert ids == ["fresh-id"]
    stored = await File.find_one({"file_id": legacy.file_id})
    assert stored.allowed_templates == ["fresh-id"]


@pytest.mark.asyncio
async def test_a_row_already_holding_ids_is_not_resolved_again(file_client):  # noqa: F811
    from unittest.mock import AsyncMock

    import document_store.services.file_service as mod

    current = await _insert_file(allowed_templates=[_person_id()])

    async def fake_resolve(wanted, entity_type, namespace, *, bypass_cache=False, strict=True):
        return {e: e for e in wanted}

    resolver = AsyncMock(side_effect=fake_resolve)
    with patch.object(mod, "resolve_entity_ids", new=resolver):
        assert await mod.stored_allowed_templates(current) == [_person_id()]

    assert resolver.await_count == 1
