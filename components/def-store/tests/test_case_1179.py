"""An export → import round trip keeps what the export carried, and an
import refuses what it cannot carry out whole.

- Each term's lifecycle comes back: a deprecated term is deprecated again,
  with its reason and its replacement, and an inactive term is inactive.
- A parent and a replacement travel by value and are resolved inside the
  imported terminology, so a hierarchy survives the fresh ids an import
  mints.
- The terminology's mutable and unique_names flags travel both ways.
- Every relation the import cannot create is counted, with a reason.
- An import into a retired terminology is refused, pointing at restore.
- Every term item is checked before anything is written.
- A CSV whose rows parse to no term is refused.
- The URL import takes a destination namespace.
- No import door accepts a created_by: the record carries the caller.
"""

import pytest
from httpx import AsyncClient

from def_store.models.term import Term
from def_store.models.terminology import Terminology

API = "/api/def-store"


async def _terminology(client, auth_headers, value, **flags):
    response = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"value": value, "label": value.title(), "namespace": "wip", **flags}],
    )
    data = response.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _terms(client, auth_headers, terminology_id, *items):
    response = await client.post(
        f"{API}/terminologies/{terminology_id}/terms", headers=auth_headers,
        json=[i if isinstance(i, dict) else {"value": i} for i in items],
    )
    data = response.json()
    assert data["succeeded"] == len(items), data
    return [r["id"] for r in data["results"]]


