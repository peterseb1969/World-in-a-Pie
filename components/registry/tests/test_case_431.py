"""CASE-431 — claim_entry_keys must be truly best-effort.

The create paths call claim_entry_keys AFTER the entry is already inserted, so
a claim failure must never fail the committed entry. claim_entry_keys was
documented as best-effort but only caught DuplicateKeyError, so any other
exception (a transient Mongo error, or an uninitialised CompositeKeyClaim
collection in a misconfigured in-process harness) propagated — and
register_keys' broad handler then flipped the already-persisted entry's
per-item result to status="error"/registry_id=None. This is what made the
wip-auth integration test 404 on by-value resolution (the harness omitted
CompositeKeyClaim from init_beanie; fixed separately in
libs/wip-auth/tests/test_fastapi_helpers.py).

These tests pin the registry-side hardening: claim_entry_keys swallows any
claim exception (logging it) and returns normally.
"""

from datetime import UTC, datetime

import pytest
from pymongo.errors import DuplicateKeyError

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services import claims as claims_module
from registry.services.claims import claim_entry_keys
from registry.services.hash import HashService


async def _insert_entry(entry_id: str) -> RegistryEntry:
    primary = {"value": entry_id, "label": entry_id}
    entry = RegistryEntry(
        entry_id=entry_id,
        namespace="aa",
        entity_type="templates",
        primary_composite_key=primary,
        primary_composite_key_hash=HashService.compute_composite_key_hash(primary),
        synonyms=[],
        created_at=datetime.now(UTC),
    )
    entry.rebuild_search_values()
    await entry.insert()
    return entry


class TestClaimEntryKeysBestEffort:
    @pytest.mark.asyncio
    async def test_unexpected_claim_error_does_not_propagate(self, client, monkeypatch):
        """A non-DuplicateKeyError from claim() must be swallowed (logged), not
        raised — the committed entry stands, the claim is left for reconcile."""
        entry = await _insert_entry("CASE431_A")

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated claims-subsystem hiccup")

        monkeypatch.setattr(CompositeKeyClaim, "claim", _boom)

        # Must NOT raise.
        await claim_entry_keys(entry)

        # No claim was recorded for the primary key (left for reconciliation).
        existing = await CompositeKeyClaim.find_existing(
            entry.namespace, entry.entity_type, entry.primary_composite_key_hash
        )
        assert existing is None

    @pytest.mark.asyncio
    async def test_duplicate_key_still_handled_distinctly(self, client, monkeypatch):
        """The pre-existing DuplicateKeyError branch is unchanged: it is caught,
        find_existing is consulted, and it does not propagate."""
        entry = await _insert_entry("CASE431_B")
        called = {"find_existing": 0}

        async def _dup(*_args, **_kwargs):
            raise DuplicateKeyError("dup")

        real_find_existing = CompositeKeyClaim.find_existing

        async def _counting_find_existing(*args, **kwargs):
            called["find_existing"] += 1
            return await real_find_existing(*args, **kwargs)

        monkeypatch.setattr(CompositeKeyClaim, "claim", _dup)
        monkeypatch.setattr(CompositeKeyClaim, "find_existing", _counting_find_existing)

        await claim_entry_keys(entry)

        # DuplicateKeyError path consulted find_existing (distinct from the
        # broad-except path, which does not).
        assert called["find_existing"] >= 1

    @pytest.mark.asyncio
    async def test_happy_path_records_primary_claim(self, client):
        """Sanity: with a working claims collection, the primary key is claimed."""
        entry = await _insert_entry("CASE431_C")
        await claim_entry_keys(entry)
        existing = await CompositeKeyClaim.find_existing(
            entry.namespace, entry.entity_type, entry.primary_composite_key_hash
        )
        assert existing is not None
        assert existing.owner_entry_id == entry.entry_id


def test_module_imports_logger():
    """Guard: the broad-except branch logs via the module logger."""
    assert claims_module.logger is not None
