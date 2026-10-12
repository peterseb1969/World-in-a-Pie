"""The namespace-deletion cascade authenticates to reporting-sync with the
install's service key (CASE-851, Phase 2b C1).

The PostgreSQL step used to read only API_KEY / REGISTRY_API_KEY — neither of
which the registry manifest sets — so it sent an empty X-API-Key. Harmless
while reporting-sync enforced nothing; once it authenticates, the cascade
would be refused and silently leave rows behind. The step now uses the key
the manifest actually provides, and never sends an empty one.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from registry.models.deletion_journal import DeletionStep
from registry.services.namespace_deletion import (
    _SERVICE_KEY_VARS,
    NamespaceDeletionService,
)


def _only_env(monkeypatch, **values: str) -> None:
    """Clear every service-key variable, then set exactly the given ones."""
    for var in _SERVICE_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    for var, value in values.items():
        monkeypatch.setenv(var, value)


def _service() -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    svc._postgres_url = "http://wip-reporting-sync:8005"
    return svc


def _response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.text = ""
    return resp


def _patched_client(response: MagicMock):
    patcher = patch("registry.services.namespace_deletion.httpx.AsyncClient")
    mock_client = patcher.start()
    instance = mock_client.return_value
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.delete = AsyncMock(return_value=response)
    return patcher, instance


@pytest.mark.asyncio
@pytest.mark.parametrize("var", ["WIP_AUTH_LEGACY_API_KEY", "MASTER_API_KEY"])
async def test_cascade_sends_the_key_the_manifest_provides(monkeypatch, var):
    """The registry manifest sets WIP_AUTH_LEGACY_API_KEY and MASTER_API_KEY
    (both from the install's api-key secret); either alone must be sent."""
    _only_env(monkeypatch, **{var: "install-secret"})
    step = DeletionStep(order=1, store="postgresql", detail="postgres rows")
    patcher, instance = _patched_client(_response(200, {"total_deleted": 7}))
    try:
        deleted = await _service()._exec_postgresql_step(step, "ns-a")
    finally:
        patcher.stop()

    assert deleted == 7
    assert step.error is None
    sent = instance.delete.call_args.kwargs["headers"]["X-API-Key"]
    assert sent == "install-secret"


@pytest.mark.asyncio
async def test_cascade_without_any_key_sends_nothing_and_says_why(monkeypatch):
    _only_env(monkeypatch)
    step = DeletionStep(order=1, store="postgresql", detail="postgres rows")
    patcher, instance = _patched_client(_response(200, {"total_deleted": 1}))
    try:
        deleted = await _service()._exec_postgresql_step(step, "ns-a")
    finally:
        patcher.stop()

    assert deleted == 0
    instance.delete.assert_not_called()
    assert step.error and "no service API key configured" in step.error


@pytest.mark.asyncio
async def test_refused_cascade_is_recorded_not_silent(monkeypatch):
    _only_env(monkeypatch, MASTER_API_KEY="wrong-or-revoked")
    step = DeletionStep(order=1, store="postgresql", detail="postgres rows")
    patcher, _ = _patched_client(_response(401))
    try:
        deleted = await _service()._exec_postgresql_step(step, "ns-a")
    finally:
        patcher.stop()

    assert deleted == 0
    assert step.error and "401" in step.error
