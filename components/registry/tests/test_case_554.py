"""Two-phase composite-key claims: pending → confirmed.

The consistency decision: write paths claim pending BEFORE the entry write
(fail-fast, with rollback of partial multi-pair claims), confirm after it
commits, and the only reachable inconsistency — a pending claim whose
request died mid-flight — is resolved by the cheap (state, age) reconcile
pass or healed inline at collision time. Legacy claims carry no state field
and read as confirmed (zero-migration default). Confirmed-dangling residue
from crashed deletes heals inline on collision too.
"""

from datetime import UTC, datetime, timedelta

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry, Synonym
from registry.services.claims import (
    claim_entry_keys_pending,
    confirm_entry_keys,
    reconcile_pending_claims,
    resolve_stale_claim,
)
from registry.services.hash import HashService


def _hash(ck: dict) -> str:
    return HashService.compute_composite_key_hash(ck)


async def _make_entry(
    entry_id: str,
    primary_key: dict,
    namespace: str = "ns554",
    entity_type: str = "terms",
    synonyms: list[dict] | None = None,
) -> RegistryEntry:
    syn_objs = [
        Synonym(
            namespace=s.get("namespace", namespace),
            entity_type=s.get("entity_type", entity_type),
            composite_key=s["composite_key"],
            composite_key_hash=_hash(s["composite_key"]),
        )
        for s in (synonyms or [])
    ]
    entry = RegistryEntry(
        entry_id=entry_id,
        namespace=namespace,
        entity_type=entity_type,
        primary_composite_key=primary_key,
        primary_composite_key_hash=_hash(primary_key),
        synonyms=syn_objs,
        status="active",
        created_by="test",
    )
    entry.rebuild_search_values()
    await entry.insert()
    return entry


async def _age_claim(namespace: str, entity_type: str, key_hash: str, seconds: int) -> None:
    """Back-date a claim so it falls outside the pending grace window."""
    await CompositeKeyClaim.get_motor_collection().update_one(
        {"namespace": namespace, "entity_type": entity_type,
         "composite_key_hash": key_hash},
        {"$set": {"created_at": datetime.now(UTC) - timedelta(seconds=seconds)}},
    )


@pytest.mark.asyncio
async def test_pending_then_confirm_lifecycle(client):
    h = _hash({"v": "lifecycle"})
    await CompositeKeyClaim.claim("ns554", "terms", h, "e1", "primary", state="pending")
    claim = await CompositeKeyClaim.find_existing("ns554", "terms", h)
    assert claim is not None and claim.state == "pending"

    await CompositeKeyClaim.confirm("ns554", "terms", h, "e1")
    claim = await CompositeKeyClaim.find_existing("ns554", "terms", h)
    assert claim is not None and claim.state == "confirmed"


@pytest.mark.asyncio
async def test_confirm_is_owner_scoped(client):
    h = _hash({"v": "scoped"})
    await CompositeKeyClaim.claim("ns554", "terms", h, "e1", "primary", state="pending")
    await CompositeKeyClaim.confirm("ns554", "terms", h, "someone-else")
    claim = await CompositeKeyClaim.find_existing("ns554", "terms", h)
    assert claim is not None and claim.state == "pending"


@pytest.mark.asyncio
async def test_legacy_claim_without_state_reads_confirmed(client):
    """Pre-CASE-554 rows carry no state field — the model default makes them
    confirmed with zero migration."""
    h = _hash({"v": "legacy"})
    await CompositeKeyClaim.get_motor_collection().insert_one({
        "namespace": "ns554", "entity_type": "terms",
        "composite_key_hash": h, "owner_entry_id": "e-legacy",
        "kind": "primary", "created_at": datetime.now(UTC),
    })
    claim = await CompositeKeyClaim.find_existing("ns554", "terms", h)
    assert claim is not None and claim.state == "confirmed"


@pytest.mark.asyncio
async def test_claim_entry_keys_pending_conflict_rolls_back_partial_claims(client):
    """Fail-fast: a colliding pair fails the item AND releases the pairs the
    same entry already claimed — no half-claimed residue."""
    taken = {"v": "taken-syn"}
    blocker = await _make_entry("blocker", {"v": "blocker"}, synonyms=[
        {"composite_key": taken},
    ])
    await CompositeKeyClaim.claim(
        "ns554", "terms", _hash(taken), blocker.entry_id, "synonym"
    )

    contender = RegistryEntry(
        entry_id="contender",
        namespace="ns554",
        entity_type="terms",
        primary_composite_key={"v": "contender"},
        primary_composite_key_hash=_hash({"v": "contender"}),
        synonyms=[Synonym(
            namespace="ns554", entity_type="terms",
            composite_key=taken, composite_key_hash=_hash(taken),
        )],
        status="active",
        created_by="test",
    )
    conflict = await claim_entry_keys_pending(contender)
    assert conflict is not None and blocker.entry_id in conflict
    # the primary pair claimed before the collision must be rolled back
    assert await CompositeKeyClaim.find_existing(
        "ns554", "terms", _hash({"v": "contender"})
    ) is None


