"""A merged-away registry entry keeps its cleared primary key hash when its
document's identity is rehashed.

The Registry clears the primary key hash of an entry it merges away: the
entry claims no key, and its key belongs to the entry it was merged into.
9122ebe6 kept that hash empty in the namespace and id rewrites, but the
identity rehash runs after them, on a merge and on a plain restore alike,
and stamped a hash computed from the rehashed key. The dead entry then
claimed the survivor's key again ("composite key(s) were already claimed
by other entries"). The rehash now rewrites the recorded key, which the
merge plan matches a merged-away entry on, and leaves the hash empty.
"""

from __future__ import annotations

from datetime import UTC, datetime

from document_store.services.backup_engine import DirectRestoreEngine, _IdentityRehash
from wip_auth.composite_key import compute_composite_key_hash

OLD = {"ns": "acme", "template_id": "TPL-1", "identity_hash": "old"}


def _rehash(new_hashes: dict[str, str]) -> _IdentityRehash:
    rehash = _IdentityRehash.__new__(_IdentityRehash)
    rehash.new_hash = dict(new_hashes)
    rehash.unrehashable = 0
    return rehash


def _entry(entry_id: str, key_hash: str, **extra) -> dict:
    return {
        "entry_id": entry_id, "namespace": "acme", "entity_type": "documents",
        "primary_composite_key": dict(OLD), "primary_composite_key_hash": key_hash,
        "synonyms": [], "status": "active", **extra,
    }


def test_a_merged_away_entry_keeps_its_cleared_hash_through_the_rehash():
    merged_away = _entry("D", "", merged_into="P", status="inactive")

    rekeyed = _rehash({"D": "new"}).rekey(merged_away)

    assert rekeyed["primary_composite_key"]["identity_hash"] == "new"
    assert rekeyed["primary_composite_key_hash"] == ""
    claims = DirectRestoreEngine._claim_rows_for([rekeyed], datetime.now(UTC))
    assert claims == []


def test_a_live_entry_is_still_rehashed():
    live = _entry("D", compute_composite_key_hash(OLD))

    rekeyed = _rehash({"D": "new"}).rekey(live)

    new_key = {**OLD, "identity_hash": "new"}
    assert rekeyed["primary_composite_key"] == new_key
    assert rekeyed["primary_composite_key_hash"] == compute_composite_key_hash(new_key)
