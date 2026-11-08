"""Registry lows: export existence, activation failures, status filters,
the auth flag on /health, and the ID format check.

- /entries/export answers `not_found` for an entry in a namespace the caller
  cannot read, exactly like a missing id (as /lookup/by-id does); `forbidden`
  stays for a namespace it reads but does not administer, so an export key
  short of admin still fails loudly.
- A database failure inside activation is an error on the items it concerns,
  never a whole-request 500, and never stamps items the classify read already
  decided.
- browse and search refuse an unknown `status` instead of answering an empty
  page.
- /health's `auth_enabled` reflects the mode the auth layer enforces.
- IdFormatValidator accepts what the generator mints and nothing looser.
"""

import uuid

import pytest
from httpx import AsyncClient

from registry.api import entries as entries_api
from registry.models.entry import RegistryEntry
from registry.models.id_algorithm import IdAlgorithmConfig, IdFormatValidator, IdGenerator
from tests.test_reservation_lifecycle import NAMESPACE, _provision

BASE = "/api/registry"


async def _mint_key(client: AsyncClient, auth_headers: dict, namespace: str) -> dict:
    resp = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": f"case1132-{uuid.uuid4().hex[:8]}", "groups": [], "namespaces": [namespace],
    })
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


async def _register(client: AsyncClient, auth_headers: dict, namespace: str, value: str) -> str:
    resp = await client.post(f"{BASE}/entries/register", headers=auth_headers, json=[{
        "namespace": namespace, "entity_type": "documents", "composite_key": {"value": value},
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["registry_id"]


# ---------------------------------------------------------------- export


@pytest.mark.asyncio
async def test_export_confirms_no_entry_in_a_namespace_the_caller_cannot_read(client, auth_headers):
    tag = uuid.uuid4().hex[:8]
    own = await _register(client, auth_headers, "vendor1", f"own-{tag}")
    foreign = await _register(client, auth_headers, "vendor2", f"foreign-{tag}")
    reader = await _mint_key(client, auth_headers, "vendor1")  # read, not admin

    resp = await client.post(f"{BASE}/entries/export", headers=reader, json={
        "entry_ids": [own, foreign, f"missing-{tag}"],
    })
    assert resp.status_code == 200, resp.text
    statuses = [r["status"] for r in resp.json()["results"]]
    # Readable but not administered: loud. Unreadable and missing: the same.
    assert statuses == ["forbidden", "not_found", "not_found"]
    assert resp.json()["results"][1].get("entry") is None


# ------------------------------------------------------------ activation


@pytest.mark.asyncio
async def test_a_failed_claim_insert_fails_the_keyed_items_not_the_request(
    client, auth_headers, monkeypatch
):
    (eid,) = await _provision(client, auth_headers, defer_claim=True)

    async def broken(*_a, **_k):
        raise RuntimeError("claims collection unavailable")

    monkeypatch.setattr(entries_api, "claim_entries_keys_pending", broken)
    resp = await client.post(f"{BASE}/entries/activate", headers=auth_headers, json=[
        {"entry_id": eid, "composite_key": {"ns": NAMESPACE, "value": f"K-{eid}"}},
        {"entry_id": f"missing-{eid}", "composite_key": {"ns": NAMESPACE, "value": "x"}},
    ])
    assert resp.status_code == 200, resp.text
    first, second = resp.json()["results"]
    assert first["status"] == "error" and "claims collection unavailable" in first["error"]
    assert second["status"] == "not_found"
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    assert entry.status == "reserved"


@pytest.mark.asyncio
async def test_a_failed_keyless_flip_fails_only_the_reserved_items(client, auth_headers, monkeypatch):
    (reserved,) = await _provision(client, auth_headers)
    (already,) = await _provision(client, auth_headers)
    ok = await client.post(f"{BASE}/entries/activate", headers=auth_headers,
                           json=[{"entry_id": already}])
    assert ok.json()["activated"] == 1

    original = RegistryEntry.get_motor_collection()

    class FailingUpdates:
        def __getattr__(self, name):
            return getattr(original, name)

        async def update_many(self, *_a, **_k):
            raise RuntimeError("write unavailable")

    monkeypatch.setattr(RegistryEntry, "get_motor_collection", classmethod(lambda cls: FailingUpdates()))
    resp = await client.post(f"{BASE}/entries/activate", headers=auth_headers, json=[
        {"entry_id": reserved}, {"entry_id": already}, {"entry_id": f"missing-{reserved}"},
    ])
    assert resp.status_code == 200, resp.text
    statuses = [r["status"] for r in resp.json()["results"]]
    assert statuses == ["error", "already_active", "not_found"]


# ------------------------------------------------------- status filters


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"{BASE}/entries", f"{BASE}/entries/search?q=x"])
async def test_an_unknown_status_filter_is_refused(client, auth_headers, path):
    sep = "&" if "?" in path else "?"
    bad = await client.get(f"{path}{sep}status=acive", headers=auth_headers)
    assert bad.status_code == 422, bad.text
    good = await client.get(f"{path}{sep}status=active", headers=auth_headers)
    assert good.status_code == 200, good.text


# ------------------------------------------------------------------ health


@pytest.mark.asyncio
async def test_health_reports_the_enforced_auth_mode(client, monkeypatch):
    resp = await client.get("/health")
    assert resp.status_code == 200 and resp.json()["auth_enabled"] is True

    from registry import main

    class NoAuth:
        mode = "none"

    monkeypatch.setattr(main, "get_auth_config", lambda: NoAuth())
    assert (await client.get("/health")).json()["auth_enabled"] is False


# ------------------------------------------------------------ ID format


def _cfg(**kw) -> IdAlgorithmConfig:
    return IdAlgorithmConfig(**kw)


def test_uuids_must_be_canonical_but_any_version_passes():
    v7 = IdGenerator.generate_uuid7()
    v4 = str(uuid.uuid4())
    for algo in ("uuid7", "uuid4"):
        cfg = _cfg(algorithm=algo)
        assert IdFormatValidator.validate(v7, cfg)
        assert IdFormatValidator.validate(v4, cfg)
        for spelling in (v7.upper(), v7.replace("-", ""), "{" + v7 + "}", "urn:uuid:" + v7):
            assert not IdFormatValidator.validate(spelling, cfg), spelling


def test_prefixed_accepts_every_id_the_generator_mints():
    cfg = _cfg(algorithm="prefixed", prefix="T-", pad=3)
    for seq in (1, 999, 1000, 123456):  # past the pad width the ID just grows
        assert IdFormatValidator.validate(IdGenerator.generate_prefixed("T-", seq, 3), cfg)
    assert IdFormatValidator.validate("T-1", cfg)  # minted under an earlier, narrower pad
    for bad in ("T-", "T-12a", "T-12\n", "X-001", "T--001"):
        assert not IdFormatValidator.validate(bad, cfg), bad
    pad0 = _cfg(algorithm="prefixed", prefix="T-", pad=0)
    assert IdFormatValidator.validate(IdGenerator.generate_prefixed("T-", 7, 0), pad0)
    assert not IdFormatValidator.validate("T-", pad0)


def test_pattern_and_nanoid_must_match_the_whole_id():
    pattern = _cfg(algorithm="pattern", pattern=r"WIP-\d+")
    assert IdFormatValidator.validate("WIP-123", pattern)
    assert not IdFormatValidator.validate("WIP-123xyz", pattern)
    nano = _cfg(algorithm="nanoid", length=21)
    minted = IdGenerator.generate_nanoid(21)
    assert IdFormatValidator.validate(minted, nano)
    assert not IdFormatValidator.validate(minted[:-1] + "\n", nano)
    assert not IdFormatValidator.validate(minted + "\n", nano)
