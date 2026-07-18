"""CASE-431 — committed entries must never fail on claim-domain hiccups.

Originally this pinned the best-effort contract of the post-insert
claim_entry_keys. The two-phase protocol (CASE-554) retired that function:
claims now happen BEFORE the insert (claim_entry_keys_pending — where a
failure legitimately fails the not-yet-committed request), and the
committed-entry protection this case is about moved to the post-commit side:
confirm_entry_keys and release_entry_keys must swallow any exception
(logging it) and return normally, leaving the pending claim for the
reconcile pass. These tests pin that relocated contract.
"""

from datetime import UTC, datetime

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services import claims as claims_module
from registry.services.claims import (
    claim_entry_keys_pending,
    confirm_entry_keys,
    release_entry_keys,
)
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


class TestPostCommitBestEffort:
    @pytest.mark.asyncio
    async def test_confirm_error_does_not_propagate(self, client, monkeypatch):
        """A transient error during the confirm flip must not fail the
        already-committed entry — the claim stays pending for reconcile."""
        entry = await _insert_entry("CASE431_A")
        await claim_entry_keys_pending(entry)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("transient mongo hiccup")

        monkeypatch.setattr(CompositeKeyClaim, "confirm", _boom)

        # Must NOT raise.
        await confirm_entry_keys(entry)

        existing = await CompositeKeyClaim.find_existing(
            entry.namespace, entry.entity_type, entry.primary_composite_key_hash
        )
        assert existing is not None and existing.state == "pending"

    @pytest.mark.asyncio
    async def test_release_error_does_not_propagate(self, client, monkeypatch):
        """Rollback after a failed batch insert is best-effort too — a
        failing release logs and returns; the pending claim dies in the
        reconcile pass."""
        entry = await _insert_entry("CASE431_B")
        await claim_entry_keys_pending(entry)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("transient mongo hiccup")

        monkeypatch.setattr(CompositeKeyClaim, "release", _boom)

        # Must NOT raise.
        await release_entry_keys(entry)

    @pytest.mark.asyncio
    async def test_happy_path_claims_pending_then_confirms(self, client):
        """Sanity: the two-phase pair leaves a confirmed primary claim."""
        entry = await _insert_entry("CASE431_C")
        assert await claim_entry_keys_pending(entry) is None
        await confirm_entry_keys(entry)
        existing = await CompositeKeyClaim.find_existing(
            entry.namespace, entry.entity_type, entry.primary_composite_key_hash
        )
        assert existing is not None
        assert existing.owner_entry_id == entry.entry_id
        assert existing.state == "confirmed"


def test_module_imports_logger():
    """Guard: the best-effort branches log via the module logger."""
    assert claims_module.logger is not None
