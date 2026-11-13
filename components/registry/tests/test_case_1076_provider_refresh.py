"""The Registry's own key provider always matches the managed records it
imported, whichever caller ran the import (CASE-1076).

A key-file change first seen by a reservation lookup was imported by a pass
without the provider: MongoDB changed, the provider did not, and every later
pass found MongoDB current and refreshed nothing. Until a restart a removed
key kept authenticating at the Registry, a rotated key's old value worked and
its new one did not. Now:

- a pass without a provider uses the one wired at startup;
- every provider-aware pass aligns the provider with MongoDB's managed
  records, not with what the pass changed (another replica, or anything
  else, may have written MongoDB first).
"""

from unittest.mock import patch

import pytest

from registry.services import key_import

from . import test_case_1033_key_import as key_import_tests
from .test_case_1033_key_import import _key_file, _p

provider = key_import_tests.provider  # the shared fixture

NO_REGISTERED = "registry.api.api_keys._api_key_provider_or_none"


def _entry(name: str, plaintext: str, groups: list[str] | None = None) -> dict:
    if groups:  # a key in a privileged group is never scoped
        return {"name": name, "key": plaintext, "owner": f"app:{name}", "groups": groups}
    return {"name": name, "key": plaintext, "owner": f"app:{name}", "groups": [],
            "namespaces": ["default"], "grants": {"default": "write"}}


async def _pass_without_provider() -> None:
    """A pass that has no provider at hand: a lookup before startup wiring,
    or another replica's import seen from this one."""
    with patch(NO_REGISTERED, return_value=None):
        await key_import.run_pass()


@pytest.mark.asyncio
async def test_a_rotated_key_takes_effect_after_a_pass_without_the_provider(
    client, monkeypatch, tmp_path, provider,
):
    name = _p("rot")
    _key_file(monkeypatch, tmp_path, _entry(name, "old-" + name))
    await key_import.run_pass(provider)
    assert provider.lookup("old-" + name).name == name

    _key_file(monkeypatch, tmp_path, _entry(name, "new-" + name))
    await _pass_without_provider()
    await key_import.run_pass(provider)
    assert provider.lookup("old-" + name) is None
    assert provider.lookup("new-" + name).name == name


@pytest.mark.asyncio
async def test_a_removed_admin_key_stops_after_a_pass_without_the_provider(
    client, monkeypatch, tmp_path, provider,
):
    name = _p("gone")
    _key_file(monkeypatch, tmp_path, _entry(name, "pt-" + name, groups=["wip-admins"]))
    await key_import.run_pass(provider)
    assert provider.lookup("pt-" + name).name == name

    _key_file(monkeypatch, tmp_path)  # the key is no longer declared
    await _pass_without_provider()
    await key_import.run_pass(provider)
    assert provider.lookup("pt-" + name) is None


@pytest.mark.asyncio
async def test_a_new_key_works_after_a_pass_without_the_provider(
    client, monkeypatch, tmp_path, provider,
):
    name = _p("new")
    _key_file(monkeypatch, tmp_path, _entry(name, "pt-" + name))
    await _pass_without_provider()
    await key_import.run_pass(provider)
    assert provider.lookup("pt-" + name).name == name


@pytest.mark.asyncio
async def test_a_lookup_triggered_import_refreshes_the_wired_provider(
    client, monkeypatch, tmp_path, provider,
):
    """ensure_current() is what reservation lookups call, with no provider:
    it must reach the provider wired at startup."""
    name = _p("lookup")
    _key_file(monkeypatch, tmp_path, _entry(name, "old-" + name))
    await key_import.run_pass(provider)
    _key_file(monkeypatch, tmp_path, _entry(name, "new-" + name))
    with patch(NO_REGISTERED, return_value=provider):
        await key_import.ensure_current()
    assert provider.lookup("old-" + name) is None
    assert provider.lookup("new-" + name).name == name


@pytest.mark.asyncio
async def test_an_api_key_is_never_pruned_by_the_alignment(
    client, auth_headers, monkeypatch, tmp_path, provider,
):
    """A key the API creates is not a managed record: aligning leaves it."""
    _key_file(monkeypatch, tmp_path)
    created = await client.post(
        "/api/registry/api-keys", headers=auth_headers,
        json={"name": _p("api"), "namespaces": ["default"]},
    )
    assert created.status_code == 201, created.text
    plaintext = created.json()["plaintext_key"]
    await key_import.run_pass(provider)
    assert provider.lookup(plaintext) is not None
