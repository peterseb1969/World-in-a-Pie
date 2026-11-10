"""The low findings of the wip-auth review that still held on develop.

Provenance: CASE-1193 (inspect-yac area 3, wip-auth). Each test names the
finding it pins. All of them failed on the code before the fix, except the
watcher-baseline and reconcile-pass tests, whose new parameters (``applied``,
``active_among``) did not exist there.
"""

from __future__ import annotations

import logging
from importlib.metadata import version as package_version

import pytest
from fastapi import FastAPI
from starlette.requests import Request

import wip_auth
from wip_auth import resolve
from wip_auth.key_file_watch import ConfigFileWatcher, content_hash
from wip_auth.middleware import public_path_set
from wip_auth.models import APIKeyRecord
from wip_auth.openapi import declare_api_key_security
from wip_auth.providers import api_key as api_key_module
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key
from wip_auth.providers.trusted_header import TrustedHeaderProvider
from wip_auth.resolve import (
    EntityNotFoundError,
    TermNamespaceRequiredError,
    clear_resolution_cache,
    resolve_entity_id,
    resolve_entity_ids,
    resolve_term_by_fields,
)
from wip_auth.synonym_lifecycle import reconcile_synonym_lifecycle

UUID = "01903f5a-7b3c-7d4e-8f9a-0b1c2d3e4f5a"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


# --- 1-providers R2: a key removed while bcrypt ran is not cached as valid --


def test_a_key_removed_during_its_check_is_not_cached(monkeypatch):
    removed = APIKeyRecord(name="old", key_hash=hash_api_key("old-key"))
    provider = APIKeyProvider([removed])
    real_verify = api_key_module.verify_api_key
    reloaded: list[int] = []

    def verify_while_a_reload_lands(plaintext, key_hash, salt):
        if not reloaded:
            reloaded.append(1)
            provider.reload_config_keys([])  # the key file dropped the key, mid-check
        return real_verify(plaintext, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", verify_while_a_reload_lands)
    record, _message = provider._validate_key("old-key")
    assert record is None  # re-checked against the keys as they are now
    record, _message = provider._validate_key("old-key")
    assert record is None  # and nothing cached it


# --- 1-providers R3: a change made while the state was built is applied -----


def test_a_change_between_the_read_and_the_watcher_start_is_applied(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text("A")
    applied_before_read = content_hash(path)
    path.write_text("B")  # changed while the service was building its state
    calls: list[int] = []
    watcher = ConfigFileWatcher(
        path, lambda: calls.append(1), what="policy", applied=applied_before_read,
    )
    assert watcher.check_once() is True
    assert calls == [1]


# --- 1-providers R9: a legacy hash arriving after construction is reported --


def test_a_synced_legacy_hash_is_warned_about_once(caplog):
    provider = APIKeyProvider([])
    legacy = APIKeyRecord(name="legacy", key_hash="a" * 64)
    with caplog.at_level(logging.WARNING, logger="wip_auth.providers.api_key"):
        provider.replace_runtime_keys([legacy])
        provider.replace_runtime_keys([legacy])  # the next sync poll
    warnings = [r for r in caplog.records if "legacy SHA-256" in r.getMessage()]
    assert len(warnings) == 1


# --- 1-providers R10: an outage is not logged as a spoofing attempt ---------


@pytest.mark.asyncio
async def test_an_unverifiable_proxy_key_is_not_called_spoofing(caplog):
    proxy = APIKeyRecord(name="gateway", key_hash=hash_api_key("gw"), groups=["wip-services"])
    provider = APIKeyProvider([])
    provider.add_key(proxy)
    provider.expire_runtime_keys()  # the Registry has not confirmed the runtime keys
    trusted = TrustedHeaderProvider(keys=[proxy], key_provider=provider)
    request = Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"x-wip-user", b"someone@wip.local"), (b"x-api-key", b"gw")],
    })
    with caplog.at_level(logging.WARNING, logger="wip_auth.trusted_header"):
        assert await trusted.authenticate(request) is None
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "spoofing" not in messages
    assert "cannot be verified" in messages


# --- 3-resolution R3: a widened lookup does not answer an active-only one ---


@pytest.mark.asyncio
async def test_the_status_set_is_part_of_the_cache_key(monkeypatch):
    asked: list[dict] = []

    async def post_resolve(payload, identifier, entity_type, namespace=None):
        asked.append(payload)
        return "reserved-id" if payload.get("include_statuses") else "active-id"

    monkeypatch.setattr(resolve, "_post_resolve", post_resolve)
    widened = await resolve_entity_id("X", "template", "ns", ["reserved", "active"])
    plain = await resolve_entity_id("X", "template", "ns")
    assert (widened, plain) == ("reserved-id", "active-id")
    assert len(asked) == 2


