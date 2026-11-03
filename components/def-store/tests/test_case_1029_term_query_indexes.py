"""Term and term-relation listings are served by indexes, whoever the caller.

A superadmin key carries no namespace filter, and every compound index on
terms leads with namespace, so its listings used to scan the collection.
The contract under test:

  - Listing a terminology's terms names the terminology's own namespace, and
    the default sort (sort_order, then value) is served by an index — no
    collection scan, no in-memory sort.
  - Naming that namespace narrows the caller's namespace filter and never
    widens it: a caller who cannot read the namespace still gets nothing.
  - A lookup by term_id alone is served by an index.
  - The all-relations listing pages in a fixed order, so a paged sweep sees
    every relation exactly once.
  - The superseded sort index is dropped at startup.
  - Startup builds the indexes Beanie silently skips (they share their keys
    with a case-insensitive twin): the unique value index then refuses a
    second term with the same value in one terminology. When the data
    already holds such duplicates, startup reports them and carries on.
"""

import contextlib

import pymongo.errors
import pytest
from beanie.odm.enums import SortDirection
from httpx import AsyncClient

from def_store.main import reconcile_term_indexes
from def_store.models.term import TERM_INDEXES_BUILT_AT_STARTUP, Term
from def_store.models.term_relation import TermRelation
from def_store.services.terminology_service import TerminologyService

API = "/api/def-store"


def _stages(explain: dict) -> list[tuple[str, str | None]]:
    """Flatten the winning plan into (stage, indexName) pairs."""
    winning = explain["queryPlanner"]["winningPlan"]
    winning = winning.get("queryPlan", winning)
    out: list[tuple[str, str | None]] = []

    def walk(node: dict) -> None:
        out.append((node["stage"], node.get("indexName")))
        if "inputStage" in node:
            walk(node["inputStage"])
        for child in node.get("inputStages", []):
            walk(child)

    walk(winning)
    return out


async def _terminology_with_terms(client: AsyncClient, auth_headers: dict, n: int) -> str:
    resp = await client.post(
        f"{API}/terminologies",
        json=[{"value": "C1029_VOC", "label": "C1029", "namespace": "wip"}],
        headers=auth_headers,
    )
    tid = resp.json()["results"][0]["id"]
    resp = await client.post(
        f"{API}/terminologies/{tid}/terms",
        json=[{"value": f"T{i:03d}", "sort_order": i % 3} for i in range(n)],
        headers=auth_headers,
    )
    assert resp.json()["succeeded"] == n, resp.json()
    return tid


@pytest.mark.asyncio
async def test_listing_sort_is_index_served(client: AsyncClient, auth_headers: dict):
    tid = await _terminology_with_terms(client, auth_headers, 30)
    coll = Term.get_motor_collection()

    # The shape TerminologyService.list_terms sends for a superadmin.
    explain = await coll.find({"terminology_id": tid, "namespace": "wip"}) \
        .sort([("sort_order", 1), ("value", 1)]).limit(10).explain()
    stages = _stages(explain)
    assert ("IXSCAN", "ns_terminology_sort_value_idx") in stages, stages
    assert all(s not in ("COLLSCAN", "SORT") for s, _ in stages), stages

    count_explain = await Term.get_motor_collection().database.command(
        "explain",
        {"count": coll.name, "query": {"terminology_id": tid, "namespace": "wip"}},
    )
    assert all(s != "COLLSCAN" for s, _ in _stages(count_explain))


@pytest.mark.asyncio
async def test_listing_order_and_total_unchanged(client: AsyncClient, auth_headers: dict):
    tid = await _terminology_with_terms(client, auth_headers, 30)
    resp = await client.get(
        f"{API}/terminologies/{tid}/terms", params={"page_size": 100}, headers=auth_headers
    )
    body = resp.json()
    assert body["total"] == 30
    keys = [(t["sort_order"], t["value"]) for t in body["items"]]
    assert keys == sorted(keys)


@pytest.mark.asyncio
async def test_namespace_narrows_and_never_widens(client: AsyncClient, auth_headers: dict):
    tid = await _terminology_with_terms(client, auth_headers, 5)

    terms, total = await TerminologyService.list_terms(
        terminology_id=tid, ns_filter={}, namespace="wip"
    )
    assert total == 5 and len(terms) == 5

    terms, total = await TerminologyService.list_terms(
        terminology_id=tid, ns_filter={"namespace": {"$in": ["wip", "test-ns"]}}, namespace="wip"
    )
    assert total == 5

    terms, total = await TerminologyService.list_terms(
        terminology_id=tid, ns_filter={"namespace": {"$in": ["test-ns"]}}, namespace="wip"
    )
    assert (terms, total) == ([], 0)


