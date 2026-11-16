"""An app key wip-deploy derives (its own namespace, wip read-only) may omit
the namespace on reporting-sync's listings, as on every other service.

reporting-sync's own rule derived a namespace only for a key that can read
exactly one, so such a key got 400 "namespace is required" from search,
activity, term documents and referenced-by. It now derives through
wip_auth's implied_namespace, as document-store and def-store do. A key that
holds write on wip implies nothing and must still say which.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, state
from reporting_sync.search_service import TermDocumentsResponse
from wip_auth import clear_permission_cache

P = "/api/reporting-sync"


@pytest.fixture(autouse=True)
def _fresh_permission_cache():
    clear_permission_cache()
    yield
    clear_permission_cache()


class _Recorder:
    """Records the namespace scope a term-documents call is given."""

    def __init__(self):
        self.calls: list[tuple[str | None, list[str] | None]] = []

    async def get_term_documents(self, term_id, limit, namespace, within):
        self.calls.append((namespace, within))
        return TermDocumentsResponse(term_id=term_id)


async def _get(key: str, path: str):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": key}) as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_an_app_key_beside_a_read_only_wip_derives_its_own_namespace(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(state, "search_service", recorder)

    r = await _get("rs_app_key", f"{P}/references/term/term-1/documents")

    assert r.status_code == 200, r.text
    assert recorder.calls == [("ns-a", ["ns-a"])]


@pytest.mark.asyncio
async def test_a_key_that_can_write_wip_must_still_name_the_namespace(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(state, "search_service", recorder)

    r = await _get("rs_app_wipwrite_key", f"{P}/references/term/term-1/documents")

    assert r.status_code == 400, r.text
    assert "namespace is required" in r.text
    assert recorder.calls == []
