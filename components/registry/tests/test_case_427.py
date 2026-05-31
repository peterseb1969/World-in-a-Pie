"""CASE-427 — composite-key claim uniqueness.

Phase-1 (Commit 1) coverage: the CompositeKeyClaim atomic primitives, the
backfill that builds the claims domain + reconciles pre-existing duplicates
(strip + log losers), and orphan reconciliation. Write-path gating + the
concurrency repro live in the Commit-2 test set.
"""

from datetime import UTC, datetime

import pytest
from pymongo.errors import DuplicateKeyError

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry, Synonym
from registry.services.claims import backfill_claims, reconcile_orphan_claims
from registry.services.hash import HashService


def _hash(ck: dict) -> str:
    return HashService.compute_composite_key_hash(ck)


async def _make_entry(
    entry_id: str,
    namespace: str,
    entity_type: str,
    primary_key: dict,
    synonyms: list[dict] | None = None,
    created_at: datetime | None = None,
) -> RegistryEntry:
    """Insert a RegistryEntry directly (bypassing the API) with embedded
    synonyms and NO claims — simulating pre-CASE-427 data."""
    syn_objs = []
    for s in synonyms or []:
        syn_objs.append(Synonym(
            namespace=s.get("namespace", namespace),
            entity_type=s.get("entity_type", entity_type),
            composite_key=s["composite_key"],
            composite_key_hash=_hash(s["composite_key"]),
        ))
    entry = RegistryEntry(
        entry_id=entry_id,
        namespace=namespace,
        entity_type=entity_type,
        primary_composite_key=primary_key,
        primary_composite_key_hash=_hash(primary_key),
        synonyms=syn_objs,
        created_at=created_at or datetime.now(UTC),
    )
    entry.rebuild_search_values()
    await entry.insert()
    return entry


# ── Atomic primitive ───────────────────────────────────────────────────


class TestClaimPrimitive:
    @pytest.mark.asyncio
    async def test_claim_then_conflict(self, client):
        h = _hash({"value": "COLLIDE"})
        first = await CompositeKeyClaim.claim("default", "templates", h, "E_A", "synonym")
        assert first is not None
        # Same owner re-claim → idempotent no-op (returns existing, no raise).
        again = await CompositeKeyClaim.claim("default", "templates", h, "E_A", "synonym")
        assert again is not None
        # Different owner → DuplicateKeyError.
        with pytest.raises(DuplicateKeyError):
            await CompositeKeyClaim.claim("default", "templates", h, "E_B", "synonym")
        # Exactly one claim exists.
        assert await CompositeKeyClaim.find({"composite_key_hash": h}).count() == 1

    @pytest.mark.asyncio
    async def test_empty_hash_not_claimed(self, client):
        assert await CompositeKeyClaim.claim("default", "templates", "", "E_A", "primary") is None

    @pytest.mark.asyncio
    async def test_release_only_own(self, client):
        h = _hash({"value": "REL"})
        await CompositeKeyClaim.claim("default", "templates", h, "E_A", "synonym")
        # Release scoped to a different owner does nothing.
        await CompositeKeyClaim.release("default", "templates", h, owner_entry_id="E_B")
        assert await CompositeKeyClaim.find({"composite_key_hash": h}).count() == 1
        # Release scoped to the real owner removes it.
        await CompositeKeyClaim.release("default", "templates", h, owner_entry_id="E_A")
        assert await CompositeKeyClaim.find({"composite_key_hash": h}).count() == 0


# ── Backfill + duplicate reconciliation ──────────────────────────────────


