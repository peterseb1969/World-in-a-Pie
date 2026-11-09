"""The reporting relation listing, pinned before list_tables' refactor.

Provenance: CASE-1161. The suite never reached these branches: the
namespace filter skipping another schema's relations, and the entity
grouping — version tables summed under their entity, the __entities view,
the bare default view, a legacy bare-name table, and later relations
joining an entity already started. Also pinned: detail mode (one relation,
its columns, no entities) and its 404.
"""

from unittest.mock import AsyncMock

import pytest

from .test_case_645 import http_client, mock_state  # noqa: F401  (fixtures)

CATALOG = [
    ("ns1", "doc_old", "BASE TABLE"),
    ("ns1", "doc_visit", "VIEW"),
    ("ns1", "doc_visit__entities", "VIEW"),
    ("ns1", "doc_visit__v1", "BASE TABLE"),
    ("ns1", "doc_visit__v2", "BASE TABLE"),
    ("ns1", "terms", "BASE TABLE"),
    ("ns1", "_wip_internal", "BASE TABLE"),
    ("ns2", "doc_visit__v1", "BASE TABLE"),
]
COUNTS = {"doc_old": 4, "doc_visit": 5, "doc_visit__entities": 5, "doc_visit__v1": 2, "doc_visit__v2": 3, "terms": 7}


def _wire(conn):
    async def fetch(sql, *params):
        if "information_schema.tables" in sql:
            return [{"table_schema": s, "table_name": t, "table_type": k} for s, t, k in CATALOG]
        _schema, table = params
        return [{"column_name": "document_id", "data_type": "text", "is_nullable": "NO"},
                {"column_name": f"{table}_col", "data_type": "integer", "is_nullable": "YES"}]

    async def fetchval(sql, timeout=None):
        return COUNTS[sql.split('"')[3]]

    conn.fetch = AsyncMock(side_effect=fetch)
    conn.fetchval = AsyncMock(side_effect=fetchval)


@pytest.mark.asyncio
async def test_listing_groups_relations_under_their_entities(http_client, mock_state):  # noqa: F811
    _wire(mock_state[1])
    async with http_client:
        body = (await http_client.get("/api/reporting-sync/tables", params={"namespace": "ns1"})).json()

    assert [(t["name"], t["kind"], t["row_count"], t["column_count"]) for t in body["tables"]] == [
        ("doc_old", "table", 4, 2), ("doc_visit", "view", 5, 2), ("doc_visit__entities", "view", 5, 2),
        ("doc_visit__v1", "table", 2, 2), ("doc_visit__v2", "table", 3, 2), ("terms", "table", 7, 2),
    ]
    assert body["tables"][0]["template_value"] == "old" and body["tables"][5]["template_value"] is None
    assert body["tables"][0]["qualified_name"] == '"ns1"."doc_old"'
    assert body["entities"] == [
        {"namespace": "ns1", "entity": "old", "default_view": "doc_old", "default_view_present": False,
         "entities_view": "doc_old__entities", "legacy_table": True, "versions": [], "row_count": 4},
        {"namespace": "ns1", "entity": "visit", "default_view": "doc_visit", "default_view_present": True,
         "entities_view": "doc_visit__entities", "legacy_table": False,
         "versions": [{"version": 1, "table": "doc_visit__v1", "row_count": 2},
                      {"version": 2, "table": "doc_visit__v2", "row_count": 3}],
         "row_count": 5},
    ]


@pytest.mark.asyncio
async def test_detail_mode_lists_one_relations_columns(http_client, mock_state):  # noqa: F811
    _wire(mock_state[1])
    async with http_client:
        body = (await http_client.get("/api/reporting-sync/tables", params={"table_name": "doc_visit__v1"})).json()
    assert "entities" not in body
    assert [(t["namespace"], t["columns"]) for t in body["tables"]] == [
        (ns, [{"name": "document_id", "type": "text", "nullable": False},
              {"name": "doc_visit__v1_col", "type": "integer", "nullable": True}])
        for ns in ("ns1", "ns2")
    ]


@pytest.mark.asyncio
async def test_detail_mode_unknown_relation_is_404(http_client, mock_state):  # noqa: F811
    _wire(mock_state[1])
    async with http_client:
        response = await http_client.get("/api/reporting-sync/tables", params={"table_name": "doc_nope"})
    assert response.status_code == 404
