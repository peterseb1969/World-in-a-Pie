"""A batch sweep over unchanged definitions writes nothing to PostgreSQL.

The startup backfill re-sends every terminology, term and term relation on
every start. It stays the repair for content drift that count-based parity
cannot see (a lost update event), so it keeps running in full — but a row
that already matches must not be rewritten. The contract under test, against
a real PostgreSQL:

  - A second sweep with nothing changed leaves every terminology, term,
    relation and term-name row at the same row version (xmin).
  - A changed term is rewritten, and only that term: its row and its name
    rows change, its unchanged neighbours keep their row versions.
  - Name rows are compared as a set: the same names in another order are
    unchanged.
"""

import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.batch_sync import BatchSyncService

from .conftest import requires_postgres

NS = "sweepns"

TERMINOLOGY = {
    "terminology_id": "TL-1", "namespace": NS, "value": "COLOURS", "label": "Colours",
    "description": None, "case_sensitive": False, "allow_multiple": False,
    "extensible": True, "mutable": False, "status": "active", "term_count": 2,
    "created_at": "2026-01-01T00:00:00Z", "created_by": "t",
    "updated_at": "2026-01-01T00:00:00Z", "updated_by": "t",
}


def _term(term_id: str, value: str, names: list[dict]) -> dict:
    return {
        "term_id": term_id, "namespace": NS, "terminology_id": "TL-1",
        "terminology_value": "COLOURS", "value": value, "label": value,
        "description": None, "sort_order": 0, "parent_term_id": None,
        "status": "active", "deprecated_reason": None, "replaced_by_term_id": None,
        "names": names, "aliases": [n["value"] for n in names],
        "created_at": "2026-01-01T00:00:00Z", "created_by": "t",
        "updated_at": "2026-01-01T00:00:00Z", "updated_by": "t",
    }


RELATION = {
    "namespace": NS, "source_term_id": "T-2", "target_term_id": "T-1",
    "relation_type": "is_a", "source_term_value": "CRIMSON", "target_term_value": "RED",
    "source_terminology_id": "TL-1", "target_terminology_id": "TL-1",
    "metadata": {}, "status": "active",
    "created_at": "2026-01-01T00:00:00Z", "created_by": "t",
}


def _def_store(terminologies: list, terms: list, relations: list):
    """A fake def-store answering the sweeps' three listings, one page each."""
    def get(url, params=None, headers=None, **_):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/terminologies"):
            items = terminologies
        elif url.endswith("/terms"):
            items = terms
        else:
            items = relations
        resp.json.return_value = {"items": items, "page": 1, "pages": 1}
        return resp

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=get)
    return patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=client)


async def _sweep(service: BatchSyncService, terms: list) -> None:
    with _def_store([TERMINOLOGY], terms, [RELATION]):
        assert (await service.batch_sync_terminologies())["failed"] == 0
        assert (await service.batch_sync_terms())["failed"] == 0
        assert (await service.batch_sync_term_relations())["failed"] == 0


async def _versions(pool) -> dict[str, list]:
    """Row versions per table; a rewritten row gets a new xmin, a re-inserted one a new ctid."""
    out = {}
    async with pool.acquire() as conn:
        for table, key in (
            ("terminologies", '"terminology_id"'),
            ("terms", '"term_id"'),
            ("term_relations", '"source_term_id"'),
            ("term_names", '"term_id", "value"'),
        ):
            out[table] = [
                tuple(r) for r in await conn.fetch(
                    f'SELECT {key}, xmin::text, ctid::text FROM "{NS}"."{table}" ORDER BY {key}'
                )
            ]
    return out


def _service(pool) -> BatchSyncService:
    service = BatchSyncService(pool)
    # The closure rebuild is not under test and needs bookkeeping tables.
    service.closure.rebuild = AsyncMock()
    return service


NAMES_RED = [{"value": "rouge", "language": "fr"}, {"value": "rot", "language": "de"}]
NAMES_CRIMSON = [{"value": "carmin", "language": "fr"}]


@requires_postgres
@pytest.mark.asyncio
async def test_unchanged_sweep_rewrites_nothing(pg_pool):
    service = _service(pg_pool)
    terms = [_term("T-1", "RED", NAMES_RED), _term("T-2", "CRIMSON", NAMES_CRIMSON)]
    await _sweep(service, terms)
    first = await _versions(pg_pool)
    assert len(first["term_names"]) == 3

    # Same data, names listed in another order.
    again = copy.deepcopy(terms)
    again[0]["names"] = list(reversed(again[0]["names"]))
    await _sweep(service, again)
    assert await _versions(pg_pool) == first


@requires_postgres
@pytest.mark.asyncio
async def test_only_the_changed_term_is_rewritten(pg_pool):
    service = _service(pg_pool)
    terms = [_term("T-1", "RED", NAMES_RED), _term("T-2", "CRIMSON", NAMES_CRIMSON)]
    await _sweep(service, terms)
    before = await _versions(pg_pool)

    changed = copy.deepcopy(terms)
    changed[1]["label"] = "Crimson red"
    changed[1]["names"] = [*NAMES_CRIMSON, {"value": "karmesin", "language": "de"}]
    await _sweep(service, changed)
    after = await _versions(pg_pool)

    assert after["terminologies"] == before["terminologies"]
    assert after["term_relations"] == before["term_relations"]
    t1_before, t2_before = before["terms"]
    t1_after, t2_after = after["terms"]
    assert t1_after == t1_before
    assert t2_after != t2_before

    def names_of(versions, term_id):
        return [r for r in versions["term_names"] if r[0] == term_id]

    assert names_of(after, "T-1") == names_of(before, "T-1")
    assert [r[1] for r in names_of(after, "T-2")] == ["carmin", "karmesin"]

    async with pg_pool.acquire() as conn:
        label = await conn.fetchval(f'SELECT "label" FROM "{NS}"."terms" WHERE "term_id" = $1', "T-2")
    assert label == "Crimson red"