class TestBackfill:
    @pytest.mark.asyncio
    async def test_duplicate_synonym_loser_stripped(self, client):
        # Two entries embed the SAME synonym hash, no claims (pre-fix race state).
        dup = {"value": "DUP"}
        await _make_entry(
            "EV_0", "default", "templates", {"value": "P0"}, synonyms=[{"composite_key": dup}],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        await _make_entry(
            "EV_1", "default", "templates", {"value": "P1"}, synonyms=[{"composite_key": dup}],
            created_at=datetime(2026, 1, 2, tzinfo=UTC),  # later → loser
        )

        summary = await backfill_claims()

        dup_hash = _hash(dup)
        # Exactly one claim for the duplicated hash.
        assert await CompositeKeyClaim.find({"composite_key_hash": dup_hash}).count() == 1
        winner = await CompositeKeyClaim.find_existing("default", "templates", dup_hash)
        assert winner.owner_entry_id == "EV_0"  # earliest created_at wins
        assert summary["synonym_losers_stripped"] == 1
        # Loser's embedded synonym was stripped; winner's retained.
        ev0 = await RegistryEntry.find_one({"entry_id": "EV_0"})
        ev1 = await RegistryEntry.find_one({"entry_id": "EV_1"})
        assert any(s.composite_key_hash == dup_hash for s in ev0.synonyms)
        assert all(s.composite_key_hash != dup_hash for s in ev1.synonyms)
        # Primaries each claimed.
        assert await CompositeKeyClaim.find({"kind": "primary"}).count() == 2

    @pytest.mark.asyncio
    async def test_primary_beats_synonym(self, client):
        # One entry holds X as PRIMARY, another holds X as a SYNONYM.
        x = {"value": "X"}
        await _make_entry(
            "P_OWNER", "default", "templates", x,
            created_at=datetime(2026, 1, 5, tzinfo=UTC),  # later, but primary wins
        )
        await _make_entry(
            "S_OWNER", "default", "templates", {"value": "OTHER"}, synonyms=[{"composite_key": x}],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),  # earlier, but only a synonym
        )

        await backfill_claims()

        claim = await CompositeKeyClaim.find_existing("default", "templates", _hash(x))
        assert claim.owner_entry_id == "P_OWNER"
        assert claim.kind == "primary"
        # The synonym loser was stripped from S_OWNER.
        s_owner = await RegistryEntry.find_one({"entry_id": "S_OWNER"})
        assert all(s.composite_key_hash != _hash(x) for s in s_owner.synonyms)

    @pytest.mark.asyncio
    async def test_backfill_idempotent(self, client):
        await _make_entry("E1", "default", "templates", {"value": "A"},
                          synonyms=[{"composite_key": {"value": "B"}}])
        first = await backfill_claims()
        before = await CompositeKeyClaim.find_all().count()
        second = await backfill_claims()
        after = await CompositeKeyClaim.find_all().count()
        assert before == after
        assert second["synonym_losers_stripped"] == 0
        assert first["primary_claimed"] >= 1


# ── Orphan reconciliation ────────────────────────────────────────────────


class TestReconcile:
    @pytest.mark.asyncio
    async def test_orphan_owner_missing_deleted(self, client):
        # Claim points at a non-existent entry.
        await CompositeKeyClaim.claim("default", "templates", _hash({"v": "1"}), "GHOST", "synonym")
        summary = await reconcile_orphan_claims()
        assert summary["orphans_deleted"] == 1
        assert await CompositeKeyClaim.find_all().count() == 0

    @pytest.mark.asyncio
    async def test_orphan_entry_lacks_key_deleted(self, client):
        # Entry exists but doesn't embed/own the claimed hash.
        await _make_entry("E_REAL", "default", "templates", {"value": "REAL"})
        await CompositeKeyClaim.claim("default", "templates", _hash({"v": "stale"}), "E_REAL", "synonym")
        summary = await reconcile_orphan_claims()
        assert summary["orphans_deleted"] == 1

    @pytest.mark.asyncio
    async def test_valid_claim_kept(self, client):
        await _make_entry("E_OK", "default", "templates", {"value": "K"})
        await backfill_claims()  # creates the legitimate primary claim
        summary = await reconcile_orphan_claims()
        assert summary["orphans_deleted"] == 0
        assert await CompositeKeyClaim.find({"owner_entry_id": "E_OK"}).count() == 1


# ── Concurrency + write-path behavior (Commit 2) ─────────────────────────


import asyncio  # noqa: E402


async def _register(client, auth_headers, namespace, entity_type, composite_key) -> str:
    r = await client.post(
        "/api/registry/entries/register",
        json=[{"namespace": namespace, "entity_type": entity_type, "composite_key": composite_key}],
        headers=auth_headers,
    )
    assert r.status_code == 200
    return r.json()["results"][0]["registry_id"]


