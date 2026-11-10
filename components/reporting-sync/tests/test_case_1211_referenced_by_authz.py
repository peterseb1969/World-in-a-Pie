"""Who may see what /entity/.../referenced-by and /references say about a target.

/referenced-by reads its target with the service key and returns the
target's value and label, so the caller must be able to read the namespace
the target lives in, exactly as /references checks the entity it describes.
A target in an unreadable namespace and a missing target answer the same
404; a target whose namespace could not be learned answers 503.

/references hides the label and value of every target it cannot place in
a namespace the caller reads, whatever the target's Registry status: a
merged-away or deactivated entry still lives somewhere.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, state
from reporting_sync.search_service import ReferencedByResponse
from wip_auth import clear_permission_cache

ADMIN = "rs_test_admin_key"
READER = "rs_reader_key"  # read on ns-a
MULTI = "rs_multi_key"  # read on ns-a and ns-c
P = "/api/reporting-sync"


@pytest.fixture(autouse=True)
def _fresh_permission_cache():
    clear_permission_cache()
    yield
    clear_permission_cache()


class _Target:
    """A referenced-by answer about a target in a chosen namespace."""

    def __init__(self, owner, missing=False):
        self.owner, self.missing = owner, missing

    async def get_referenced_by(self, entity_type, entity_id, limit, namespace, within=None):
        if self.missing:
            r = ReferencedByResponse(
                entity_type=entity_type, entity_id=entity_id,
                error=f"Term not found: {entity_id}",
            )
            r._target_missing = True
            return r
        r = ReferencedByResponse(
            entity_type=entity_type, entity_id=entity_id,
            entity_value="SECRET", entity_label="Secret term",
        )
        if self.owner != "unknown":
            r._target_namespace = self.owner
        return r


async def _get(key, path):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": key}) as c:
        return await c.get(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,key,expected",
    [
        (_Target("ns-a"), READER, 200),
        # The leak: no namespace param, so the referrer scope is the
        # reader's own namespace, but the target lives in ns-c.
        (_Target("ns-c"), READER, 404),
        (_Target(None, missing=True), READER, 404),  # same answer as unreadable
        (_Target("unknown"), READER, 503),  # cannot tell where it lives
        (_Target("ns-c"), MULTI, 200),
        (_Target("ns-c"), ADMIN, 200),
        (_Target(None, missing=True), ADMIN, 200),  # unrestricted: error body
    ],
)
async def test_referenced_by_checks_the_targets_namespace(monkeypatch, target, key, expected):
    monkeypatch.setattr(state, "search_service", target)
    path = f"{P}/entity/term/term-c/referenced-by"
    if key == MULTI:
        path += "?namespace=ns-a"
    r = await _get(key, path)
    assert r.status_code == expected, r.text
    if expected == 200 and not getattr(target, "missing", False):
        assert r.json()["entity_label"] == "Secret term"
    if expected != 200:
        assert "SECRET" not in r.text


def test_the_targets_namespace_is_never_served():
    r = ReferencedByResponse(entity_type="term", entity_id="t")
    r._target_namespace = "ns-c"
    assert "ns-c" not in r.model_dump_json()
    assert "_target" not in str(ReferencedByResponse.model_json_schema())


# ---------------------------------------------------------------------------
# /references: a target the Registry places nowhere is hidden
# ---------------------------------------------------------------------------


class _EntityWithRefs:
    async def get_entity_references(self, entity_type, entity_id):
        from reporting_sync.search_service import (
            EntityDetails,
            EntityReference,
            EntityReferencesResponse,
        )

        return EntityReferencesResponse(entity=EntityDetails(
            entity_type=entity_type, entity_id=entity_id, namespace="ns-a",
            references=[
                EntityReference(ref_type="template", ref_id="tpl-own", ref_value="OWN",
                                ref_label="Own", status="valid"),
                EntityReference(ref_type="term", ref_id="term-x", ref_value="SECRET",
                                ref_label="Secret term", status="valid"),
            ],
            valid_refs=2,
        ))


@pytest.mark.asyncio
async def test_an_unplaceable_target_is_hidden(monkeypatch):
    from wip_auth.registry_client import RegistryClientBase

    async def placed(self, ids):
        return {"tpl-own": "ns-a"}  # term-x: no Registry entry anywhere

    monkeypatch.setattr(state, "search_service", _EntityWithRefs())
    monkeypatch.setattr(RegistryClientBase, "place_entries", placed)
    r = await _get(READER, f"{P}/entity/document/doc-1/references")
    assert r.status_code == 200, r.text
    entity = r.json()["entity"]
    own, other = entity["references"]
    assert own["ref_label"] == "Own"
    assert other["ref_value"] is None and other["ref_label"] is None
    assert other["status"] == "broken"
    assert (entity["valid_refs"], entity["broken_refs"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_registry_that_cannot_place_targets_refuses(monkeypatch):
    from wip_auth.registry_client import RegistryClientBase, RegistryError

    async def outage(self, ids):
        raise RegistryError("down")

    monkeypatch.setattr(state, "search_service", _EntityWithRefs())
    monkeypatch.setattr(RegistryClientBase, "place_entries", outage)
    r = await _get(READER, f"{P}/entity/document/doc-1/references")
    assert r.status_code == 503
    assert "SECRET" not in r.text
