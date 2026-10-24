"""Field names that reporting cannot quote are refused at authoring time.

Field names become double-quoted column names in the reporting database. A
double quote would close that quoting and a NUL truncates the name, so the
write seam rejects both, as a per-item error. Stored templates are not
re-checked on read (the guard lives at the write seam, not on the
persistence model), and every other character stays allowed.
"""

import pytest
from httpx import AsyncClient

from template_store.models.field import FieldDefinition
from template_store.services.template_service import TemplateService

TEMPLATES = "/api/template-store/templates"


def _field(name: str) -> FieldDefinition:
    return FieldDefinition.model_validate({"name": name, "label": "F", "type": "string"})


@pytest.mark.parametrize("name", ['bad"field', "bad\x00field"])
def test_write_seam_rejects_unquotable_name(name):
    with pytest.raises(ValueError, match="double quote or NUL"):
        TemplateService.validate_fields_for_write([_field(name)])


@pytest.mark.parametrize("name", ["first-name", "first name", "Ünïcode", "a'b"])
def test_write_seam_keeps_other_names(name):
    TemplateService.validate_fields_for_write([_field(name)])  # no raise


def test_persistence_model_stays_permissive():
    # A stored template with such a name must still hydrate.
    FieldDefinition.model_validate({"name": 'bad"field', "label": "F", "type": "string"})


def _template(value: str, *names: str) -> dict:
    return {
        "namespace": "wip",
        "value": value,
        "label": value,
        "fields": [{"name": n, "label": n, "type": "string"} for n in names],
        "identity_fields": [names[0]],
    }


@pytest.mark.asyncio
async def test_create_rejects_per_item(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        TEMPLATES, headers=auth_headers, json=[_template("QUOTE_NAME_TPL", "id", 'bad"field')]
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "error", item
    assert "double quote or NUL" in item["error"]


@pytest.mark.asyncio
async def test_new_version_rejects_per_item(client: AsyncClient, auth_headers: dict):
    ok = await client.post(
        TEMPLATES, headers=auth_headers, json=[_template("QUOTE_NAME_V_TPL", "id")]
    )
    assert ok.json()["results"][0]["status"] == "created", ok.text

    resp = await client.post(
        TEMPLATES, headers=auth_headers, json=[_template("QUOTE_NAME_V_TPL", "id", 'bad"field')]
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "error", item
    assert "double quote or NUL" in item["error"]