async def _add_synonym(client, auth_headers, target_id, ns, etype, key) -> dict:
    r = await client.post(
        "/api/registry/synonyms/add",
        json=[{
            "target_id": target_id, "synonym_namespace": ns,
            "synonym_entity_type": etype, "synonym_composite_key": key,
        }],
        headers=auth_headers,
    )
    assert r.status_code == 200
    return r.json()["results"][0]


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_direct_claim_race(self, client):
        """The unique index is the atomic gate: N concurrent claims of the same
        key by different owners → exactly 1 wins, the rest raise."""
        h = _hash({"value": "RACE"})

        async def attempt(owner: str):
            try:
                await CompositeKeyClaim.claim("default", "templates", h, owner, "synonym")
                return True
            except DuplicateKeyError:
                return False

        outcomes = await asyncio.gather(*(attempt(f"OWNER_{n}") for n in range(10)))
        assert sum(outcomes) == 1
        assert await CompositeKeyClaim.find({"composite_key_hash": h}).count() == 1

    @pytest.mark.asyncio
    async def test_concurrent_add_synonym_one_winner(self, client, auth_headers):
        """N entries, N concurrent add_synonym of the SAME key → exactly one
        'added', the rest 'error'. The headline CASE-427 regression guard."""
        targets = [
            await _register(client, auth_headers, "default", "templates", {"value": f"P{n}"})
            for n in range(8)
        ]
        dup_key = {"value": "COLLIDE"}
        results = await asyncio.gather(*(
            _add_synonym(client, auth_headers, t, "default", "templates", dup_key)
            for t in targets
        ))
        added = [r for r in results if r["status"] == "added"]
        errored = [r for r in results if r["status"] == "error"]
        assert len(added) == 1, results
        assert len(errored) == 7, results
        # Exactly one claim, and lookup resolves deterministically to one entry.
        assert await CompositeKeyClaim.find({"composite_key_hash": _hash(dup_key)}).count() == 1


class TestWritePathClaims:
    @pytest.mark.asyncio
    async def test_add_creates_claim_remove_releases_then_readd_elsewhere(self, client, auth_headers):
        a = await _register(client, auth_headers, "default", "templates", {"value": "A"})
        b = await _register(client, auth_headers, "default", "templates", {"value": "B"})
        key = {"vendor": "V-1"}

        assert (await _add_synonym(client, auth_headers, a, "vendor1", "templates", key))["status"] == "added"
        assert await CompositeKeyClaim.find_existing("vendor1", "templates", _hash(key)) is not None

        # Adding the same key to B is rejected while A owns it.
        assert (await _add_synonym(client, auth_headers, b, "vendor1", "templates", key))["status"] == "error"

        # Remove from A releases the claim.
        rm = await client.post(
            "/api/registry/synonyms/remove",
            json=[{"target_id": a, "synonym_namespace": "vendor1",
                   "synonym_entity_type": "templates", "synonym_composite_key": key}],
            headers=auth_headers,
        )
        assert rm.json()["results"][0]["status"] == "removed"
        assert await CompositeKeyClaim.find_existing("vendor1", "templates", _hash(key)) is None

        # Now B can claim it.
        assert (await _add_synonym(client, auth_headers, b, "vendor1", "templates", key))["status"] == "added"

    @pytest.mark.asyncio
    async def test_self_owned_orphan_reclaim(self, client, auth_headers):
        a = await _register(client, auth_headers, "default", "templates", {"value": "SELF"})
        key = {"vendor": "ORPH"}
        # Simulate crash-after-claim: claim exists owned by A, but A doesn't embed it.
        await CompositeKeyClaim.claim("vendor1", "templates", _hash(key), a, "synonym")
        # add_synonym for the SAME owner reclaims the orphan → added.
        assert (await _add_synonym(client, auth_headers, a, "vendor1", "templates", key))["status"] == "added"

    @pytest.mark.asyncio
    async def test_cross_owner_orphan_not_stolen(self, client, auth_headers):
        a = await _register(client, auth_headers, "default", "templates", {"value": "OWN"})
        b = await _register(client, auth_headers, "default", "templates", {"value": "OTH"})
        key = {"vendor": "X"}
        # Claim owned by A, A doesn't embed it (cross-owner orphan for B).
        await CompositeKeyClaim.claim("vendor1", "templates", _hash(key), a, "synonym")
        # B must NOT steal it.
        assert (await _add_synonym(client, auth_headers, b, "vendor1", "templates", key))["status"] == "error"

    @pytest.mark.asyncio
    async def test_merge_transfers_claims(self, client, auth_headers):
        pref = await _register(client, auth_headers, "default", "templates", {"value": "PREF"})
        dep = await _register(client, auth_headers, "default", "templates", {"value": "DEP"})
        skey = {"vendor": "DEP-SYN"}
        assert (await _add_synonym(client, auth_headers, dep, "vendor1", "templates", skey))["status"] == "added"

        m = await client.post(
            "/api/registry/synonyms/merge",
            json=[{"preferred_id": pref, "deprecated_id": dep}],
            headers=auth_headers,
        )
        assert m.json()["results"][0]["status"] == "merged"
        # The deprecated synonym's claim now points at preferred.
        claim = await CompositeKeyClaim.find_existing("vendor1", "templates", _hash(skey))
        assert claim is not None and claim.owner_entry_id == pref