# --- 3-resolution R4: the field-form cache key is injective ------------------


@pytest.mark.asyncio
async def test_colon_carrying_terminology_and_value_do_not_share_a_cache_entry(monkeypatch):
    async def resolve_at(namespace, terminology_value, value):
        return f"{terminology_value}|{value}"

    monkeypatch.setattr(resolve, "_resolve_term_at", resolve_at)
    first = await resolve_term_by_fields("C", "A:B", "ns")  # literal terminology "A:B"
    second = await resolve_term_by_fields("B:C", "A", "ns")
    assert first != second


# --- 3-resolution R5: a UUID that names no terminology is "not found" -------


@pytest.mark.asyncio
async def test_an_unknown_terminology_uuid_is_not_found_not_namespace_required(monkeypatch):
    async def no_terminology(entry_id):
        return None

    monkeypatch.setattr(resolve, "terminology_coordinates_by_id", no_terminology)
    with pytest.raises(EntityNotFoundError) as caught:
        await resolve_term_by_fields("v", UUID, "ns")
    assert not isinstance(caught.value, TermNamespaceRequiredError)


# --- 3-resolution R6: strict batch resolution takes the value fallback ------


@pytest.mark.asyncio
async def test_strict_batch_resolution_finds_what_single_resolution_finds(monkeypatch):
    async def chunk(registry_url, api_key, ids, entity_type, namespace, include_statuses):
        return [{"status": "not_found"} for _ in ids]

    async def by_id(value, namespace, entity_type):
        return "doc-id" if value == "Invoice 7" else None

    monkeypatch.setattr(resolve, "_post_resolve_chunk", chunk)
    monkeypatch.setattr(resolve, "_post_lookup_by_id", by_id)
    assert await resolve_entity_ids(["Invoice 7"], "document", "ns") == {"Invoice 7": "doc-id"}
    with pytest.raises(EntityNotFoundError):
        await resolve_entity_ids(["Nothing"], "document", "ns")


# --- 5-helpers R7: public routes declare no credential -----------------------


def test_public_routes_declare_no_security():
    app = FastAPI()

    @app.get("/health")
    async def health():  # pragma: no cover - route body irrelevant
        return {}

    @app.get("/api/svc/health")
    async def prefixed_health():  # pragma: no cover
        return {}

    @app.get("/api/svc/things")
    async def things():  # pragma: no cover
        return {}

    app.state.wip_public_paths = public_path_set(["/api/svc/health"])
    paths = declare_api_key_security(app).openapi()["paths"]
    assert paths["/health"]["get"]["security"] == []
    assert paths["/api/svc/health"]["get"]["security"] == []
    assert "security" not in paths["/api/svc/things"]["get"]  # the global requirement


# --- 5-helpers R8: the module's version is the package's --------------------


def test_the_module_version_is_the_package_version():
    assert wip_auth.__version__ == package_version("wip-auth")


# --- 4-identity-events R3: an entity revived mid-pass gets its synonyms back -


class _Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def deactivate_entries(self, entry_ids, updated_by=None, dry_run=False):
        self.calls.append(("deactivate", list(entry_ids)))
        return [{"registry_id": e, "status": "deactivated"} for e in entry_ids]

    async def reactivate_entries(self, entry_ids, updated_by=None):
        self.calls.append(("reactivate", list(entry_ids)))
        return [{"registry_id": e, "status": "reactivated", "reclaimed": [{"k": e}]}
                for e in entry_ids]


@pytest.mark.asyncio
async def test_an_entity_revived_after_it_was_listed_is_reclaimed():
    registry = _Registry()

    async def retired():
        yield ["a", "b"]  # listed retired; "b" is revived before the release

    async def active():
        if False:  # pragma: no cover
            yield []

    async def active_among(ids):
        return [i for i in ids if i == "b"]

    report = await reconcile_synonym_lifecycle(
        registry, "ns", retired, active, active_among, dry_run=False,
    )
    assert registry.calls == [("deactivate", ["a", "b"]), ("reactivate", ["b"])]
    assert report.released == 1
    assert report.reclaimed == 1
    assert [i.entry_id for i in report.items if i.status == "released"] == ["a"]


@pytest.mark.asyncio
async def test_a_dry_run_asks_nothing_back():
    registry = _Registry()

    async def retired():
        yield ["a"]

    async def active():
        if False:  # pragma: no cover
            yield []

    async def active_among(ids):  # pragma: no cover - must not be asked
        raise AssertionError("a dry run releases nothing, so reclaims nothing")

    report = await reconcile_synonym_lifecycle(registry, "ns", retired, active, active_among)
    assert registry.calls == [("deactivate", ["a"])]
    assert report.released == 1
