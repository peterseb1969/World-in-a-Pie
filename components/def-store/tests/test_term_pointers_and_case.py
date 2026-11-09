"""Term pointers must resolve, and values compare as their terminology says.

parent_term_id and replaced_by_term_id name an existing, ACTIVE term of the
same terminology, never the term itself — on every write door (single and
bulk create, update, deprecate, reconcile). A case-insensitive terminology
holds one of `Foo` and `foo`, never both; where old data holds both,
validation lists every bearer instead of silently picking one.
"""

import uuid

import pytest
from httpx import AsyncClient

from def_store.models.term import Term
from def_store.services.terminology_service import TerminologyService
from registry.models.entry import RegistryEntry

TERMS = "/api/def-store/terminologies"


async def _terminology(client, auth_headers, *, case_sensitive=False) -> tuple[str, str]:
    value = f"PTR_{uuid.uuid4().hex[:8].upper()}"
    resp = await client.post(TERMS, headers=auth_headers, json=[{
        "value": value, "label": value, "namespace": "wip", "case_sensitive": case_sensitive,
    }])
    return resp.json()["results"][0]["id"], value


async def _create(client, auth_headers, tid, items) -> list[dict]:
    resp = await client.post(f"{TERMS}/{tid}/terms", headers=auth_headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


async def _id(client, auth_headers, tid, value) -> str:
    (row,) = await _create(client, auth_headers, tid, [{"value": value}])
    assert row["status"] == "created", row
    return row["id"]


async def _deprecate(client, auth_headers, term_id, replaced_by=None) -> dict:
    body = {"term_id": term_id, "reason": "test"}
    if replaced_by is not None:
        body["replaced_by_term_id"] = replaced_by
    resp = await client.post("/api/def-store/terms/deprecate", headers=auth_headers, json=[body])
    return resp.json()["results"][0]


def _refused(row: dict, rule: str) -> None:
    assert row["status"] == "error", row
    assert row["error_code"] == "reference_violation", row
    assert (row.get("details") or {}).get("rule") == rule, row


# --------------------------------------------------------------------------
# parent_term_id
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_create_refuses_a_parent_that_is_not_an_active_term_here(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers)
    other_tid, _ = await _terminology(client, auth_headers)
    foreign = await _id(client, auth_headers, other_tid, "foreign")
    retired = await _id(client, auth_headers, tid, "retired")
    assert (await _deprecate(client, auth_headers, retired))["status"] == "updated"

    for parent, rule in (
        (str(uuid.uuid4()), "term_not_found"),
        (foreign, "other_terminology"),
        (retired, "not_active"),
    ):
        (row,) = await _create(client, auth_headers, tid, [{"value": f"child-{rule}", "parent_term_id": parent}])
        _refused(row, rule)
    assert await Term.find({"terminology_id": tid, "value": {"$regex": "^child-"}}).count() == 0


@pytest.mark.asyncio
async def test_a_parent_resolves_by_id_or_by_this_terminologys_value(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers)
    animal = await _id(client, auth_headers, tid, "Animal")
    rows = await _create(client, auth_headers, tid, [
        {"value": "Cat", "parent_term_id": animal},
        {"value": "Dog", "parent_term_id": "Animal"},
    ])
    assert [r["status"] for r in rows] == ["created", "created"], rows
    stored = {t.value: t.parent_term_id for t in await Term.find({"terminology_id": tid}).to_list()}
    assert stored["Cat"] == animal and stored["Dog"] == animal


@pytest.mark.asyncio
async def test_bulk_create_refuses_bad_parents_per_item_and_mints_no_entry_for_them(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers)
    animal = await _id(client, auth_headers, tid, "Animal")
    rows = await _create(client, auth_headers, tid, [
        {"value": "Cat", "parent_term_id": animal},
        {"value": "Ghost", "parent_term_id": str(uuid.uuid4())},
    ])
    assert rows[0]["status"] == "created", rows
    _refused(rows[1], "term_not_found")
    assert await RegistryEntry.find({
        "entity_type": "terms", "primary_composite_key.value": "Ghost",
    }).count() == 0


@pytest.mark.asyncio
async def test_update_refuses_the_term_as_its_own_parent(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers)
    cat = await _id(client, auth_headers, tid, "Cat")
    resp = await client.put("/api/def-store/terms", headers=auth_headers,
                            json=[{"term_id": cat, "parent_term_id": cat}])
    _refused(resp.json()["results"][0], "self_reference")
    assert (await Term.find_one({"term_id": cat})).parent_term_id is None


# --------------------------------------------------------------------------
# replaced_by_term_id
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deprecate_refuses_a_successor_that_is_not_an_active_term_here(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers)
    other_tid, _ = await _terminology(client, auth_headers)
    old = await _id(client, auth_headers, tid, "old")
    foreign = await _id(client, auth_headers, other_tid, "foreign")
    gone = await _id(client, auth_headers, tid, "gone")
    assert (await _deprecate(client, auth_headers, gone))["status"] == "updated"

    for successor, rule in (
        (old, "self_reference"),
        (str(uuid.uuid4()), "term_not_found"),
        (foreign, "other_terminology"),
        (gone, "not_active"),
    ):
        _refused(await _deprecate(client, auth_headers, old, successor), rule)
    still = await Term.find_one({"term_id": old})
    assert still.status == "active" and still.replaced_by_term_id is None

    new = await _id(client, auth_headers, tid, "new")
    assert (await _deprecate(client, auth_headers, old, new))["status"] == "updated"
    assert (await Term.find_one({"term_id": old})).replaced_by_term_id == new


@pytest.mark.asyncio
async def test_reconcile_never_reports_a_successor_it_did_not_write(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    tid, _ = await _terminology(client, auth_headers)
    await _id(client, auth_headers, tid, "RETIRE")
    real = TerminologyService.create_terms_bulk

    async def successor_create_fails(*args, **kwargs):
        rows = await real(*args, **kwargs)
        for row in rows:
            if row.value == "NEW" and row.status == "created":
                await Term.find({"term_id": row.id}).delete()
                row.status, row.error, row.error_code = "error", "simulated", "internal_error"
        return rows

    monkeypatch.setattr(TerminologyService, "create_terms_bulk", staticmethod(successor_create_fails))
    resp = await client.post(f"{TERMS}/{tid}/reconcile", headers=auth_headers, json={
        "terms": [{"value": "NEW"}, {"value": "OTHER"}], "dry_run": False,
        "on_missing": "deprecate", "successors": {"RETIRE": "NEW"},
    })
    assert resp.status_code == 200, resp.text
    retire = next(r for r in resp.json()["results"] if r["value"] == "RETIRE")
    assert retire["status"] == "error" and retire["error_code"] == "unknown_successor", retire
    assert (await Term.find_one({"terminology_id": tid, "value": "RETIRE"})).status == "active"


# --------------------------------------------------------------------------
# Case-insensitive values
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_case_insensitive_terminology_holds_one_of_foo_and_foo(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers)
    foo = await _id(client, auth_headers, tid, "Foo")

    (single,) = await _create(client, auth_headers, tid, [{"value": "foo"}])
    assert single["status"] == "error" and single["error_code"] == "already_exists", single

    rows = await _create(client, auth_headers, tid, [{"value": "FOO"}, {"value": "Bar"}, {"value": "bar"}])
    assert rows[0]["status"] == "skipped" and rows[0]["id"] == foo, rows
    assert rows[1]["status"] == "created", rows
    assert rows[2]["status"] == "error" and rows[2]["error_code"] == "already_exists", rows
    assert sorted(t.value for t in await Term.find({"terminology_id": tid}).to_list()) == ["Bar", "Foo"]


@pytest.mark.asyncio
async def test_a_case_sensitive_terminology_keeps_case_variants_apart(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers, case_sensitive=True)
    rows = await _create(client, auth_headers, tid, [{"value": "Ab"}, {"value": "aB"}])
    assert [r["status"] for r in rows] == ["created", "created"], rows


@pytest.mark.asyncio
async def test_validation_lists_every_bearer_of_an_ambiguous_value(client: AsyncClient, auth_headers: dict):
    """Case variants written before writes compared values this way are
    still in some databases: validation must not pick one silently."""
    tid, _ = await _terminology(client, auth_headers)
    first = await _id(client, auth_headers, tid, "Foo")
    stored = await Term.find_one({"term_id": first})
    variant = stored.model_copy(update={"id": None, "term_id": str(uuid.uuid4()), "value": "foo"})
    await variant.insert()

    resp = await client.post("/api/def-store/validate", headers=auth_headers,
                             json={"terminology_id": tid, "value": "FOO"})
    body = resp.json()
    assert body["valid"] is True and body["matched_via"] == "value", body
    assert body["matches"] == 2, body
    assert {c["term"]["term_id"] for c in body["candidates"]} == {first, variant.term_id}
    assert all(c["matched_name"] is None for c in body["candidates"])


@pytest.mark.asyncio
async def test_making_a_terminology_case_insensitive_is_refused_over_case_variants(client: AsyncClient, auth_headers: dict):
    tid, _ = await _terminology(client, auth_headers, case_sensitive=True)
    await _create(client, auth_headers, tid, [{"value": "Ab"}, {"value": "aB"}])
    resp = await client.put(TERMS, headers=auth_headers,
                            json=[{"terminology_id": tid, "case_sensitive": False}])
    row = resp.json()["results"][0]
    assert row["status"] == "error" and row["error_code"] == "invalid_state", row
    assert row["details"]["rule"] == "case_variant_values"

    clean, _ = await _terminology(client, auth_headers, case_sensitive=True)
    await _create(client, auth_headers, clean, [{"value": "Ab"}, {"value": "Cd"}])
    resp = await client.put(TERMS, headers=auth_headers,
                            json=[{"terminology_id": clean, "case_sensitive": False}])
    assert resp.json()["results"][0]["status"] == "updated"
