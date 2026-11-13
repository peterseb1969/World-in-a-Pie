"""A failed activation says exactly what is live.

Provenance: CASE-1096 part 4. A fresh restore activates its restored
identities in 500-item batches, target by target. A failure partway left the
earlier batches (and earlier targets) live, while the error said the
namespace was "invisible" and suggested a "re-run activation" no surface
offers. The error and the job record now give the active, failed and
not-activated counts per target.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from document_store.services.backup_engine import DirectRestoreEngine, RestoreEngineError


class _Response:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._body


def _client_answering(*responses: _Response):
    queue = list(responses)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json, headers):
            return queue.pop(0)

    return _Client


def _ok(n: int) -> _Response:
    return _Response(200, {"errors": 0, "results": [{"status": "activated"}] * n})


def _engine() -> DirectRestoreEngine:
    return DirectRestoreEngine(MagicMock(), None, lambda event: None, registry_base_url="http://registry.test")


def _items(n: int) -> list[dict[str, Any]]:
    return [{"entry_id": f"e{i}"} for i in range(n)]


@pytest.mark.asyncio
async def test_a_failing_later_batch_reports_the_earlier_batch_as_live():
    engine = _engine()
    client = _client_answering(_ok(500), _Response(503, text="registry down"))
    with patch("httpx.AsyncClient", client), pytest.raises(RestoreEngineError) as refused:
        await engine._activate_entries("kb-copy", _items(700))

    message = str(refused.value)
    assert "invisible" not in message and "re-run activation" not in message
    assert "'kb-copy': 500 of 700 active, 200 not activated" in message
    assert refused.value.details["activation"] == {
        "kb-copy": {"total": 700, "active": 500, "failed": 0},
    }


@pytest.mark.asyncio
async def test_a_duplicate_in_a_batch_counts_the_ones_that_did_activate():
    engine = _engine()
    body = {"errors": 1, "results": [{"status": "activated"}] * 2 + [{"status": "error", "error": "dup"}]}
    client = _client_answering(_Response(200, body))
    with patch("httpx.AsyncClient", client), pytest.raises(RestoreEngineError) as refused:
        await engine._activate_entries("kb-copy", _items(3))

    assert "'kb-copy': 2 of 3 active, 1 failed" in str(refused.value)
    assert refused.value.details["activation"]["kb-copy"] == {"total": 3, "active": 2, "failed": 1}


@pytest.mark.asyncio
async def test_an_earlier_target_that_went_live_is_named():
    engine = _engine()
    with patch("httpx.AsyncClient", _client_answering(_ok(2))):
        await engine._activate_entries("first", _items(2))
    with patch("httpx.AsyncClient", _client_answering(_Response(500))), pytest.raises(RestoreEngineError) as refused:
        await engine._activate_entries("second", _items(4))

    message = str(refused.value)
    assert "'first': 2 of 2 active" in message
    assert "'second': 0 of 4 active, 4 not activated" in message
    assert "('first', 'second')" in message or "'first', 'second'" in message
    assert refused.value.details["result_kind"] == "activation_failed"


@pytest.mark.asyncio
async def test_only_a_key_collision_is_called_a_duplicate():
    # CASE-1268: a Registry failure is not a duplicate identity.
    engine = _engine()
    body = {"errors": 2, "results": [
        {"status": "activated"},
        {"status": "error", "error_code": "internal_error", "error": "re-read failed"},
        {"status": "error", "error_code": "already_exists", "error": "key claimed"},
    ]}
    with patch("httpx.AsyncClient", _client_answering(_Response(200, body))), \
            pytest.raises(RestoreEngineError) as refused:
        await engine._activate_entries("kb-copy", _items(3))

    message = str(refused.value)
    assert "2 identit(ies) were not activated (1 already_exists, 1 internal_error)" in message
    assert "1 could not claim their composite key (a genuine duplicate)" in message


@pytest.mark.asyncio
async def test_a_registry_failure_alone_names_no_duplicate():
    engine = _engine()
    body = {"errors": 1, "results": [{"status": "error", "error_code": "internal_error", "error": "x"}]}
    with patch("httpx.AsyncClient", _client_answering(_Response(200, body))), \
            pytest.raises(RestoreEngineError) as refused:
        await engine._activate_entries("kb-copy", _items(1))

    assert "duplicate" not in str(refused.value)
