"""A term whose value contains '/' is addressed by that value through
reporting-sync's term doors, exactly like any other term.

The value travels in the path as the fully qualified 'ns:terminology:value'
form, and a '/' in it arrives as a path separator whether it is sent raw or
as %2F: Starlette routes on the decoded path. A one-segment route parameter
therefore missed the route (404) or, for a trailing '/', redirected to a
different identifier. The three routes take the rest of the path instead,
and each test checks the identifier the handler hands on is the one sent.
"""

from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, state
from reporting_sync.search_service import (
    EntityReferencesResponse,
    ReferencedByResponse,
    TermDocumentsResponse,
)
from wip_auth import clear_permission_cache

P = "/api/reporting-sync"
ADMIN = "rs_test_admin_key"

VALUES = ["A/B", "mg/dL", "/lead", "trail/", "a//b"]


@pytest.fixture(autouse=True)
def _fresh_permission_cache():
    clear_permission_cache()
    yield
    clear_permission_cache()


class _Recorder:
    """Records the identifier each term door hands to the search service."""

    def __init__(self):
        self.ids: list[str] = []

    async def get_term_documents(self, term_id, limit, namespace, within):
        self.ids.append(term_id)
        return TermDocumentsResponse(term_id=term_id)

    async def get_entity_references(self, entity_type, entity_id):
        self.ids.append(entity_id)
        return EntityReferencesResponse()

    async def get_referenced_by(self, entity_type, entity_id, limit, namespace, within):
        self.ids.append(entity_id)
        return ReferencedByResponse(entity_type=entity_type, entity_id=entity_id)


async def _get(path: str):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": ADMIN}) as c:
        return await c.get(path)


def _doors(term: str) -> list[str]:
    return [
        f"{P}/references/term/{term}/documents?namespace=wip",
        f"{P}/entity/term/{term}/references",
        f"{P}/entity/term/{term}/referenced-by?namespace=wip",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("encode", [True, False], ids=["encoded", "raw"])
async def test_every_term_door_hands_on_a_slash_value_intact(monkeypatch, value, encode):
    recorder = _Recorder()
    monkeypatch.setattr(state, "search_service", recorder)
    qualified = f"wip:SLASH_UNITS:{value}"
    sent = quote(qualified, safe=":") if encode else qualified

    for door in _doors(sent):
        r = await _get(door)
        assert r.status_code == 200, (door, r.status_code, r.text)

    assert recorder.ids == [qualified] * 3
