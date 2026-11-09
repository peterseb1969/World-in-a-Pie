"""Deleting a terminology is judged by what the delete will actually do.

A mutable terminology is removed outright by any DELETE — hard_delete or
not — so its delete is guarded like every hard delete: templates of ANY
status that reference it refuse it (an inactive template still validates
the documents pinned to it), an unverifiable reference set refuses it, and
force=true does not apply. A template field reaches a terminology three
ways — terminology_ref, array_terminology_ref and a term reference's
target_terminologies — and the guard reads all three. The mutable flag
cannot be switched once the terminology holds a term of any status: a
deprecated or soft-deleted term is still referenced by documents, and a
mutable terminology would hard-delete it.
"""

import pytest
from httpx import AsyncClient

API = "/api/def-store"


async def _terminology(client, auth_headers, value, *, mutable=False):
    response = await client.post(
        f"{API}/terminologies",
        headers=auth_headers,
        json=[{"value": value, "label": value.title(), "namespace": "wip", "mutable": mutable}],
    )
    data = response.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _term(client, auth_headers, terminology_id, value):
    response = await client.post(
        f"{API}/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{"value": value}],
    )
    data = response.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _delete(client, auth_headers, terminology_id, **flags):
    response = await client.request(
        "DELETE", f"{API}/terminologies", headers=auth_headers,
        json=[{"id": terminology_id, **flags}],
    )
    assert response.status_code == 200
    return response.json()


async def _exists(client, auth_headers, terminology_id):
    response = await client.get(f"{API}/terminologies/{terminology_id}", headers=auth_headers)
    return response.status_code == 200


def _template(fields, status="active"):
    return {
        "template_id": "0190c000-0000-7000-0000-000000000002",
        "value": "probe_1175",
        "label": "Probe 1175",
        "status": status,
        "fields": fields,
    }


def _term_ref_field(terminology_id):
    return {"name": "color", "type": "term", "terminology_ref": terminology_id}


def _target_terminologies_field(terminology_id):
    return {
        "name": "shade", "type": "reference", "reference_type": "term",
        "target_terminologies": [terminology_id],
    }


@pytest.mark.asyncio
async def test_plain_delete_of_mutable_counts_inactive_templates(
    client: AsyncClient, auth_headers: dict, template_store
):
    """A plain DELETE removes a mutable terminology for good, so an inactive
    referencing template refuses it, exactly as it refuses hard_delete."""
    tid = await _terminology(client, auth_headers, "probe_mut_inactive", mutable=True)
    await _term(client, auth_headers, tid, "red")
    template_store.templates = [_template([_term_ref_field(tid)], status="inactive")]

    data = await _delete(client, auth_headers, tid)

    assert data["failed"] == 1, data
    assert data["results"][0]["details"]["rule"] == "has_templates"
    assert await _exists(client, auth_headers, tid)


@pytest.mark.asyncio
async def test_force_does_not_apply_to_a_mutable_delete(
    client: AsyncClient, auth_headers: dict, template_store
):
    """force=true means "deactivate despite dependents" — a reversible act.
    A mutable terminology's delete is not reversible, so force cannot skip
    its guard."""
    tid = await _terminology(client, auth_headers, "probe_mut_force", mutable=True)
    template_store.templates = [_template([_term_ref_field(tid)])]

    data = await _delete(client, auth_headers, tid, force=True)

    assert data["failed"] == 1, data
    assert "force=true does not apply" in data["results"][0]["error"]
    assert await _exists(client, auth_headers, tid)


@pytest.mark.asyncio
async def test_force_on_mutable_refused_when_unverifiable(
    client: AsyncClient, auth_headers: dict, template_store
):
    tid = await _terminology(client, auth_headers, "probe_mut_unknown", mutable=True)
    template_store.unreachable = True

    data = await _delete(client, auth_headers, tid, force=True)

    template_store.unreachable = False
    assert data["failed"] == 1, data
    assert data["results"][0]["details"]["rule"] == "dependencies_unverifiable"
    assert await _exists(client, auth_headers, tid)


@pytest.mark.asyncio
async def test_unreferenced_mutable_terminology_still_deletes(
    client: AsyncClient, auth_headers: dict, template_store
):
    """Mutable keeps its meaning: with nothing referencing it, a plain DELETE
    removes the terminology."""
    tid = await _terminology(client, auth_headers, "probe_mut_free", mutable=True)
    await _term(client, auth_headers, tid, "red")

    data = await _delete(client, auth_headers, tid)

    assert data["succeeded"] == 1, data
    assert not await _exists(client, auth_headers, tid)
    # The guard that ran was the any-status one
    assert "status" not in template_store.requests[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{}, {"hard_delete": True}])
async def test_target_terminologies_reference_blocks_delete(
    client: AsyncClient, auth_headers: dict, template_store, flags
):
    """A term reference field's target_terminologies is a reference to the
    terminology like any terminology_ref."""
    tid = await _terminology(client, auth_headers, f"probe_target_{len(flags)}", mutable=True)
    template_store.templates = [_template([_target_terminologies_field(tid)])]

    data = await _delete(client, auth_headers, tid, **flags)

    assert data["failed"] == 1, data
    assert data["results"][0]["details"]["rule"] == "has_templates"
    assert await _exists(client, auth_headers, tid)


@pytest.mark.asyncio
async def test_target_terminologies_reference_blocks_soft_delete(
    client: AsyncClient, auth_headers: dict, template_store
):
    tid = await _terminology(client, auth_headers, "probe_target_soft")
    template_store.templates = [_template([_target_terminologies_field(tid)])]

    data = await _delete(client, auth_headers, tid)

    assert data["failed"] == 1, data
    assert data["results"][0]["details"]["rule"] == "has_templates"


@pytest.mark.asyncio
async def test_dependencies_endpoint_lists_target_terminologies(
    client: AsyncClient, auth_headers: dict, template_store
):
    tid = await _terminology(client, auth_headers, "probe_target_deps")
    template_store.templates = [_template([_target_terminologies_field(tid)])]

    response = await client.get(f"{API}/terminologies/{tid}/dependencies", headers=auth_headers)

    data = response.json()
    assert data["template_count"] == 1, data
    assert data["templates"][0]["field"] == "shade"


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", ["deprecate", "delete"])
async def test_mutable_flag_frozen_while_any_term_exists(
    client: AsyncClient, auth_headers: dict, retire
):
    """Retiring every term does not free the mutable flag: the terms are
    still there, and documents still reference them."""
    tid = await _terminology(client, auth_headers, f"probe_flip_{retire}")
    term_id = await _term(client, auth_headers, tid, "red")
    if retire == "deprecate":
        response = await client.post(
            f"{API}/terms/deprecate", headers=auth_headers,
            json=[{"term_id": term_id, "reason": "retired"}],
        )
    else:
        response = await client.request(
            "DELETE", f"{API}/terms", headers=auth_headers, json=[{"id": term_id}],
        )
    assert response.json()["succeeded"] == 1, response.json()

    response = await client.put(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"terminology_id": tid, "mutable": True}],
    )

    data = response.json()
    assert data["failed"] == 1, data
    assert data["results"][0]["details"]["rule"] == "has_terms"
    detail = (await client.get(f"{API}/terminologies/{tid}", headers=auth_headers)).json()
    assert detail["mutable"] is False
