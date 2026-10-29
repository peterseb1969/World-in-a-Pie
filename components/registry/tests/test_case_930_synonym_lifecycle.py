"""CASE-930 — retiring an entity releases its synonyms; reviving reclaims them.

A retired entity's synonyms stay recorded on its entry but become inactive:
they neither block another entity from claiming the key nor resolve. The
entry itself stays active — its canonical ID, primary key and auto-synonym
({ns, type, …}, the entity's own address) keep resolving — so a re-post of
the same identity still revives it rather than forking. Reviving re-claims
each synonym whose key is free; one another entity took meanwhile stays with
that entity, and the revival says so.

The registry-level shape of the reported failure: a retired document whose
term identity field resolved to a since-deleted term keeps the
{"@template": T, name: X} synonym, so a new document whose X resolves to the
re-created term (a different identity) was refused.
"""

import pytest
from httpx import AsyncClient

NS = "default"
TEMPLATE = "CHAR_930"
SPELLING = {"@template": TEMPLATE, "name": "X"}


async def _post(client, auth_headers, path, body, params=None) -> dict:
    r = await client.post(f"/api/registry{path}", json=body, headers=auth_headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def _register_doc(client, auth_headers, term_id: str) -> dict:
    """A document entry as document-store registers it: the primary key
    carries the identity hash of the resolved term, the synonym the spelling."""
    data = await _post(client, auth_headers, "/entries/register", [{
        "namespace": NS,
        "entity_type": "documents",
        "composite_key": {"ns": NS, "template_id": "TPL-930"},
        "identity_values": {"name": term_id},
        "synonym_values": SPELLING,
    }])
    return data["results"][0]


async def _add_synonym(client, auth_headers, target_id: str, key: dict) -> dict:
    data = await _post(client, auth_headers, "/synonyms/add", [{
        "target_id": target_id,
        "synonym_namespace": NS,
        "synonym_entity_type": "documents",
        "synonym_composite_key": key,
    }])
    return data["results"][0]


async def _by_key(client, auth_headers, key: dict, entity_type: str = "documents") -> dict:
    data = await _post(client, auth_headers, "/entries/lookup/by-key", [{
        "namespace": NS, "entity_type": entity_type, "composite_key": key,
    }])
    return data["results"][0]


async def _by_id(client, auth_headers, value: str) -> dict:
    data = await _post(client, auth_headers, "/entries/lookup/by-id", [{
        "entry_id": value, "namespace": NS, "entity_type": "documents",
    }])
    return data["results"][0]


async def _lifecycle(client, auth_headers, verb: str, entry_id: str, dry_run=False) -> dict:
    data = await _post(
        client, auth_headers, f"/entries/{verb}", [{"entry_id": entry_id}],
        params={"dry_run": "true"} if dry_run else None,
    )
    return data["results"][0]


async def _retired_doc(client, auth_headers) -> tuple[str, dict]:
    """A document entry with its auto-synonym, then retired."""
    doc = await _register_doc(client, auth_headers, "term-old")
    assert doc["status"] == "created"
    auto = {"ns": NS, "type": "document", "template": TEMPLATE, "identity_hash": doc["identity_hash"]}
    assert (await _add_synonym(client, auth_headers, doc["registry_id"], auto))["status"] == "added"
    retired = await _lifecycle(client, auth_headers, "deactivate", doc["registry_id"])
    assert retired["status"] == "deactivated"
    return doc["registry_id"], auto


class TestRetirementReleasesSynonyms:
    @pytest.mark.asyncio
    async def test_a_live_document_blocks_the_spelling(self, client: AsyncClient, auth_headers: dict):
        first = await _register_doc(client, auth_headers, "term-old")
        assert first["status"] == "created"
        second = await _register_doc(client, auth_headers, "term-new")
        assert second["status"] == "error"
        assert second["details"] == {"rule": "key_claimed"}

    @pytest.mark.asyncio
    async def test_a_retired_document_no_longer_blocks_a_different_identity(
        self, client: AsyncClient, auth_headers: dict,
    ):
        old_id, _ = await _retired_doc(client, auth_headers)
        new = await _register_doc(client, auth_headers, "term-new")
        assert new["status"] == "created"
        assert new["registry_id"] != old_id
        assert (await _by_key(client, auth_headers, SPELLING))["entry_id"] == new["registry_id"]

    @pytest.mark.asyncio
    async def test_a_retired_synonym_does_not_resolve(self, client: AsyncClient, auth_headers: dict):
        await _retired_doc(client, auth_headers)
        assert (await _by_key(client, auth_headers, SPELLING))["status"] == "not_found"
        # nor by value: the spelling left the entry's search values
        assert (await _by_id(client, auth_headers, "X"))["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_canonical_id_and_auto_synonym_keep_resolving(
        self, client: AsyncClient, auth_headers: dict,
    ):
        old_id, auto = await _retired_doc(client, auth_headers)
        found = await _by_id(client, auth_headers, old_id)
        assert found["status"] == "found"
        assert (await _by_key(client, auth_headers, auto))["entry_id"] == old_id
        statuses = {
            s["composite_key"].get("@template", "auto"): s["status"] for s in found["synonyms"]
        }
        assert statuses == {TEMPLATE: "inactive", "auto": "active"}

    @pytest.mark.asyncio
    async def test_reposting_the_same_identity_finds_the_retired_entry(
        self, client: AsyncClient, auth_headers: dict,
    ):
        old_id, _ = await _retired_doc(client, auth_headers)
        again = await _register_doc(client, auth_headers, "term-old")
        assert again["status"] == "already_exists"
        assert again["registry_id"] == old_id

    @pytest.mark.asyncio
    async def test_explicit_synonyms_are_released_too(self, client: AsyncClient, auth_headers: dict):
        doc = await _register_doc(client, auth_headers, "term-old")
        sku = {"vendor_sku": "S-930"}
        assert (await _add_synonym(client, auth_headers, doc["registry_id"], sku))["status"] == "added"
        retired = await _lifecycle(client, auth_headers, "deactivate", doc["registry_id"])
        assert sorted(retired["released"], key=str) == sorted([SPELLING, sku], key=str)
        assert (await _by_key(client, auth_headers, sku))["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_value_auto_synonym_of_a_template_stays_claimed(
        self, client: AsyncClient, auth_headers: dict,
    ):
        data = await _post(client, auth_headers, "/entries/register", [{
            "namespace": NS, "entity_type": "templates",
            "composite_key": {"ns": NS, "value": "TPL_930"},
        }])
        tpl_id = data["results"][0]["registry_id"]
        auto = {"ns": NS, "type": "template", "value": "TPL_930"}
        added = await _post(client, auth_headers, "/synonyms/add", [{
            "target_id": tpl_id, "synonym_namespace": NS,
            "synonym_entity_type": "templates", "synonym_composite_key": auto,
        }])
        assert added["results"][0]["status"] == "added"
        retired = await _lifecycle(client, auth_headers, "deactivate", tpl_id)
        assert retired["status"] == "unchanged"
        assert (await _by_key(client, auth_headers, auto, "templates"))["entry_id"] == tpl_id

    @pytest.mark.asyncio
    async def test_deactivate_is_idempotent(self, client: AsyncClient, auth_headers: dict):
        old_id, _ = await _retired_doc(client, auth_headers)
        again = await _lifecycle(client, auth_headers, "deactivate", old_id)
        assert again["status"] == "unchanged"
        assert again["released"] == []

    @pytest.mark.asyncio
    async def test_dry_run_changes_nothing(self, client: AsyncClient, auth_headers: dict):
        doc = await _register_doc(client, auth_headers, "term-old")
        dry = await _lifecycle(client, auth_headers, "deactivate", doc["registry_id"], dry_run=True)
        assert dry["status"] == "deactivated"
        assert dry["released"] == [SPELLING]
        assert (await _by_key(client, auth_headers, SPELLING))["entry_id"] == doc["registry_id"]
        blocked = await _register_doc(client, auth_headers, "term-new")
        assert blocked["status"] == "error"

    @pytest.mark.asyncio
    async def test_unknown_entry_is_not_found(self, client: AsyncClient, auth_headers: dict):
        for verb in ("deactivate", "reactivate"):
            assert (await _lifecycle(client, auth_headers, verb, "no-such-entry"))["status"] == "not_found"


class TestRevivalReclaimsSynonyms:
    @pytest.mark.asyncio
    async def test_a_free_synonym_is_reclaimed(self, client: AsyncClient, auth_headers: dict):
        old_id, _ = await _retired_doc(client, auth_headers)
        revived = await _lifecycle(client, auth_headers, "reactivate", old_id)
        assert revived["status"] == "reactivated"
        assert revived["reclaimed"] == [SPELLING]
        assert revived["warnings"] == []
        assert (await _by_key(client, auth_headers, SPELLING))["entry_id"] == old_id
        # claimed again: a different identity is refused once more
        assert (await _register_doc(client, auth_headers, "term-new"))["status"] == "error"

    @pytest.mark.asyncio
    async def test_a_taken_synonym_stays_with_its_new_holder(
        self, client: AsyncClient, auth_headers: dict,
    ):
        old_id, _ = await _retired_doc(client, auth_headers)
        new = await _register_doc(client, auth_headers, "term-new")
        assert new["status"] == "created"

        revived = await _lifecycle(client, auth_headers, "reactivate", old_id)
        assert revived["status"] == "reactivated"
        assert revived["reclaimed"] == []
        assert revived["not_reclaimed"] == [{"composite_key": SPELLING, "held_by": new["registry_id"]}]
        assert len(revived["warnings"]) == 1 and new["registry_id"] in revived["warnings"][0]
        assert (await _by_key(client, auth_headers, SPELLING))["entry_id"] == new["registry_id"]

    @pytest.mark.asyncio
    async def test_reactivate_without_retirement_is_unchanged(
        self, client: AsyncClient, auth_headers: dict,
    ):
        doc = await _register_doc(client, auth_headers, "term-old")
        revived = await _lifecycle(client, auth_headers, "reactivate", doc["registry_id"])
        assert revived["status"] == "unchanged"


class TestMergeLeavesForeignClaimsAlone:
    @pytest.mark.asyncio
    async def test_a_released_synonym_moves_without_its_new_holders_claim(
        self, client: AsyncClient, auth_headers: dict,
    ):
        old_id, _ = await _retired_doc(client, auth_headers)
        new = await _register_doc(client, auth_headers, "term-new")
        survivor = await _post(client, auth_headers, "/entries/register", [{
            "namespace": NS, "entity_type": "documents",
            "composite_key": {"ns": NS, "template_id": "TPL-930", "k": "survivor"},
        }])
        survivor_id = survivor["results"][0]["registry_id"]

        merged = await _post(client, auth_headers, "/synonyms/merge", [{
            "preferred_id": survivor_id, "deprecated_id": old_id,
        }])
        assert merged["results"][0]["status"] == "merged"
        # the spelling still belongs to the document that claimed it
        assert (await _by_key(client, auth_headers, SPELLING))["entry_id"] == new["registry_id"]
        assert (await _register_doc(client, auth_headers, "term-other"))["status"] == "error"


class TestReaddingAReleasedSynonym:
    @pytest.mark.asyncio
    async def test_a_free_key_is_claimed_again(self, client: AsyncClient, auth_headers: dict):
        old_id, _ = await _retired_doc(client, auth_headers)
        added = await _add_synonym(client, auth_headers, old_id, SPELLING)
        assert added["status"] == "added"
        assert (await _by_key(client, auth_headers, SPELLING))["entry_id"] == old_id
        found = await _by_id(client, auth_headers, old_id)
        spellings = [s for s in found["synonyms"] if "@template" in s["composite_key"]]
        assert [s["status"] for s in spellings] == ["active"]  # replaced, not duplicated

    @pytest.mark.asyncio
    async def test_a_taken_key_is_a_conflict_not_already_exists(
        self, client: AsyncClient, auth_headers: dict,
    ):
        old_id, _ = await _retired_doc(client, auth_headers)
        new = await _register_doc(client, auth_headers, "term-new")
        added = await _add_synonym(client, auth_headers, old_id, SPELLING)
        assert added["status"] == "error"
        assert added["error_code"] == "synonym_conflict"
        assert new["registry_id"] in added["error"]