@pytest.mark.asyncio
async def test_term_id_lookup_is_index_served(client: AsyncClient, auth_headers: dict):
    await _terminology_with_terms(client, auth_headers, 3)
    term_id = (await Term.find_one({})).term_id
    explain = await Term.get_motor_collection().find({"term_id": {"$in": [term_id]}}).explain()
    assert ("IXSCAN", "term_id_idx") in _stages(explain)


@pytest.mark.asyncio
async def test_all_relations_pages_cover_every_relation_once(
    client: AsyncClient, auth_headers: dict
):
    # Inserted directly: the listing reads relations, it does not validate
    # their endpoints, and 25 real terms add nothing to what is under test.
    await TermRelation.insert_many([
        TermRelation(
            namespace="wip", source_term_id=f"s{i}", target_term_id=f"t{i}",
            relation_type="is_a", status="active",
        )
        for i in range(25)
    ])
    seen: list[tuple[str, str]] = []
    page = 1
    while True:
        resp = await client.get(
            f"{API}/ontology/term-relations/all",
            params={"page": page, "page_size": 7},
            headers=auth_headers,
        )
        items = resp.json()["items"]
        if not items:
            break
        seen.extend((r["source_term_id"], r["target_term_id"]) for r in items)
        page += 1
    assert sorted(seen) == sorted((f"s{i}", f"t{i}") for i in range(25))
    assert len(seen) == len(set(seen))

    explain = await TermRelation.get_motor_collection().find({"status": "active"}) \
        .sort([("_id", SortDirection.ASCENDING)]).limit(7).explain()
    assert all(s not in ("COLLSCAN", "SORT") for s, _ in _stages(explain))


@pytest.mark.asyncio
async def test_superseded_sort_index_is_dropped(client: AsyncClient):
    coll = Term.get_motor_collection()
    await coll.create_index(
        [("namespace", 1), ("terminology_id", 1), ("sort_order", 1)],
        name="ns_terminology_sort_idx",
    )
    await reconcile_term_indexes(Term.get_motor_collection().database)
    names = set(await coll.index_information())
    assert "ns_terminology_sort_idx" not in names
    assert "ns_terminology_sort_value_idx" in names
    # A second run on a database without it is a no-op.
    await reconcile_term_indexes(Term.get_motor_collection().database)


def _raw_term(term_id: str, value: str) -> dict:
    return {"namespace": "wip", "term_id": term_id, "terminology_id": "c1029-dup-voc",
            "value": value, "label": value, "names": [], "status": "active", "sort_order": 0}


@pytest.mark.asyncio
async def test_startup_builds_the_indexes_beanie_skips(client: AsyncClient):
    declared = {ix.document["name"] for ix in Term.Settings.indexes}
    for ix in TERM_INDEXES_BUILT_AT_STARTUP:
        assert ix.document["name"] not in declared

    coll = Term.get_motor_collection()
    assert await reconcile_term_indexes(coll.database) == []
    info = await coll.index_information()
    assert info["ns_terminology_value_unique_idx"].get("unique") is True
    assert "ns_terminology_name_values_idx" in info

    await coll.insert_one(_raw_term("c1029-a", "SAME"))
    with pytest.raises(pymongo.errors.DuplicateKeyError):
        await coll.insert_one(_raw_term("c1029-b", "SAME"))
    # Exact-case and case-variant values stay distinct terms.
    await coll.insert_one(_raw_term("c1029-c", "same"))


@pytest.mark.asyncio
async def test_existing_duplicates_are_reported_not_fatal(client: AsyncClient, caplog):
    coll = Term.get_motor_collection()
    with contextlib.suppress(pymongo.errors.OperationFailure):
        await coll.drop_index("ns_terminology_value_unique_idx")
    await coll.insert_many([_raw_term("c1029-d1", "DUP"), _raw_term("c1029-d2", "DUP")])
    try:
        duplicates = await reconcile_term_indexes(coll.database)
        assert len(duplicates) == 1
        assert sorted(duplicates[0]["term_ids"]) == ["c1029-d1", "c1029-d2"]
        info = await coll.index_information()
        assert "ns_terminology_value_unique_idx" not in info
        assert "ns_terminology_name_values_idx" in info
        assert any(
            r.levelname == "CRITICAL" and "ns_terminology_value_unique_idx" in r.getMessage()
            for r in caplog.records
        )
    finally:
        await coll.delete_many({"terminology_id": "c1029-dup-voc"})
        await reconcile_term_indexes(coll.database)
