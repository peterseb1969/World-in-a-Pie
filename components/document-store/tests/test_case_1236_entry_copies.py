"""A registry entry copied from another install compares identical when only
its copy bookkeeping differs.

Two copies of one entry differ in more than entry_id, search_values and
revision: each synonym carries its own audit fields (created_at, created_by)
and a composite_key_hash derived from its key, the synonyms may be listed in
another order, actor_note goes with the audit fields, and merged_into /
merged_from name entry ids, which the merge must rewrite through its id maps
like the ids in the keys. None of that is content, so none of it is a clash.
What stays a clash: a synonym key the target lacks, a synonym's status, a
merge into another entry.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from wip_archive.remap import IDRemapper

from document_store.services.backup_engine import DirectRestoreEngine
from wip_auth.composite_key import compute_composite_key_hash

from .test_merge_plan import NAMESPACE, planner  # noqa: F401  (planner is a fixture)


def _synonym(key: dict, **extra) -> dict:
    return {
        "namespace": NAMESPACE, "entity_type": "documents", "composite_key": key,
        "composite_key_hash": f"hash-of-{sorted(key.items())}", "status": "active", **extra,
    }


SYN_A = {"erp_id": "SAP-1"}
SYN_B = {"legacy": "OLD-7"}


def _entry(entry_id: str, synonyms: list, **extra) -> dict:
    return {
        "namespace": NAMESPACE, "entry_id": entry_id, "entity_type": "documents",
        "primary_composite_key_hash": "h1", "primary_composite_key": {"k": "v"},
        "status": "active", "synonyms": synonyms, "source_info": {"system_id": "document-store"},
        **extra,
    }


@pytest.mark.asyncio
async def test_synonym_bookkeeping_and_order_are_not_a_clash(planner):  # noqa: F811
    target = _entry("E-HERE", [
        _synonym(SYN_A, created_at="2026-11-01T00:00:00Z", created_by="apikey:here"),
        _synonym(SYN_B, created_at="2026-11-02T00:00:00Z", created_by="apikey:here"),
    ], actor_note="console import")
    await planner.seed("registry_entries", [target])
    archived = _entry("E-THERE", [
        _synonym(SYN_B, created_at="2025-01-02T00:00:00Z", created_by="apikey:there",
                 composite_key_hash="other-algorithm-run"),
        _synonym(SYN_A, created_at="2025-01-01T00:00:00Z", created_by="apikey:there"),
    ], actor_note=None)

    plan = await planner.plan("registry_entries", [archived], NAMESPACE)

    assert plan.clashes[0].diff == {}
    assert plan.summary()["unchanged"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("archived_synonyms", [
    [_synonym(SYN_A)],  # a synonym the target holds is missing
    [_synonym(SYN_A), _synonym(SYN_B, status="inactive")],  # a synonym's status differs
])
async def test_a_synonym_difference_stays_a_clash(planner, archived_synonyms):  # noqa: F811
    await planner.seed("registry_entries", [_entry("E-HERE", [_synonym(SYN_A), _synonym(SYN_B)])])
    plan = await planner.plan("registry_entries", [_entry("E-THERE", archived_synonyms)], NAMESPACE)
    assert set(plan.clashes[0].diff) == {"synonyms"}


def test_the_rewrite_maps_merge_pointers_like_key_ids():
    """merged_into and a synonym's merged_from name entries by id: the entry
    rewrite follows the merge's id maps for them as for the keys."""
    remapper = IDRemapper()
    remapper.add_document_mapping("D-THERE", "D-HERE")
    entry = _entry("E-THERE", [_synonym({"k": "w"}, merged_from="D-THERE")],
                   merged_into="D-THERE", status="inactive")
    engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)

    out = engine._rewrite_registry_entry(entry, remapper)

    assert out["merged_into"] == "D-HERE"
    assert out["synonyms"][0]["merged_from"] == "D-HERE"
    assert entry["merged_into"] == "D-THERE"  # the archived row is untouched


# ---------------------------------------------------------------------------
# A merged-away entry: its primary key hash is cleared, and the survivor holds
# its primary key as a synonym with merged_from naming it.
# ---------------------------------------------------------------------------

K_D = {"ns": NAMESPACE, "template_id": "TPL-1", "identity": "D"}


