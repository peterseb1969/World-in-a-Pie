"""Tests for the KeySyncService and replace_runtime_keys."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wip_auth import APIKeyRecord, KeySyncService
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key


class TestReplaceRuntimeKeys:
    """Tests for APIKeyProvider.replace_runtime_keys()."""

    def _make_provider(self, keys: list[APIKeyRecord]) -> APIKeyProvider:
        return APIKeyProvider(keys)

    def test_replace_preserves_config_keys(self):
        config_key = APIKeyRecord(
            name="wip-admins", key_hash=hash_api_key("admin"), groups=["wip-admins"]
        )
        runtime_key = APIKeyRecord(
            name="old-app", key_hash=hash_api_key("old"), groups=[]
        )
        provider = self._make_provider([config_key, runtime_key])
        assert len(provider._keys) == 2

        new_runtime = APIKeyRecord(
            name="new-app", key_hash=hash_api_key("new"), groups=[]
        )
        provider.replace_runtime_keys([new_runtime], config_key_names={"wip-admins"})

        names = {k.name for k in provider._keys}
        assert "wip-admins" in names
        assert "new-app" in names
        assert "old-app" not in names

    def test_replace_filters_disabled_keys(self):
        config_key = APIKeyRecord(
            name="config", key_hash=hash_api_key("c"), groups=["wip-admins"]
        )
        provider = self._make_provider([config_key])

        disabled = APIKeyRecord(
            name="disabled-app", key_hash=hash_api_key("d"), groups=[], enabled=False
        )
        enabled = APIKeyRecord(
            name="enabled-app", key_hash=hash_api_key("e"), groups=[], enabled=True
        )
        provider.replace_runtime_keys([disabled, enabled], config_key_names={"config"})

        names = {k.name for k in provider._keys}
        assert "enabled-app" in names
        assert "disabled-app" not in names

    def test_replace_keeps_valid_cache_entries_and_drops_removed_ones(self):
        """Sync runs every 30 s: it must not force every key back through
        bcrypt, only forget keys that are gone."""
        config_key = APIKeyRecord(
            name="config", key_hash=hash_api_key("c"), groups=["wip-admins"]
        )
        runtime_key = APIKeyRecord(
            name="runtime", key_hash=hash_api_key("r"), groups=["wip-users"]
        )
        provider = self._make_provider([config_key, runtime_key])
        # Simulate cached verifications
        provider._verified_cache["config-fp"] = config_key
        provider._verified_cache["runtime-fp"] = runtime_key

        provider.replace_runtime_keys([], config_key_names={"config"})
        assert provider._verified_cache == {"config-fp": config_key}


class TestKeySyncService:
    """Tests for KeySyncService polling."""

    def _make_service(self) -> tuple[KeySyncService, APIKeyProvider]:
        config_key = APIKeyRecord(
            name="wip-services", key_hash=hash_api_key("svc"), groups=["wip-services"]
        )
        provider = APIKeyProvider([config_key])
        service = KeySyncService(
            registry_url="http://localhost:8001",
            api_key="svc",
            provider=provider,
            config_key_names={"wip-services"},
            interval_seconds=1,
        )
        return service, provider

    @pytest.mark.asyncio
    async def test_sync_once_updates_provider(self):
        service, provider = self._make_service()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "name": "app-key",
                "key_hash": hash_api_key("app123"),
                "owner": "test",
                "groups": [],
                "description": None,
                "created_at": "2026-01-01T00:00:00Z",
                "expires_at": None,
                "enabled": True,
                "namespaces": ["wip"],
            }
        ]

        with patch("wip_auth.key_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await service._sync_once()

        names = {k.name for k in provider._keys}
        assert "wip-services" in names  # config key preserved
        assert "app-key" in names  # runtime key added

    @pytest.mark.asyncio
    async def test_sync_once_handles_connection_error(self):
        """Sync should not raise on connection errors."""
        service, provider = self._make_service()

        import httpx
        with patch("wip_auth.key_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            # Should not raise
            await service._sync_once()

        # Config key should still be there
        assert len(provider._keys) == 1
        assert provider._keys[0].name == "wip-services"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_name", ["ConnectError", "ConnectTimeout", "ReadTimeout"])
    async def test_unreachable_registry_is_a_warning_not_a_traceback(self, exc_name, caplog):
        """A Registry still starting (connect timeout) is routine: one
        warning line, no 'unexpected error' traceback."""
        import logging

        import httpx
        service, _ = self._make_service()
        with patch("wip_auth.key_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=getattr(httpx, exc_name)("down"))
            mock_client_cls.return_value = mock_client
            with caplog.at_level(logging.WARNING, logger="wip_auth.key_sync"):
                await service._sync_once()

        messages = [(r.levelno, r.getMessage()) for r in caplog.records]
        assert any(lvl == logging.WARNING and "connection failed" in msg for lvl, msg in messages)
        assert not any(lvl >= logging.ERROR for lvl, _ in messages)

    @pytest.mark.asyncio
    async def test_start_stop(self):
        service, _ = self._make_service()

        with patch.object(service, "_sync_once", new_callable=AsyncMock):
            await service.start()
            assert service._task is not None
            assert not service._task.done()

            await service.stop()
            assert not service._running