async def _export(client, auth_headers, terminology_id, **params):
    response = await client.get(
        f"{API}/import-export/export/{terminology_id}", headers=auth_headers,
        params={"include_inactive": "true", **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _import(client, auth_headers, body, **params):
    return await client.post(
        f"{API}/import-export/import", headers=auth_headers,
        params={"namespace": "wip", **params}, json=body,
    )


async def _imported_terms(value):
    terminology = await Terminology.find_one({"value": value, "namespace": "wip"})
    assert terminology is not None
    return {t.value: t for t in await Term.find({"terminology_id": terminology.terminology_id}).to_list()}


def _renamed(export, value):
    export["terminology"]["value"] = value
    export["terminology"]["label"] = value.title()
    return export


@pytest.mark.asyncio
async def test_lifecycle_round_trips(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "PROBE_1179_LIFE")
    keep, old, gone = await _terms(client, auth_headers, tid, "keep", "old", "gone")
    response = await client.post(
        f"{API}/terms/deprecate", headers=auth_headers,
        json=[{"term_id": old, "reason": "superseded", "replaced_by_term_id": keep}],
    )
    assert response.json()["succeeded"] == 1, response.text
    response = await client.request(
        "DELETE", f"{API}/terms", headers=auth_headers, json=[{"id": gone}],
    )
    assert response.json()["succeeded"] == 1, response.text

    export = _renamed(await _export(client, auth_headers, tid), "PROBE_1179_LIFE_COPY")
    response = await _import(client, auth_headers, export)
    assert response.status_code == 200, response.text

    terms = await _imported_terms("PROBE_1179_LIFE_COPY")
    assert terms["keep"].status == "active"
    assert terms["old"].status == "deprecated"
    assert terms["old"].deprecated_reason == "superseded"
    assert terms["old"].replaced_by_term_id == terms["keep"].term_id
    assert terms["gone"].status == "inactive"


@pytest.mark.asyncio
async def test_hierarchy_round_trips(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "PROBE_1179_TREE")
    (root,) = await _terms(client, auth_headers, tid, "root")
    (mid,) = await _terms(client, auth_headers, tid, {"value": "mid", "parent_term_id": root})
    await _terms(client, auth_headers, tid, {"value": "leaf", "parent_term_id": mid})

    export = _renamed(await _export(client, auth_headers, tid), "PROBE_1179_TREE_COPY")
    # Children before their parents in the file: the import orders them.
    export["terms"].reverse()
    response = await _import(client, auth_headers, export)
    assert response.status_code == 200, response.text
    assert response.json()["terms_result"]["failed"] == 0, response.json()

    terms = await _imported_terms("PROBE_1179_TREE_COPY")
    assert terms["mid"].parent_term_id == terms["root"].term_id
    assert terms["leaf"].parent_term_id == terms["mid"].term_id


@pytest.mark.asyncio
async def test_terminology_flags_round_trip(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "PROBE_1179_FLAGS", mutable=True, unique_names=True)
    await _terms(client, auth_headers, tid, "a")

    export = _renamed(await _export(client, auth_headers, tid), "PROBE_1179_FLAGS_COPY")
    assert export["terminology"]["mutable"] is True
    assert export["terminology"]["unique_names"] is True
    response = await _import(client, auth_headers, export)
    assert response.status_code == 200, response.text

    copy = await Terminology.find_one({"value": "PROBE_1179_FLAGS_COPY", "namespace": "wip"})
    assert copy.mutable is True
    assert copy.unique_names is True


@pytest.mark.asyncio
async def test_every_relation_not_created_is_counted(client: AsyncClient, auth_headers: dict):
    body = {
        "terminology": {"value": "PROBE_1179_RELS", "label": "Rels"},
        "terms": [{"value": "a"}, {"value": "b"}],
        "relations": [
            {"source_term_value": "a", "target_term_value": "b", "relation_type": "is_a"},
            {"source_term_value": "a", "target_term_value": "nowhere", "relation_type": "is_a"},
            {"source_term_value": "a", "target_term_value": "x",
             "target_terminology_id": "019a0000-0000-7000-8000-000000000000", "relation_type": "is_a"},
        ],
    }
    response = await _import(client, auth_headers, body)
    assert response.status_code == 200, response.text
    rels = response.json()["relations_result"]
    assert rels["created"] == 1, rels
    assert rels["dropped"] == 2, rels
    assert rels["created"] + rels["skipped"] + rels["errors"] + rels["dropped"] == rels["total"], rels
    assert len(rels["dropped_samples"]) == 2, rels


@pytest.mark.asyncio
async def test_import_into_a_retired_terminology_is_refused(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "PROBE_1179_RETIRED")
    await _terms(client, auth_headers, tid, "old")
    response = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers, json=[{"id": tid}])
    assert response.json()["succeeded"] == 1, response.text

    body = {"terminology": {"value": "PROBE_1179_RETIRED", "label": "R"}, "terms": [{"value": "new"}]}
    response = await _import(client, auth_headers, body)
    assert response.status_code == 400, response.text
    assert "restore" in response.json()["detail"].lower(), response.text
    assert await Term.find_one({"terminology_id": tid, "value": "new"}) is None


@pytest.mark.asyncio
async def test_a_malformed_term_item_writes_nothing(client: AsyncClient, auth_headers: dict):
    body = {
        "terminology": {"value": "PROBE_1179_MALFORMED", "label": "M"},
        "terms": [{"value": "fine"}, {"label": "no value"}],
    }
    response = await _import(client, auth_headers, body)
    assert response.status_code == 400, response.text
    assert await Terminology.find_one({"value": "PROBE_1179_MALFORMED"}) is None


@pytest.mark.asyncio
async def test_a_csv_that_parses_to_no_term_is_refused(client: AsyncClient, auth_headers: dict):
    body = {
        "terminology_value": "PROBE_1179_CSV",
        "terminology_label": "Csv",
        "csv_content": "code,name\nA,Alpha\nB,Beta\n",
    }
    response = await _import(client, auth_headers, body, format="csv")
    assert response.status_code == 400, response.text
    assert "code" in response.json()["detail"], response.text
    assert await Terminology.find_one({"value": "PROBE_1179_CSV"}) is None


@pytest.mark.asyncio
async def test_csv_status_column_is_honoured(client: AsyncClient, auth_headers: dict):
    body = {
        "terminology_value": "PROBE_1179_CSV_STATUS",
        "terminology_label": "Csv",
        "csv_content": "value,status\nlive,active\nold,deprecated\ngone,inactive\n",
    }
    response = await _import(client, auth_headers, body, format="csv")
    assert response.status_code == 200, response.text
    terms = await _imported_terms("PROBE_1179_CSV_STATUS")
    assert {v: t.status for v, t in terms.items()} == {
        "live": "active", "old": "deprecated", "gone": "inactive",
    }


@pytest.mark.asyncio
async def test_url_csv_import_takes_a_namespace(client: AsyncClient, auth_headers: dict, monkeypatch):
    import httpx

    async def fake_get(self, url, *args, **kwargs):
        return httpx.Response(200, text="value,label\nA,Alpha\n", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    response = await client.post(
        f"{API}/import-export/import/url", headers=auth_headers,
        params={
            "url": "http://example.invalid/vocab.csv", "format": "csv", "namespace": "wip",
            "terminology_value": "PROBE_1179_URL", "terminology_label": "Url",
        },
    )
    assert response.status_code == 200, response.text
    terminology = await Terminology.find_one({"value": "PROBE_1179_URL"})
    assert terminology is not None and terminology.namespace == "wip"


@pytest.mark.asyncio
async def test_no_import_door_accepts_created_by(client: AsyncClient):
    from def_store.main import app

    paths = app.openapi()["paths"]
    for path in (
        "/api/def-store/import-export/import",
        "/api/def-store/import-export/import-ontology",
        "/api/def-store/import-export/import/url",
    ):
        params = {p["name"] for p in paths[path]["post"].get("parameters", [])}
        assert "created_by" not in params, path
