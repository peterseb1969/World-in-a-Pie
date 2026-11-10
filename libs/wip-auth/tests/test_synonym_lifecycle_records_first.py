"""A retirement or revival is recorded in the Registry before the store
writes it, and the reconcile pass keeps what it applied when the Registry
stops answering.

``record_entry_lifecycle`` / ``entry_lifecycle`` are the one rule every store
follows: the Registry is asked first; when it does not answer, or refuses an
entry, what it did record is undone and the error is raised, so the store
writes nothing; when the store's write then fails, the Registry change is
undone. A revival's warning (a synonym another entity took meanwhile) is not
a refusal.

``reconcile_synonym_lifecycle`` raises SynonymLifecycleAborted — a 503 —
carrying the report of the batches it had applied.
"""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wip_auth import registry_unavailable_handler
from wip_auth.registry_client import RegistryClientBase, RegistryError, RegistryUnavailableError
from wip_auth.synonym_lifecycle import (
    LifecycleRefused,
    SynonymLifecycleAborted,
    entry_lifecycle,
    reconcile_synonym_lifecycle,
    record_entry_lifecycle,
)

BASE = "http://registry.test:8001"


class FakeRegistry:
    """Records lifecycle calls; answers each entry with ``status`` unless
    ``answers`` overrides it, or raises ``down`` instead of answering."""

    def __init__(self, *, down: Exception | None = None, answers: dict[str, dict] | None = None,
                 drop: int = 0):
        self.calls: list[tuple[str, list[str]]] = []
        self.down = down
        self.answers = answers or {}
        self.drop = drop

    async def _call(self, verb: str, entry_ids: list[str], ok: str) -> list[dict[str, Any]]:
        self.calls.append((verb, list(entry_ids)))
        if self.down is not None:
            raise self.down
        results = [self.answers.get(e, {"registry_id": e, "status": ok}) for e in entry_ids]
        return results[: len(results) - self.drop] if self.drop else results

    async def deactivate_entries(self, entry_ids, updated_by=None, dry_run=False):
        return await self._call("deactivate", entry_ids, "deactivated")

    async def reactivate_entries(self, entry_ids, updated_by=None):
        return await self._call("reactivate", entry_ids, "reactivated")


@pytest.mark.asyncio
async def test_a_recorded_change_returns_the_registrys_warnings():
    registry = FakeRegistry(answers={"b": {
        "registry_id": "b", "status": "reactivated", "warnings": ["held by e-9"],
    }})
    warnings = await record_entry_lifecycle(registry, ["a", "b"], retired=False, actor="me")
    assert warnings == ["held by e-9"]
    assert registry.calls == [("reactivate", ["a", "b"])]


@pytest.mark.asyncio
async def test_no_entries_ask_nothing():
    registry = FakeRegistry(down=RegistryUnavailableError("down"))
    assert await record_entry_lifecycle(registry, [], retired=True, actor=None) == []
    assert registry.calls == []


