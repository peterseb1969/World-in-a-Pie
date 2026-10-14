"""The terminology-delete dependency guard fails closed (CASE-886).

The guard calls Template Store to refuse deleting a terminology that
templates still reference. It used to fail open two ways: an unreachable
Template Store collapsed to template_count -1 (and ``-1 > 0`` is false, so
the delete proceeded), and a non-200 answer collapsed to an empty template
list — count 0, indistinguishable from "no dependencies". Now:

- a non-forced soft delete is refused when references exist OR when the
  check cannot complete (force=true overrides both);
- a hard-delete is refused when templates of ANY status reference the
  terminology or when that cannot be verified — force does not apply;
- GET /dependencies reports an incomplete check as template_count -1 with
  has_dependencies null, never false.

The fake Template Store these tests steer lives in conftest.py
(``template_store`` fixture), so the guard's real HTTP/counting code runs.
"""

import pytest
from httpx import AsyncClient


async def _create_terminology(client, auth_headers, value):
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": value, "label": value.title(), "namespace": "wip"}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1, f"Failed to create terminology: {data}"
    return data["results"][0]["id"]


async def _delete(client, auth_headers, terminology_id, *, force=False, hard_delete=False):
    response = await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"id": terminology_id, "force": force, "hard_delete": hard_delete}],
    )
    assert response.status_code == 200
    return response.json()


async def _status_of(client, auth_headers, terminology_id):
    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}", headers=auth_headers
    )
    assert response.status_code == 200
    return response.json()["status"]


def _referencing_template(terminology_id, status="active"):
    """A template dict as the list endpoint serves it, with one term field
    pointing at the terminology under test."""
    return {
        "template_id": "0190c000-0000-7000-0000-000000000001",
        "value": "probe_item",
        "label": "Probe Item",
        "status": status,
        "fields": [{"name": "color", "type": "term", "terminology_ref": terminology_id}],
    }


@pytest.mark.asyncio
async def test_soft_delete_refused_when_template_store_unreachable(
    client: AsyncClient, auth_headers: dict, template_store
):
    """An unverifiable count refuses the non-forced delete — the -1 sentinel
    must never sail past a ``> 0`` comparison."""
    tid = await _create_terminology(client, auth_headers, "probe_unreachable")
    template_store.unreachable = True

    data = await _delete(client, auth_headers, tid)

    assert data["failed"] == 1 and data["succeeded"] == 0
    assert "could not verify" in data["results"][0]["error"].lower()
    assert "force=true" in data["results"][0]["error"]

    template_store.unreachable = False
    assert await _status_of(client, auth_headers, tid) == "active"


@pytest.mark.asyncio
async def test_soft_delete_refused_when_template_store_refuses(
    client: AsyncClient, auth_headers: dict, template_store
):
    """A non-200 answer (stale key → 401) is an UNKNOWN count, not an empty
    one — it used to collapse to 0 and let the delete through silently."""
    tid = await _create_terminology(client, auth_headers, "probe_401")
    template_store.http_status = 401

    data = await _delete(client, auth_headers, tid)

    assert data["failed"] == 1
    assert "could not verify" in data["results"][0]["error"].lower()

    template_store.http_status = 200
    assert await _status_of(client, auth_headers, tid) == "active"


@pytest.mark.asyncio
async def test_soft_delete_refused_when_referenced(
    client: AsyncClient, auth_headers: dict, template_store
):
    """With Template Store reachable, a referencing template blocks the
    delete — the path the case reproduced as never having worked live."""
    tid = await _create_terminology(client, auth_headers, "probe_referenced")
    template_store.templates = [_referencing_template(tid)]

    data = await _delete(client, auth_headers, tid)

    assert data["failed"] == 1
    assert "dependent templates" in data["results"][0]["error"].lower()
    assert await _status_of(client, auth_headers, tid) == "active"


@pytest.mark.asyncio
async def test_force_overrides_unverifiable_check(
    client: AsyncClient, auth_headers: dict, template_store
):
    """force=true keeps its meaning for the reversible soft delete: it
    proceeds even when the check cannot complete."""
    tid = await _create_terminology(client, auth_headers, "probe_force")
    template_store.unreachable = True

    data = await _delete(client, auth_headers, tid, force=True)

    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "deleted"

    template_store.unreachable = False
    assert await _status_of(client, auth_headers, tid) == "inactive"


@pytest.mark.asyncio
async def test_hard_delete_refused_on_unknown_even_with_force(
    client: AsyncClient, auth_headers: dict, template_store
):
    """Hard-delete is irreversible, so an unverifiable reference set refuses
    it outright — force does not apply."""
    tid = await _create_terminology(client, auth_headers, "probe_hard_unknown")
    template_store.unreachable = True

    data = await _delete(client, auth_headers, tid, force=True, hard_delete=True)

    assert data["failed"] == 1
    error = data["results"][0]["error"]
    assert "hard-delete refused" in error.lower()
    assert "force=true does not apply" in error.lower()

    template_store.unreachable = False
    assert await _status_of(client, auth_headers, tid) == "active"


@pytest.mark.asyncio
async def test_hard_delete_counts_templates_of_any_status(
    client: AsyncClient, auth_headers: dict, template_store
):
    """An INACTIVE template still validates the documents pinned to it, so
    it blocks a hard-delete — while the soft delete, judged on active
    references only, proceeds."""
    tid = await _create_terminology(client, auth_headers, "probe_inactive_ref")
    template_store.templates = [_referencing_template(tid, status="inactive")]

    hard = await _delete(client, auth_headers, tid, hard_delete=True)
    assert hard["failed"] == 1
    assert "hard-delete refused" in hard["results"][0]["error"].lower()
    assert "1 template" in hard["results"][0]["error"]
    # The hard check queried without a status filter; the inactive row counted.
    assert "status" not in template_store.requests[-1]

    soft = await _delete(client, auth_headers, tid)
    assert soft["succeeded"] == 1
    # The soft check filtered to active templates and saw none.
    assert template_store.requests[-1].get("status") == "active"


@pytest.mark.asyncio
async def test_dependencies_endpoint_reports_unknown(
    client: AsyncClient, auth_headers: dict, template_store
):
    """GET /dependencies reports an incomplete check as unknown
    (template_count -1, has_dependencies null) — never as 'no dependencies'."""
    tid = await _create_terminology(client, auth_headers, "probe_deps_unknown")

    for failure in ("unreachable", "http_401"):
        if failure == "unreachable":
            template_store.unreachable = True
        else:
            template_store.unreachable = False
            template_store.http_status = 401

        response = await client.get(
            f"/api/def-store/terminologies/{tid}/dependencies", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["template_count"] == -1, failure
        assert data["has_dependencies"] is None, failure
        assert "unavailable" in data["warning_message"].lower(), failure
