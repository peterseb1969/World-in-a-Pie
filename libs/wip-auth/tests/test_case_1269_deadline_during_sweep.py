"""The runtime-key deadline is judged when the check answers, not before
its bcrypt sweep.

Provenance: CASE-1269 (2b8644ae R4). The deadline was read once before the
sweep, so a copy that went past it while bcrypt ran answered an unknown key
"invalid" (401) instead of "cannot verify" (503), and still accepted a
runtime key after the deadline.
"""

from __future__ import annotations

import pytest

from wip_auth.models import APIKeyRecord
from wip_auth.providers import api_key as api_key_module
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key


def _provider_with_runtime_key() -> tuple[APIKeyProvider, APIKeyRecord]:
    provider = APIKeyProvider([])
    record = APIKeyRecord(name="rt", key_hash=hash_api_key("rt-key", "wip_auth_salt"))
    provider.replace_runtime_keys([record])
    provider.confirm_runtime_keys(60.0)
    return provider, record


def _deadline_passes_during_verify(monkeypatch, provider, *, match: bool) -> None:
    def verify(key, key_hash, salt="wip_auth_salt"):
        provider.expire_runtime_keys()  # the deadline passes while bcrypt runs
        return match

    monkeypatch.setattr(api_key_module, "verify_api_key", verify)


def test_an_unknown_key_checked_across_the_deadline_cannot_be_verified(monkeypatch):
    provider, _ = _provider_with_runtime_key()
    _deadline_passes_during_verify(monkeypatch, provider, match=False)
    with pytest.raises(api_key_module.KeysUnverifiable):
        provider._validate_key("no-such-key")


def test_a_runtime_key_matched_across_the_deadline_is_not_accepted(monkeypatch):
    provider, _ = _provider_with_runtime_key()
    _deadline_passes_during_verify(monkeypatch, provider, match=True)
    with pytest.raises(api_key_module.KeysUnverifiable):
        provider._validate_key("rt-key")


def test_a_runtime_key_within_its_deadline_is_accepted():
    provider, record = _provider_with_runtime_key()
    found, message = provider._validate_key("rt-key")
    assert found is not None and found.name == record.name, message
