"""A namespace archive carries the namespace's id, and an ID-preserving
restore re-creates the namespace under it.

A system restore writes grants for a namespace that is not here as orphans,
recording the namespace id they were made for; the Registry activates them
when a namespace with that id is created. So the data restore must create
the namespace under its archived id, a fresh restore (a copy) must not, and
an archive without the id, made before namespace ids, is refused.
Provenance: CASE-1084.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from wip_archive.models import NamespaceConfig

from document_store.services.backup_engine import (
    DirectBackupEngine,
    DirectRestoreEngine,
    RestoreRequester,
)

ARCHIVED = "019a0000-0000-7000-8000-000000001084"
CONFIG = NamespaceConfig(namespace_id=ARCHIVED, prefix="kb", description="KB")


def _mongo(namespace_doc):
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=namespace_doc)
    db = MagicMock()
    db.__getitem__.return_value = coll
    mongo = MagicMock()
    mongo.__getitem__.return_value = db
    return mongo


def _response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(status_code=status, text=str(body))
    resp.json.return_value = body
    return resp


@contextlib.contextmanager
def _registry(put_body: dict):
    with patch("httpx.AsyncClient") as cls:
        client = MagicMock()
        client.put = AsyncMock(return_value=_response(200, put_body))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = client
        yield client


def _restore_engine(events: list) -> DirectRestoreEngine:
    return DirectRestoreEngine(
        _mongo(None), None, events.append,
        registry_base_url="http://registry:8001", registry_api_key="service-key",
        requester=RestoreRequester("admin-key", platform_admin=True),
    )


@pytest.mark.asyncio
async def test_a_backup_records_the_namespace_id():
    engine = DirectBackupEngine(_mongo({"prefix": "kb", "namespace_id": ARCHIVED}), None, lambda _: None)
    config = await engine._read_namespace_config("kb")
    assert config.namespace_id == ARCHIVED


@pytest.mark.asyncio
async def test_a_namespace_without_an_id_is_not_archived():
    engine = DirectBackupEngine(_mongo({"prefix": "kb"}), None, lambda _: None)
    with pytest.raises(RuntimeError, match="no namespace_id"):
        await engine._read_namespace_config("kb")


def test_an_archive_without_the_id_is_refused():
    with pytest.raises(ValidationError, match="namespace_id"):
        NamespaceConfig.model_validate({"prefix": "kb", "description": "old"})


@pytest.mark.asyncio
async def test_an_id_preserving_restore_sends_the_archived_id():
    events: list = []
    with _registry({"prefix": "kb", "namespace_id": ARCHIVED}) as reg:
        await _restore_engine(events)._upsert_namespace("kb", CONFIG)
    assert reg.put.await_args.kwargs["json"]["namespace_id"] == ARCHIVED
    assert not [e for e in events if e.phase == "warning"]


@pytest.mark.asyncio
async def test_a_fresh_restore_sends_no_id():
    with _registry({"prefix": "copy", "namespace_id": "019a0000-0000-7000-8000-00000000c0b1"}) as reg:
        await _restore_engine([])._upsert_namespace("copy", CONFIG, preserve_id_config=False)
    assert "namespace_id" not in reg.put.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_restoring_into_another_namespace_of_the_name_is_a_warning():
    events: list = []
    live = {"prefix": "kb", "namespace_id": "019a0000-0000-7000-8000-00000000beef",
            "warnings": ["namespace id is held by namespace 'kb-old'"]}
    with _registry(live):
        await _restore_engine(events)._upsert_namespace("kb", CONFIG)
    warnings = [e.message for e in events if e.phase == "warning"]
    assert any("kb-old" in w for w in warnings)
    assert any("not the archived namespace" in w and ARCHIVED in w for w in warnings)
