"""Template lifecycle, reserved names and system schemas in reporting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from reporting_sync import worker as worker_module
from reporting_sync.models import ReportingConfig, SyncStatus
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.worker import SyncWorker


@pytest.mark.parametrize("name", ["public", "information_schema", "pg_catalog", "pg_toast"])
def test_a_system_schema_is_never_a_namespace_schema(name):
    with pytest.raises(ValueError):
        SchemaManager(None).schema_for(name)


@pytest.mark.parametrize("name", ["term_names", "term_closure", "terms", "templates"])
def test_every_metadata_table_name_is_reserved(name):
    assert SchemaManager.is_reserved_relation_name(name)


def test_disabled_strategy_means_no_sync():
    assert not ReportingConfig(sync_strategy="disabled").syncs
    assert not ReportingConfig(sync_enabled=False).syncs
    assert ReportingConfig().syncs


def _upstream(monkeypatch, items):
    def handle(request):
        assert request.url.params["status"] == "active"
        return httpx.Response(200, json={"items": items})

    real = httpx.AsyncClient
    monkeypatch.setattr(worker_module.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handle), **k))


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining,flips", [([{"version": 1}], False), ([], True)])
async def test_a_deactivated_version_flips_the_template_only_when_none_is_active(
    monkeypatch, remaining, flips, real_has_active_version,
):
    _upstream(monkeypatch, remaining)
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    status = SyncStatus(running=True, connected_to_nats=True, connected_to_postgres=True)
    w = SyncWorker(MagicMock(), MagicMock(), pool, status)
    w.schema_manager.ensure_templates_table = AsyncMock(return_value='"ns"."templates"')
    w.schema_manager.ensure_table_for_template = AsyncMock(return_value="")
    event = {"event_type": "template.deleted", "template": {
        "template_id": "t1", "namespace": "ns", "value": "Visit", "version": 2,
        "reporting": {"sync_enabled": False},
    }}
    await w._process_template_event(event)
    updates = [c for c in conn.execute.await_args_list if "SET \"status\" = 'inactive'" in c.args[0]]
    assert bool(updates) is flips
