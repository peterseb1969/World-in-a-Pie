"""Every lifecycle change of a term or terminology is in the audit trail.

The audit actions are created, updated, deprecated, deleted, restored and
merged, and the action filter accepts only those. Every entry names the
namespace it was recorded in. The id-scoped audit doors read only the
entity's own namespace, and check read on it even after the entity is gone
— a hard-deleted term's history stays behind its namespace's read gate.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from def_store.models.audit_log import TermAuditLog

API = "/api/def-store"


async def _terminology(client, auth_headers, value, *, mutable=False):
    response = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"value": value, "label": value.title(), "namespace": "wip", "mutable": mutable}],
    )
    data = response.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _terms(client, auth_headers, terminology_id, *values):
    response = await client.post(
        f"{API}/terminologies/{terminology_id}/terms", headers=auth_headers,
        json=[{"value": v} for v in values],
    )
    data = response.json()
    assert data["succeeded"] == len(values), data
    return [r["id"] for r in data["results"]]


async def _term_audit(client, auth_headers, term_id, **params):
    response = await client.get(f"{API}/audit/terms/{term_id}", headers=auth_headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _terminology_audit(client, auth_headers, terminology_id, **params):
    response = await client.get(
        f"{API}/audit/terminologies/{terminology_id}", headers=auth_headers, params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _actions(items):
    return [e["action"] for e in items]


@pytest.mark.asyncio
async def test_deprecate_is_audited(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "probe_1181_dep")
    old, new = await _terms(client, auth_headers, tid, "old", "new")

    response = await client.post(
        f"{API}/terms/deprecate", headers=auth_headers,
        json=[{"term_id": old, "reason": "renamed", "replaced_by_term_id": new}],
    )
    assert response.json()["succeeded"] == 1

    entry = (await _term_audit(client, auth_headers, old))[0]
    assert entry["action"] == "deprecated"
    assert entry["previous_values"]["status"] == "active"
    assert entry["new_values"] == {
        "status": "deprecated", "deprecated_reason": "renamed", "replaced_by_term_id": new,
    }
    assert entry["namespace"] == "wip"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutable", [False, True])
async def test_delete_term_is_audited(client: AsyncClient, auth_headers: dict, mutable):
    tid = await _terminology(client, auth_headers, f"probe_1181_del_{mutable}", mutable=mutable)
    (term_id,) = await _terms(client, auth_headers, tid, "gone")

    response = await client.request(
        "DELETE", f"{API}/terms", headers=auth_headers, json=[{"id": term_id}],
    )
    assert response.json()["succeeded"] == 1

    entry = (await _term_audit(client, auth_headers, term_id))[0]
    assert entry["action"] == "deleted"
    assert entry["new_values"] == ({"hard_delete": True} if mutable else {"status": "inactive"})


@pytest.mark.asyncio
async def test_merge_is_audited(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "probe_1181_merge")
    survivor, absorbed = await _terms(client, auth_headers, tid, "keep", "fold")

    response = await client.post(
        f"{API}/terms/merge", headers=auth_headers,
        json=[{"survivor_term_id": survivor, "absorbed_term_id": absorbed}],
    )
    assert response.json()["succeeded"] == 1, response.json()

    absorbed_log = {e["action"]: e for e in await _term_audit(client, auth_headers, absorbed)}
    assert {"created", "deprecated", "merged"} <= set(absorbed_log), absorbed_log
    assert absorbed_log["merged"]["new_values"] == {"merged_into": survivor, "names_carried": ["fold"]}
    assert "updated" in _actions(await _term_audit(client, auth_headers, survivor))


@pytest.mark.asyncio
async def test_terminology_delete_and_restore_are_audited(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "probe_1181_life")
    await _terms(client, auth_headers, tid, "a", "b")

    response = await client.request(
        "DELETE", f"{API}/terminologies", headers=auth_headers,
        json=[{"id": tid, "force": True}],
    )
    assert response.json()["succeeded"] == 1
    response = await client.post(f"{API}/terminologies/{tid}/restore", headers=auth_headers)
    assert response.status_code == 200

    log = {e["action"]: e for e in await _terminology_audit(client, auth_headers, tid) if e["term_id"] == tid}
    restored, deleted = log["restored"], log["deleted"]
    assert restored["new_values"] == {"status": "active", "terms_reactivated": 2}
    assert deleted["action"] == "deleted"
    assert deleted["new_values"] == {"status": "inactive", "terms_deactivated": 2}
    # The filter finds them
    assert _actions(await _terminology_audit(client, auth_headers, tid, action="deleted")) == ["deleted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["recent", "terminology"])
async def test_action_filter_accepts_only_real_actions(
    client: AsyncClient, auth_headers: dict, door
):
    tid = await _terminology(client, auth_headers, f"probe_1181_filter_{door}")
    url = f"{API}/audit" if door == "recent" else f"{API}/audit/terminologies/{tid}"

    response = await client.get(url, headers=auth_headers, params={"action": "nonexistent_action"})

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_terminology_door_reads_only_its_namespace(client: AsyncClient, auth_headers: dict):
    """A terminology_id is unique per namespace only; another namespace's
    rows under the same id are not this terminology's history."""
    tid = await _terminology(client, auth_headers, "probe_1181_ns")
    await TermAuditLog(
        namespace="elsewhere", term_id="t-elsewhere", terminology_id=tid,
        action="updated", changed_fields=["label"],
    ).insert()

    log = await _terminology_audit(client, auth_headers, tid)

    assert {e["namespace"] for e in log} == {"wip"}
    assert "t-elsewhere" not in {e["term_id"] for e in log}


@pytest.mark.asyncio
async def test_hard_deleted_term_history_stays_behind_read_gate(
    client: AsyncClient, auth_headers: dict
):
    tid = await _terminology(client, auth_headers, "probe_1181_gate", mutable=True)
    (term_id,) = await _terms(client, auth_headers, tid, "gone")
    response = await client.request(
        "DELETE", f"{API}/terms", headers=auth_headers, json=[{"id": term_id}],
    )
    assert response.json()["succeeded"] == 1

    async def deny(identity, namespace, level):
        raise HTTPException(404, "Namespace not found")

    with patch("def_store.api.audit.check_namespace_permission", side_effect=deny):
        response = await client.get(f"{API}/audit/terms/{term_id}", headers=auth_headers)

    assert response.status_code == 404, response.text