@pytest.mark.asyncio
async def test_reconcile_confirms_backed_deletes_unbacked_spares_young(client):
    backed_entry = await _make_entry("backed", {"v": "backed"})

    # old pending, backed → confirm
    await CompositeKeyClaim.claim(
        "ns554", "terms", backed_entry.primary_composite_key_hash,
        "backed", "primary", state="pending",
    )
    await _age_claim("ns554", "terms", backed_entry.primary_composite_key_hash, 120)

    # old pending, unbacked → delete
    ghost_hash = _hash({"v": "ghost"})
    await CompositeKeyClaim.claim(
        "ns554", "terms", ghost_hash, "never-inserted", "primary", state="pending"
    )
    await _age_claim("ns554", "terms", ghost_hash, 120)

    # young pending, unbacked → untouched (in-flight request)
    young_hash = _hash({"v": "young"})
    await CompositeKeyClaim.claim(
        "ns554", "terms", young_hash, "in-flight", "primary", state="pending"
    )

    summary = await reconcile_pending_claims(grace_seconds=60)
    assert summary["confirmed"] == 1
    assert summary["deleted"] == 1

    backed = await CompositeKeyClaim.find_existing(
        "ns554", "terms", backed_entry.primary_composite_key_hash
    )
    assert backed is not None and backed.state == "confirmed"
    assert await CompositeKeyClaim.find_existing("ns554", "terms", ghost_hash) is None
    young = await CompositeKeyClaim.find_existing("ns554", "terms", young_hash)
    assert young is not None and young.state == "pending"


@pytest.mark.asyncio
async def test_resolve_heals_confirmed_dangling(client):
    """Crashed-delete residue: a confirmed claim with no backing entry heals
    inline at collision time — no scan needed."""
    h = _hash({"v": "dangling"})
    await CompositeKeyClaim.claim("ns554", "terms", h, "deleted-entry", "primary")
    existing = await CompositeKeyClaim.find_existing("ns554", "terms", h)
    assert await resolve_stale_claim(existing) is True
    assert await CompositeKeyClaim.find_existing("ns554", "terms", h) is None


@pytest.mark.asyncio
async def test_resolve_never_steals_from_genuine_owner(client):
    entry = await _make_entry("genuine", {"v": "genuine"})
    await CompositeKeyClaim.claim(
        "ns554", "terms", entry.primary_composite_key_hash, entry.entry_id, "primary"
    )
    existing = await CompositeKeyClaim.find_existing(
        "ns554", "terms", entry.primary_composite_key_hash
    )
    assert await resolve_stale_claim(existing) is False
    assert await CompositeKeyClaim.find_existing(
        "ns554", "terms", entry.primary_composite_key_hash
    ) is not None


@pytest.mark.asyncio
async def test_resolve_leaves_young_pending_alone(client):
    h = _hash({"v": "in-flight"})
    await CompositeKeyClaim.claim(
        "ns554", "terms", h, "someone", "primary", state="pending"
    )
    existing = await CompositeKeyClaim.find_existing("ns554", "terms", h)
    assert await resolve_stale_claim(existing) is False
    assert await CompositeKeyClaim.find_existing("ns554", "terms", h) is not None


@pytest.mark.asyncio
async def test_resolve_confirms_old_backed_pending(client):
    """Request died between entry write and flip: resolve confirms the claim
    and reports it genuine — the caller gets a correct conflict."""
    entry = await _make_entry("late-flip", {"v": "late-flip"})
    await CompositeKeyClaim.claim(
        "ns554", "terms", entry.primary_composite_key_hash,
        entry.entry_id, "primary", state="pending",
    )
    await _age_claim("ns554", "terms", entry.primary_composite_key_hash, 120)

    existing = await CompositeKeyClaim.find_existing(
        "ns554", "terms", entry.primary_composite_key_hash
    )
    assert await resolve_stale_claim(existing) is False
    healed = await CompositeKeyClaim.find_existing(
        "ns554", "terms", entry.primary_composite_key_hash
    )
    assert healed is not None and healed.state == "confirmed"


@pytest.mark.asyncio
async def test_full_two_phase_happy_path(client):
    """claim_entry_keys_pending → insert → confirm_entry_keys leaves every
    pair confirmed."""
    entry = RegistryEntry(
        entry_id="happy",
        namespace="ns554",
        entity_type="terms",
        primary_composite_key={"v": "happy"},
        primary_composite_key_hash=_hash({"v": "happy"}),
        synonyms=[Synonym(
            namespace="ns554", entity_type="terms",
            composite_key={"v": "happy-syn"},
            composite_key_hash=_hash({"v": "happy-syn"}),
        )],
        status="active",
        created_by="test",
    )
    assert await claim_entry_keys_pending(entry) is None
    entry.rebuild_search_values()
    await entry.insert()
    await confirm_entry_keys(entry)

    for h in (_hash({"v": "happy"}), _hash({"v": "happy-syn"})):
        claim = await CompositeKeyClaim.find_existing("ns554", "terms", h)
        assert claim is not None and claim.state == "confirmed"