@pytest.mark.asyncio
async def test_an_outage_is_raised_and_what_may_have_landed_is_undone():
    registry = FakeRegistry(down=RegistryUnavailableError("down"))
    with pytest.raises(RegistryUnavailableError):
        await record_entry_lifecycle(registry, ["a", "b"], retired=True, actor="me")
    assert registry.calls == [("deactivate", ["a", "b"]), ("reactivate", ["a", "b"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "not_found"])
async def test_a_refused_entry_undoes_the_recorded_ones(status):
    registry = FakeRegistry(answers={"b": {"registry_id": "b", "status": status, "error": "no"}})
    with pytest.raises(LifecycleRefused, match="could not retire"):
        await record_entry_lifecycle(registry, ["a", "b", "c"], retired=True, actor="me")
    assert registry.calls == [("deactivate", ["a", "b", "c"]), ("reactivate", ["a", "c"])]


@pytest.mark.asyncio
async def test_an_incomplete_answer_is_a_refusal():
    registry = FakeRegistry(drop=1)
    with pytest.raises(LifecycleRefused, match="did not answer every entry"):
        await record_entry_lifecycle(registry, ["a", "b"], retired=False, actor="me")
    assert registry.calls == [("reactivate", ["a", "b"]), ("deactivate", ["a"])]


def test_a_refusal_is_a_registry_error_and_not_an_outage():
    assert issubclass(LifecycleRefused, RegistryError)
    assert not issubclass(LifecycleRefused, RegistryUnavailableError)


@pytest.mark.asyncio
async def test_the_block_does_not_run_when_the_registry_cannot_record():
    registry = FakeRegistry(down=RegistryUnavailableError("down"))
    ran = False
    with pytest.raises(RegistryUnavailableError):
        async with entry_lifecycle(registry, ["a"], retired=True, actor="me"):
            ran = True
    assert ran is False


@pytest.mark.asyncio
async def test_a_failed_write_undoes_the_registry_change():
    registry = FakeRegistry()
    with pytest.raises(RuntimeError, match="write failed"):
        async with entry_lifecycle(registry, ["a"], retired=True, actor="me"):
            raise RuntimeError("write failed")
    assert registry.calls == [("deactivate", ["a"]), ("reactivate", ["a"])]


@pytest.mark.asyncio
async def test_a_failed_undo_does_not_hide_the_write_error():
    class UndoFails(FakeRegistry):
        async def reactivate_entries(self, entry_ids, updated_by=None):
            raise RegistryUnavailableError("down for the undo")

    registry = UndoFails()
    with pytest.raises(RuntimeError, match="write failed"):
        async with entry_lifecycle(registry, ["a"], retired=True, actor="me"):
            raise RuntimeError("write failed")


# ── the client: an outage is RegistryUnavailableError, a refusal RegistryError ──

LIFECYCLE = f"{BASE}/api/registry/entries/deactivate"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 429])
async def test_an_error_status_is_an_outage(httpx_mock, status):
    httpx_mock.add_response(url=LIFECYCLE, method="POST", status_code=status)
    with pytest.raises(RegistryUnavailableError, match=f"HTTP {status}"):
        await RegistryClientBase(base_url=BASE, api_key="k").deactivate_entries(["e"])


@pytest.mark.asyncio
async def test_a_transport_error_is_an_outage(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=LIFECYCLE, method="POST")
    with pytest.raises(RegistryUnavailableError, match="ConnectError"):
        await RegistryClientBase(base_url=BASE, api_key="k").deactivate_entries(["e"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 422])
async def test_a_client_error_status_is_a_refusal(httpx_mock, status):
    httpx_mock.add_response(url=LIFECYCLE, method="POST", status_code=status, text="no")
    with pytest.raises(RegistryError, match=str(status)):
        await RegistryClientBase(base_url=BASE, api_key="k").deactivate_entries(["e"])


# ── the reconcile pass keeps what it applied ──────────────────────────────────


@pytest.mark.asyncio
async def test_an_outage_mid_pass_carries_the_applied_batches():
    class DownOnSecond(FakeRegistry):
        async def deactivate_entries(self, entry_ids, updated_by=None, dry_run=False):
            if self.calls:
                self.calls.append(("deactivate", list(entry_ids)))
                raise RegistryUnavailableError("Registry unavailable (entries deactivate): HTTP 503")
            return await super().deactivate_entries(entry_ids, updated_by, dry_run)

    async def retired():
        yield ["a", "b"]
        yield ["c"]

    async def active():
        if False:
            yield []

    async def _none_active(ids):
        return []

    with pytest.raises(SynonymLifecycleAborted) as caught:
        await reconcile_synonym_lifecycle(
            DownOnSecond(), "ns", retired, active, _none_active, dry_run=False,
        )
    report = caught.value.report.to_dict()
    assert report["released"] == 2
    assert report["retired_scanned"] == 2  # the unanswered batch is not counted
    assert "HTTP 503" in report["aborted"]
    assert isinstance(caught.value, RegistryUnavailableError)


def test_the_503_body_carries_the_report():
    from wip_auth.synonym_lifecycle import LifecycleReport

    report = LifecycleReport(namespace="ns", dry_run=False, revive=False, released=3)
    app = FastAPI()
    app.add_exception_handler(RegistryUnavailableError, registry_unavailable_handler)

    @app.post("/pass")
    async def run_pass():
        raise SynonymLifecycleAborted(report, RegistryUnavailableError("gone", "entries deactivate"))

    resp = TestClient(app).post("/pass")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_code"] == "registry_unavailable"
    assert body["report"]["released"] == 3
    assert body["report"]["aborted"] == "gone"


def test_a_plain_outage_has_no_report():
    app = FastAPI()
    app.add_exception_handler(RegistryUnavailableError, registry_unavailable_handler)

    @app.get("/x")
    async def x():
        raise RegistryUnavailableError("gone")

    body = TestClient(app).get("/x").json()
    assert "report" not in body
