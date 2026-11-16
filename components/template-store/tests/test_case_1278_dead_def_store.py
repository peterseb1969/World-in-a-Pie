"""A Def-Store that stopped answering is not asked again during the same scan.

Provenance: CASE-1278 (live on tc02 with def-store stopped). The scan asked
Def-Store once per terminology reference — twice for a value that misses as an
id — so during an outage every template version paid its own timeouts and
reporting-sync's 30 s budget for this store expired before it answered. The
scan now remembers the first failure and records the references after it as
skipped, which keeps the counts and the verdict it had.

An answer is never an outage: a 409 (the reference names several terminologies)
is Def-Store talking, so the scan carries on asking, and the next scan asks
again.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.def_store_client import DefStoreAmbiguousError, DefStoreError

INTEGRITY = "/health/integrity"
API = "/api/template-store/templates"


class _CountingDefStore:
    """Def-Store stub: answers every ask, or refuses it with `error`."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self._error = error

    async def get_terminology(self, terminology_id: str | None = None,
                             terminology_value: str | None = None,
                             namespace: str | None = None) -> dict[str, Any] | None:
        self.calls.append(terminology_id or terminology_value or "")
        if self._error is not None:
            raise self._error
        return {"terminology_id": "T-1", "status": "active"}


async def _post(client: AsyncClient, headers: dict, payload: dict) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _tpl(value: str, *refs: str) -> dict:
    fields = [{"name": "id", "type": "string", "label": "Id"}]
    fields += [
        {"name": f"g{i}", "type": "term", "label": f"G{i}", "terminology_ref": ref}
        for i, ref in enumerate(refs)
    ]
    return {"namespace": "wip", "value": value, "label": value.title(), "fields": fields}


async def _mine(
    client: AsyncClient, headers: dict, values: list[tuple[str, list[str]]],
) -> dict[str, list[str]]:
    """Create the given templates and map each one's id to the terminology
    references the scan will be asked about (stored as resolved ids)."""
    created = {}
    for value, refs in values:
        item = await _post(client, headers, _tpl(value, *refs))
        rows = await Template.find({"template_id": item["id"]}).to_list()
        created[item["id"]] = [f.terminology_ref for row in rows for f in row.fields if f.terminology_ref]
    return created


def _skips_for(body: dict, mine: dict[str, list[str]]) -> list[dict]:
    """The scan's skipped checks that belong to these templates — the other
    templates in this test database are another test's business."""
    return [
        s for s in body["checks_skipped"]
        if s["template_id"] in mine and s["reference"] in mine[s["template_id"]]
    ]


@pytest.mark.asyncio
async def test_a_dead_def_store_is_asked_once_for_the_whole_scan(
    client: AsyncClient, auth_headers,
):
    """Three references of mine, one failed ask: the rest are recorded as
    unchecked, not waited out."""
    mine = await _mine(client, auth_headers, [("C1278_A", ["GENDER", "COUNTRY"]),
                                             ("C1278_B", ["DOC_STATUS"])])
    assert sum(len(refs) for refs in mine.values()) == 3

    down = _CountingDefStore(DefStoreError("connection refused", unavailable=True))
    with patch("template_store.services.integrity_service.get_def_store_client",
               return_value=down):
        resp = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 0})

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] != "healthy", body
    assert len(down.calls) == 1, f"Def-Store was asked {len(down.calls)} times"
    reasons = [s["reason"] for s in _skips_for(body, mine)]
    assert len(reasons) == 3, reasons
    assert all(r.startswith("Def-Store did not answer") for r in reasons), reasons
    # One ask failed; the other two know only that the service did not answer
    # earlier in this scan.
    assert sum(1 for s in body["checks_skipped"]
               if s["reason"].startswith("Def-Store did not answer: ")) == 1, body["checks_skipped"]


@pytest.mark.asyncio
async def test_an_ambiguous_answer_is_not_an_outage(client: AsyncClient, auth_headers):
    """A 409 is Def-Store answering: the scan carries on asking, and says the
    reference was ambiguous, never that the service went quiet."""
    mine = await _mine(client, auth_headers, [("C1278_C", ["GENDER", "COUNTRY"]),
                                             ("C1278_D", ["DOC_STATUS"])])

    ambiguous = _CountingDefStore(DefStoreAmbiguousError("names two terminologies"))
    with patch("template_store.services.integrity_service.get_def_store_client",
               return_value=ambiguous):
        resp = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 0})

    body = resp.json()
    assert all(r in set(ambiguous.calls) for refs in mine.values() for r in refs), \
        "the scan stopped after the first 409"
    assert {s["reason"] for s in _skips_for(body, mine)} == {
        "Ambiguous reference: names two terminologies"
    }, _skips_for(body, mine)


@pytest.mark.asyncio
async def test_a_refusal_about_one_reference_is_not_an_outage(client: AsyncClient, auth_headers):
    """A 4xx is Def-Store answering about that one reference: it is skipped,
    and the scan goes on asking about the others."""
    mine = await _mine(client, auth_headers, [("C1278_F", ["GENDER", "COUNTRY"]),
                                             ("C1278_G", ["DOC_STATUS"])])

    refusing = _CountingDefStore(DefStoreError("Failed to get terminology: 422 - bad id"))
    with patch("template_store.services.integrity_service.get_def_store_client",
               return_value=refusing):
        resp = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 0})

    body = resp.json()
    assert all(r in set(refusing.calls) for refs in mine.values() for r in refs), \
        "the scan stopped after the first 4xx"
    assert not any("earlier in this scan" in s["reason"] for s in _skips_for(body, mine)), \
        _skips_for(body, mine)


@pytest.mark.asyncio
async def test_the_next_scan_asks_again(client: AsyncClient, auth_headers):
    """The latch belongs to a scan: an outage does not outlive it."""
    mine = await _mine(client, auth_headers, [("C1278_E", ["GENDER"])])

    with patch("template_store.services.integrity_service.get_def_store_client",
               return_value=_CountingDefStore(DefStoreError("connection refused", unavailable=True))):
        first = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 0})
    assert first.status_code == 503, first.text

    up = _CountingDefStore()
    with patch("template_store.services.integrity_service.get_def_store_client",
               return_value=up):
        second = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 0})

    assert second.status_code == 200, second.text
    body = second.json()
    assert all(r in set(up.calls) for refs in mine.values() for r in refs)
    assert [i for i in body["issues"] if i["template_id"] in mine] == [], body["issues"]
    assert _skips_for(body, mine) == []
