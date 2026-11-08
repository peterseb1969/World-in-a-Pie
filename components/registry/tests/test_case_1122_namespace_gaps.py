"""Namespace deletion dry run, create races, prefix length, counter regex.

Provenance: CASE-1122.

- A dry run calls a namespace safe to delete only when the delete would
  delete it: the delete's refusals (the 'wip' namespace, deletion_mode
  'retain', a namespace already locked) are reported and make it unsafe.
- Two creates of one prefix racing: the loser of a POST gets the 409 an
  existing namespace gets, the loser of a PUT upsert is applied as an update
  of the winner (under the update path's guards), the loser of initialize-wip
  gets the namespace — none of them a 500.
- A prefix is at most 63 characters, the longest PostgreSQL schema name.
- The id-counter filter of a deletion escapes the namespace name, so a
  pre-rule name with a regex metacharacter deletes only its own counters.
"""

import uuid

import pytest

from registry.models.id_counter import IdCounter
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import NamespaceDeletionService

NS = "/api/registry/namespaces"


def _prefix(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def _dry_run(client, headers, prefix: str) -> dict:
    r = await client.delete(f"{NS}/{prefix}", params={"dry_run": True}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- dry run --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_reports_the_deletes_refusals(client, auth_headers):
    retain = _prefix("dr-retain")
    full = _prefix("dr-full")
    locked = _prefix("dr-locked")
    r = await client.post(f"{NS}/initialize-wip", headers=auth_headers)
    assert r.status_code == 200, r.text
    for prefix, mode in ((retain, "retain"), (full, "full"), (locked, "full")):
        r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": mode}, headers=auth_headers)
        assert r.status_code == 200, r.text
    await Namespace.get_motor_collection().update_one(
        {"prefix": locked}, {"$set": {"status": "locked"}}
    )

    # 'wip' is retain-mode too: both of its refusals are reported.
    expected = {
        "wip": ["default_namespace", "deletion_mode"],
        retain: ["deletion_mode"],
        locked: ["locked"],
    }
    for prefix, rules in expected.items():
        report = await _dry_run(client, auth_headers, prefix)
        assert report["safe_to_delete"] is False, (prefix, report)
        assert [r["rule"] for r in report["refusals"]] == rules, (prefix, report)
        # The real delete refuses it, with the reason the report gives.
        r = await client.delete(f"{NS}/{prefix}", headers=auth_headers)
        assert r.status_code in (400, 409), r.text
        assert r.json()["detail"] == report["refusals"][0]["message"]

    report = await _dry_run(client, auth_headers, full)
    assert report["safe_to_delete"] is True and report["refusals"] == []


# --- create races ---------------------------------------------------------------


def _stale_first_read(monkeypatch, prefix: str) -> None:
    """The request's existence check reads before a concurrent create lands:
    its first read of ``prefix`` answers 'not there'."""
    real = Namespace.find_one
    state = {"stale": True}

    def find_one(*args, **kwargs):
        if state["stale"] and args and args[0] == {"prefix": prefix}:
            state["stale"] = False

            async def missing():
                return None

            return missing()
        return real(*args, **kwargs)

    monkeypatch.setattr(Namespace, "find_one", find_one)


@pytest.mark.asyncio
async def test_a_lost_create_race_is_409(client, auth_headers, monkeypatch):
    prefix = _prefix("race-post")
    r = await client.post(NS, json={"prefix": prefix}, headers=auth_headers)
    assert r.status_code == 200, r.text
    _stale_first_read(monkeypatch, prefix)
    r = await client.post(NS, json={"prefix": prefix}, headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "already exists" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_lost_upsert_race_updates_the_winner(client, auth_headers, monkeypatch):
    prefix = _prefix("race-put")
    r = await client.put(f"{NS}/{prefix}", json={"description": "first"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    _stale_first_read(monkeypatch, prefix)
    r = await client.put(f"{NS}/{prefix}", json={"description": "second"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "second"
    rows = await Namespace.get_motor_collection().count_documents({"prefix": prefix})
    assert rows == 1
    assert (await Namespace.find_one({"prefix": prefix})).description == "second"


@pytest.mark.asyncio
async def test_a_lost_upsert_race_keeps_the_update_guards(client, auth_headers, monkeypatch):
    """The loser's retain->full flip is an update now, and needs its confirm."""
    prefix = _prefix("race-guard")
    r = await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    _stale_first_read(monkeypatch, prefix)
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=auth_headers)
    assert r.status_code == 400, r.text
    assert "confirm_enable_deletion" in r.json()["detail"]
    assert (await Namespace.find_one({"prefix": prefix})).deletion_mode == "retain"


@pytest.mark.asyncio
async def test_a_lost_initialize_wip_race_answers_the_namespace(client, auth_headers, monkeypatch):
    r = await client.post(f"{NS}/initialize-wip", headers=auth_headers)
    assert r.status_code == 200, r.text
    _stale_first_read(monkeypatch, "wip")
    r = await client.post(f"{NS}/initialize-wip", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["prefix"] == "wip"


# --- prefix length --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_prefix_is_at_most_63_characters(client, auth_headers):
    longest = ("p" + uuid.uuid4().hex * 2)[:63]
    r = await client.put(f"{NS}/{longest}", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    too_long = longest[:-1] + "xy"
    for request in (
        client.put(f"{NS}/{too_long}", json={}, headers=auth_headers),
        client.post(NS, json={"prefix": too_long}, headers=auth_headers),
    ):
        r = await request
        assert r.status_code == 400, r.text
        assert "63" in r.json()["detail"]


# --- id counter regex -----------------------------------------------------------


@pytest.mark.asyncio
async def test_deletion_removes_only_the_namespaces_own_counters(client, auth_headers):
    # Named before the prefix rule: a '.' the rule would refuse today.
    tag = uuid.uuid4().hex[:6]
    legacy, neighbour = f"a.{tag}", f"ax{tag}"
    await Namespace(prefix=legacy, deletion_mode="full").create()
    await IdCounter(counter_key=f"{legacy}:terms:T", seq=3).create()
    await IdCounter(counter_key=f"{neighbour}:terms:T", seq=7).create()

    service = NamespaceDeletionService()
    report = await service.dry_run(legacy)
    assert report["entity_counts"]["id_counters"] == 1

    await service.start_deletion(legacy, force=True)
    remaining = {c.counter_key for c in await IdCounter.find(
        {"counter_key": {"$in": [f"{legacy}:terms:T", f"{neighbour}:terms:T"]}}
    ).to_list()}
    assert remaining == {f"{neighbour}:terms:T"}
