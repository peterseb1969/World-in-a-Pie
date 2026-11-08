"""The Registry tells other services' caches about entries that lost names or
are gone: a hard or soft delete, released synonyms, a removed synonym, a merge.
Dry runs, failures and untouched entries announce nothing."""

import pytest
from httpx import AsyncClient

import registry.api.entries as entries_api
import registry.api.synonyms as synonyms_api

NS = "default"


@pytest.fixture
def announced(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def record(entry_ids):
        seen.extend(entry_ids)

    monkeypatch.setattr(entries_api, "entries_changed", record)
    monkeypatch.setattr(synonyms_api, "entries_changed", record)
    return seen


async def _post(client, auth_headers, path, body, params=None) -> dict:
    r = await client.post(f"/api/registry{path}", json=body, headers=auth_headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def _entry(client, auth_headers, key: str, namespace: str = NS, synonym=None) -> str:
    item = {
        "namespace": namespace, "entity_type": "documents",
        "composite_key": {"ns": namespace, "template_id": "TPL-ANN", "k": key},
    }
    data = await _post(client, auth_headers, "/entries/register", [item])
    entry_id = data["results"][0]["registry_id"]
    if synonym is not None:
        added = await _post(client, auth_headers, "/synonyms/add", [{
            "target_id": entry_id, "synonym_namespace": namespace,
            "synonym_entity_type": "documents", "synonym_composite_key": synonym,
        }])
        assert added["results"][0]["status"] == "added"
    return entry_id


async def _delete(client, auth_headers, items) -> dict:
    r = await client.request(
        "DELETE", "/api/registry/entries", json=items, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_soft_delete_announces(client: AsyncClient, auth_headers: dict, announced):
    entry_id = await _entry(client, auth_headers, "soft")
    out = await _delete(client, auth_headers, [{"entry_id": entry_id}])
    assert out["results"][0]["status"] == "deactivated"
    assert announced == [entry_id]


async def test_a_hard_delete_announces(client: AsyncClient, auth_headers: dict, announced):
    await client.post(
        "/api/registry/namespaces",
        json={"prefix": "ann-hard", "deletion_mode": "full"}, headers=auth_headers,
    )
    entry_id = await _entry(client, auth_headers, "hard", namespace="ann-hard")
    out = await _delete(client, auth_headers, [{"entry_id": entry_id, "hard_delete": True}])
    assert out["results"][0]["status"] == "deleted"
    assert announced == [entry_id]


async def test_a_refused_delete_announces_nothing(client: AsyncClient, auth_headers: dict, announced):
    entry_id = await _entry(client, auth_headers, "refused")  # default namespace retains
    out = await _delete(client, auth_headers, [
        {"entry_id": entry_id, "hard_delete": True}, {"entry_id": "no-such-entry"},
    ])
    assert [r["status"] for r in out["results"]] == ["error", "not_found"]
    assert announced == []


async def test_releasing_synonyms_announces(client: AsyncClient, auth_headers: dict, announced):
    entry_id = await _entry(client, auth_headers, "retire", synonym={"erp": "ANN-1"})
    dry = await _post(client, auth_headers, "/entries/deactivate", [{"entry_id": entry_id}],
                      params={"dry_run": "true"})
    assert dry["results"][0]["status"] == "deactivated"
    assert announced == []  # a dry run changes nothing

    await _post(client, auth_headers, "/entries/deactivate", [{"entry_id": entry_id}])
    assert announced == [entry_id]

    again = await _post(client, auth_headers, "/entries/deactivate", [{"entry_id": entry_id}])
    assert again["results"][0]["status"] == "unchanged"
    assert announced == [entry_id]  # nothing left to release


async def test_removing_a_synonym_announces(client: AsyncClient, auth_headers: dict, announced):
    entry_id = await _entry(client, auth_headers, "remove", synonym={"erp": "ANN-2"})
    out = await _post(client, auth_headers, "/synonyms/remove", [
        {"target_id": entry_id, "synonym_composite_key": {"erp": "ANN-2"}},
        {"target_id": entry_id, "synonym_composite_key": {"erp": "never-there"}},
    ])
    assert [r["status"] for r in out["results"]] == ["removed", "not_found"]
    assert announced == [entry_id]


async def test_a_merge_announces_the_deprecated_entry(client: AsyncClient, auth_headers: dict, announced):
    preferred = await _entry(client, auth_headers, "preferred")
    deprecated = await _entry(client, auth_headers, "deprecated", synonym={"erp": "ANN-3"})
    out = await _post(client, auth_headers, "/synonyms/merge", [
        {"preferred_id": preferred, "deprecated_id": deprecated},
    ])
    assert out["results"][0]["status"] == "merged"
    assert announced == [deprecated]
