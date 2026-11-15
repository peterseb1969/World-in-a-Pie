"""Resolution names the entity type it wants (CASE-1088).

The resolve payload sent a UUID as a bare entry_id, so the Registry's
canonical-id path could not tell a template's id from a terminology's. Every
item now carries the Registry's (plural) entity_type; a type the Registry does
not know is left out rather than sent and refused.
"""

import json

import httpx
import pytest

from wip_auth.resolve import (
    _build_resolve_payload,
    clear_resolution_cache,
    resolve_entity_id,
)

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
UUID = "019a0000-0000-7000-8000-000000000001"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


@pytest.mark.parametrize(
    ("singular", "plural"),
    [("terminology", "terminologies"), ("term", "terms"), ("template", "templates"),
     ("document", "documents"), ("file", "files")],
)
def test_every_item_carries_the_registry_type(singular, plural):
    assert _build_resolve_payload(UUID, singular, "paint")["entity_type"] == plural
    if singular != "term":
        assert _build_resolve_payload("COLOR", singular, "paint")["entity_type"] == plural


def test_an_already_plural_type_is_kept():
    assert _build_resolve_payload(UUID, "templates", "paint")["entity_type"] == "templates"


def test_a_type_the_registry_does_not_know_is_left_out():
    assert "entity_type" not in _build_resolve_payload(UUID, "widget", "paint")


@pytest.mark.asyncio
async def test_a_resolve_request_names_the_type(httpx_mock, monkeypatch):
    monkeypatch.setenv("REGISTRY_API_KEY", "test-key")  # not left to another test
    sent: list[dict] = []

    def resolve(request: httpx.Request) -> httpx.Response:
        sent.extend(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"status": "found", "entry_id": UUID}]})

    httpx_mock.add_callback(resolve, url=RESOLVE_URL)
    assert await resolve_entity_id(UUID, "terminology", "paint") == UUID
    assert sent == [{"entry_id": UUID, "entity_type": "terminologies"}]


def test_entity_types_match_the_registry():
    """The resolve layer's copy of the Registry's entity types (plural) is the
    Registry's own list, as its served schema states it (the id_config keys
    of a namespace are VALID_ENTITY_TYPES): a type added there and not here
    would resolve untyped."""
    from pathlib import Path

    from wip_auth.resolve import _ENTITY_TYPE_PLURAL

    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "schemas" / "registry.json").read_text()
    )
    id_config = schema["components"]["schemas"]["NamespaceCreate"]["properties"]["id_config"]
    registry_types = next(
        part["propertyNames"]["enum"] for part in id_config["anyOf"] if "propertyNames" in part
    )
    assert set(_ENTITY_TYPE_PLURAL.values()) == set(registry_types)


@pytest.mark.asyncio
async def test_a_lookup_for_a_type_the_registry_has_no_name_for_is_refused():
    from wip_auth.resolve import _post_lookup_by_id

    with pytest.raises(ValueError, match="no Registry entity type"):
        await _post_lookup_by_id("COLOR", "paint", "widget")


@pytest.mark.asyncio
async def test_the_field_form_term_lookup_names_the_type(httpx_mock, monkeypatch):
    from wip_auth.resolve import resolve_term_by_fields

    monkeypatch.setenv("REGISTRY_API_KEY", "test-key")

    sent: list[dict] = []

    def resolve(request: httpx.Request) -> httpx.Response:
        sent.extend(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"status": "found", "entry_id": UUID}]})

    httpx_mock.add_callback(resolve, url=RESOLVE_URL)
    assert await resolve_term_by_fields("RED", "paint:COLOR", "paint") == UUID
    assert sent and all(item.get("entity_type") == "terms" for item in sent)