def _merged_away(entry_id: str, survivor: str) -> dict:
    return _entry(entry_id, [_synonym(SYN_B, status="inactive")],
                  primary_composite_key=K_D, primary_composite_key_hash="",
                  status="inactive", merged_into=survivor)


def _survivor(entry_id: str, merged_away: str) -> dict:
    return _entry(entry_id, [{
        **_synonym(K_D), "composite_key_hash": compute_composite_key_hash(K_D),
        "merged_from": merged_away,
    }])


@pytest.mark.asyncio
async def test_a_merged_away_entry_matches_its_copy_through_the_survivor(planner):  # noqa: F811
    """Install A merged D into P; install B did the same under its own ids. D's
    hash is cleared on both sides, so D can only be found through the key P
    took from it."""
    await planner.seed("registry_entries", [
        _survivor("P-HERE", "D-HERE"), _merged_away("D-HERE", "P-HERE"),
    ])
    # As the rewrite leaves it: merged_into already mapped onto the target's P.
    archived = _merged_away("D-THERE", "P-HERE")

    plan = await planner.plan("registry_entries", [archived], NAMESPACE)

    assert plan.to_insert == []
    assert plan.mapping == {"D-THERE": "D-HERE"}
    assert plan.clashes[0].diff == {}
    assert plan.summary()["unchanged"] == 1


@pytest.mark.asyncio
async def test_a_merged_away_entry_the_target_holds_active_is_a_clash(planner):  # noqa: F811
    """The target never merged D: it holds D's key as D's own primary key."""
    held = _entry("D-HERE", [_synonym(SYN_B)], primary_composite_key=K_D,
                  primary_composite_key_hash=compute_composite_key_hash(K_D))
    await planner.seed("registry_entries", [held])

    plan = await planner.plan("registry_entries", [_merged_away("D-THERE", "P-HERE")], NAMESPACE)

    assert plan.mapping == {"D-THERE": "D-HERE"}
    assert {"status", "merged_into", "synonyms"} <= set(plan.clashes[0].diff)


@pytest.mark.asyncio
async def test_a_merged_away_entry_the_target_lacks_is_inserted(planner):  # noqa: F811
    await planner.seed("registry_entries", [_entry("OTHER", [])])
    plan = await planner.plan("registry_entries", [_merged_away("D-THERE", "P-HERE")], NAMESPACE)
    assert [e["entry_id"] for e in plan.to_insert] == ["D-THERE"]


# ---------------------------------------------------------------------------
# A cleared primary key hash stays cleared through the merge's rewrites: the
# Registry cleared it so that the dead entry claims nothing.
# ---------------------------------------------------------------------------

def _engine() -> DirectRestoreEngine:
    return DirectRestoreEngine(MagicMock(), None, lambda _e: None)


def test_the_namespace_rewrite_keeps_a_cleared_hash_cleared():
    entry = {**_merged_away("D-THERE", "P-THERE"), "namespace": "src",
             "primary_composite_key": {**K_D, "ns": "src"}}

    out = _engine()._rewrite_namespace("registry_entries", entry, {"src": NAMESPACE})

    assert out["primary_composite_key"]["ns"] == NAMESPACE
    assert out["primary_composite_key_hash"] == ""
    assert DirectRestoreEngine._claim_rows_for([out], MagicMock()) == []


def test_the_namespace_rewrite_still_rehashes_a_live_key():
    key = {**K_D, "ns": "src"}
    entry = _entry("D-THERE", [], namespace="src", primary_composite_key=key,
                   primary_composite_key_hash=compute_composite_key_hash(key))

    out = _engine()._rewrite_namespace("registry_entries", entry, {"src": NAMESPACE})

    assert out["primary_composite_key_hash"] == compute_composite_key_hash(
        {**K_D, "ns": NAMESPACE})


def test_the_id_rewrite_keeps_a_cleared_hash_cleared():
    remapper = IDRemapper()
    remapper.add_template_mapping("TPL-1", "TPL-HERE")
    out = _engine()._rewrite_registry_entry(_merged_away("D-THERE", "P-THERE"), remapper)
    assert out["primary_composite_key"]["template_id"] == "TPL-HERE"
    assert out["primary_composite_key_hash"] == ""
