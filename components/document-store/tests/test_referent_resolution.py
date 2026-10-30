"""Resolving an archive's out-of-archive referents on the target install.

The Registry is stubbed: each test says which composite keys this install
knows, and the resolver must map a referent only when its keys agree on
exactly one entity.
"""

from __future__ import annotations

import json

import pytest

from document_store.services import referent_resolution
from document_store.services.referent_resolution import referent_keys, resolve_referents


def _registry(known: dict[str, str]):
    """A resolve function answering from {canonical key json: entry_id}."""
    calls: list[list[dict]] = []

    async def resolve(items):
        calls.append(items)
        return [
            known.get(json.dumps(
                {"key": i["composite_key"], "ns": i["namespace"], "type": i["entity_type"]},
                sort_keys=True,
            ))
            for i in items
        ]

    resolve.calls = calls
    return resolve


def _k(key, ns, entity_type):
    return json.dumps({"key": key, "ns": ns, "type": entity_type}, sort_keys=True)


TERM_ROW = {
    "entry_id": "SRC-TERM",
    "namespace": "wip",
    "entity_type": "terms",
    "primary_composite_key": {"ns": "wip", "terminology_id": "SRC-TY", "value": "seconds"},
    "synonyms": [{
        "namespace": "wip", "entity_type": "terms",
        "composite_key": {"ns": "wip", "type": "term", "terminology": "_TIME_UNITS",
                          "value": "seconds"},
    }],
}

DOC_ROW = {
    "entry_id": "SRC-DOC",
    "namespace": "other",
    "entity_type": "documents",
    "primary_composite_key": {"ns": "other", "template_id": "SRC-TPL", "identity_hash": "h"},
    "synonyms": [{
        "namespace": "other", "entity_type": "documents",
        "composite_key": {"@template": "BOSS", "code": "B-1"},
    }],
}


class TestKeys:
    def test_primary_then_every_synonym_with_retired_entities_included(self):
        items = referent_keys(TERM_ROW)
        assert [i["composite_key"] for i in items] == [
            TERM_ROW["primary_composite_key"], TERM_ROW["synonyms"][0]["composite_key"],
        ]
        assert all(i["namespace"] == "wip" and i["entity_type"] == "terms" for i in items)
        assert all(i["include_statuses"] == ["active", "inactive"] for i in items)
        assert all("entry_id" not in i for i in items)  # an old id is not portable


class TestResolution:
    @pytest.mark.asyncio
    async def test_same_install_every_key_finds_the_referent_itself(self):
        resolve = _registry({
            _k(TERM_ROW["primary_composite_key"], "wip", "terms"): "SRC-TERM",
            _k(TERM_ROW["synonyms"][0]["composite_key"], "wip", "terms"): "SRC-TERM",
        })
        result = await resolve_referents([TERM_ROW], resolve)
        assert result.mapping == {"terms": {"SRC-TERM": "SRC-TERM"}}
        assert result.unresolved == []

    @pytest.mark.asyncio
    async def test_another_install_finds_it_through_the_portable_synonym(self):
        # The document's primary key embeds the source's template id, which
        # means nothing here; its identity synonym does.
        resolve = _registry({
            _k(DOC_ROW["synonyms"][0]["composite_key"], "other", "documents"): "HERE-DOC",
        })
        result = await resolve_referents([DOC_ROW], resolve)
        assert result.mapping == {"documents": {"SRC-DOC": "HERE-DOC"}}

    @pytest.mark.asyncio
    async def test_nothing_found_is_reported_with_the_readable_key(self):
        result = await resolve_referents([TERM_ROW], _registry({}))
        assert result.mapping == {}
        (miss,) = result.unresolved
        assert miss.reason == "not_found"
        assert miss.key == TERM_ROW["synonyms"][0]["composite_key"]
        assert "_TIME_UNITS" in miss.describe()

    @pytest.mark.asyncio
    async def test_keys_naming_different_entities_are_never_guessed(self):
        resolve = _registry({
            _k(TERM_ROW["primary_composite_key"], "wip", "terms"): "ONE",
            _k(TERM_ROW["synonyms"][0]["composite_key"], "wip", "terms"): "TWO",
        })
        result = await resolve_referents([TERM_ROW], resolve)
        assert result.mapping == {}
        (miss,) = result.unresolved
        assert miss.reason == "ambiguous"
        assert miss.candidates == ["ONE", "TWO"]

    @pytest.mark.asyncio
    async def test_the_registry_is_asked_in_batches(self, monkeypatch):
        monkeypatch.setattr(referent_resolution, "RESOLVE_BATCH", 3)
        rows = [dict(TERM_ROW, entry_id=f"T{i}") for i in range(4)]  # 2 keys each
        resolve = _registry({})
        await resolve_referents(rows, resolve)
        assert [len(c) for c in resolve.calls] == [3, 3, 2]
