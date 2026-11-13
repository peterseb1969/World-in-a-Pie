"""A service's outbound keys are read from their variables or reported missing.

The publicly documented dev key used to stand in when a key's variables were
unset, and some readers sent an empty key instead. Either way the called
service answered 401, and wip-auth reported that as the Registry being
unreachable (503), so a missing setting looked like an outage. Now:

- ``service_api_key`` returns the first non-empty variable of a chain, or
  raises ``MissingServiceKeyError`` naming it.
- ``require_service_keys`` refuses startup, naming every missing chain.
- ``setup_auth`` answers the error as a 500 that names the variable.
- every wip-auth reader goes through the chain: the Registry client, synonym
  resolution, term resolution, the permission check and key sync.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wip_auth import (
    DEF_STORE_KEY,
    KEY_SYNC_KEY,
    PERMISSION_KEY,
    REGISTRY_KEY,
    MissingServiceKeyError,
    require_service_keys,
    service_api_key,
    setup_auth,
    setup_key_sync,
)
from wip_auth.config import AuthConfig
from wip_auth.registry_client import RegistryClientBase

_ALL = {name for chain in (REGISTRY_KEY, DEF_STORE_KEY, PERMISSION_KEY, KEY_SYNC_KEY) for name in chain}


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestServiceApiKey:
    def test_the_named_variable_comes_first(self, no_keys: pytest.MonkeyPatch) -> None:
        no_keys.setenv("REGISTRY_API_KEY", "named")
        no_keys.setenv("API_KEY", "general")
        assert service_api_key(*REGISTRY_KEY) == "named"

    def test_api_key_is_the_fallback(self, no_keys: pytest.MonkeyPatch) -> None:
        no_keys.setenv("API_KEY", "general")
        assert service_api_key(*REGISTRY_KEY) == "general"

    def test_an_empty_value_counts_as_unset(self, no_keys: pytest.MonkeyPatch) -> None:
        no_keys.setenv("REGISTRY_API_KEY", "")
        no_keys.setenv("API_KEY", "general")
        assert service_api_key(*REGISTRY_KEY) == "general"

    def test_nothing_set_names_the_variable_and_never_defaults(
        self, no_keys: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(MissingServiceKeyError) as caught:
            service_api_key(*REGISTRY_KEY)
        assert caught.value.names == REGISTRY_KEY
        assert str(caught.value) == "REGISTRY_API_KEY is not set (nor API_KEY)"


class TestStartupCheck:
    def test_every_missing_chain_is_named_once(self, no_keys: pytest.MonkeyPatch) -> None:
        no_keys.setenv("WIP_AUTH_LEGACY_API_KEY", "own")
        with pytest.raises(RuntimeError) as caught:
            require_service_keys(
                (REGISTRY_KEY, DEF_STORE_KEY, PERMISSION_KEY, REGISTRY_KEY),
                service="probe",
            )
        message = str(caught.value)
        assert message.startswith("probe cannot start")
        assert message.count("REGISTRY_API_KEY is not set") == 1
        assert "DEF_STORE_API_KEY is not set" in message
        assert "WIP_AUTH_LEGACY_API_KEY" not in message

    def test_all_set_passes(self, no_keys: pytest.MonkeyPatch) -> None:
        no_keys.setenv("API_KEY", "general")
        require_service_keys((REGISTRY_KEY, DEF_STORE_KEY), service="probe")


class TestReaders:
    def test_the_registry_client_refuses_without_a_key(
        self, no_keys: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(MissingServiceKeyError, match="REGISTRY_API_KEY"):
            RegistryClientBase(base_url="http://registry.invalid")

    def test_the_registry_client_takes_the_named_key(
        self, no_keys: pytest.MonkeyPatch
    ) -> None:
        no_keys.setenv("REGISTRY_API_KEY", "named")
        assert RegistryClientBase(base_url="http://registry.invalid").api_key == "named"

    def test_resolution_and_permission_readers_refuse(
        self, no_keys: pytest.MonkeyPatch
    ) -> None:
        from wip_auth import permissions, resolve, term_resolution

        with pytest.raises(MissingServiceKeyError, match="REGISTRY_API_KEY"):
            resolve._get_api_key()
        with pytest.raises(MissingServiceKeyError, match="DEF_STORE_API_KEY"):
            term_resolution._get_api_key()
        with pytest.raises(MissingServiceKeyError, match="WIP_AUTH_LEGACY_API_KEY"):
            permissions._service_api_key()

    @pytest.mark.asyncio
    async def test_key_sync_refuses_instead_of_polling_with_an_empty_key(
        self, no_keys: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(MissingServiceKeyError, match="REGISTRY_API_KEY"):
            await setup_key_sync([], registry_url="http://registry.invalid")


def _app() -> FastAPI:
    app = FastAPI()
    setup_auth(app, AuthConfig(mode="none"))

    @app.get("/call")
    async def call() -> dict:
        service_api_key(*REGISTRY_KEY)
        return {}

    return app


def test_setup_auth_answers_a_missing_key_as_misconfiguration(
    no_keys: pytest.MonkeyPatch,
) -> None:
    response = TestClient(_app()).get("/call")
    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "service_key_missing"
    assert "REGISTRY_API_KEY is not set" in body["detail"]
    assert "could not be reached" not in body["detail"]
