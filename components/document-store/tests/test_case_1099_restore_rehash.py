"""Restore rehash, final keys, reference namespaces and search values.

Provenance: CASE-1099.

- A fresh restore rehashes each document against the template version it is
  pinned to, from any archived namespace, not by template value within the
  document's own namespace.
- A template the archive does not carry is read from this install; a keyed
  document whose template is known nowhere is counted, not passed as clean.
- The merge and plain rehash read referent_identity_paths, so a dotted
  identity path into a nested template is rehashed.
- A document's final Registry key takes its template id from the rewritten
  row once — not through the id map again.
- A redirected merge renames references[].resolved.namespace with the rest.
- Registry rows a restore writes carry search_values derived from their keys.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig
from wip_archive.remap import IDRemapper

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    _IdentityRehash,
    registry_search_values,
)
from document_store.services.remap_restore import (
    RemapRestore,
    RemapSource,
    TemplateDefinitions,
    plan_multi,
)
from registry.models.entry import RegistryEntry
from wip_auth.document_identity import compute_hash, resolved_identity_values

TERM_FIELD = {"name": "kind", "type": "term", "terminology_ref": "KIND"}


def _provision_factory():
    counter = {"n": 0}

    def for_target(target: str):
        async def provision(entity_type, keys):
            ids = []
            for _ in keys:
                counter["n"] += 1
                ids.append(f"NEW-{counter['n']}")
            return ids
        return provision

    return for_target


def _term_doc(document_id: str, template_id: str, value: str, term_id: str, hash_: str) -> dict:
    return {
        "document_id": document_id, "template_id": template_id, "template_value": value,
        "template_version": 1, "version": 1, "identity_hash": hash_,
        "data": {"kind": "foo"},
        "term_references": [{"field_path": "kind", "term_id": term_id}],
    }


def _hash(term_id: str, fields=(TERM_FIELD,), referent_paths=None) -> str:
    return compute_hash(resolved_identity_values(
        {"kind": "foo"}, ["kind"], list(fields),
        [{"field_path": "kind", "term_id": term_id}], [], referent_paths,
    ))


# --- fresh: the pinned template, from any archived namespace --------------------


@pytest.mark.asyncio
async def test_a_document_on_another_namespaces_template_is_rehashed():
    """ns-a's document is pinned to ns-b's template; ns-a holds a template of
    the SAME value whose identity field is a plain string. The rehash must
    read ns-b's (term-typed) definition."""
    tpl_b = {"template_id": "T-B", "value": "THING", "version": 1,
             "identity_fields": ["kind"], "fields": [TERM_FIELD]}
    tpl_a = {"template_id": "T-A", "value": "THING", "version": 1,
             "identity_fields": ["kind"],
             "fields": [{"name": "kind", "type": "string"}]}
    doc = _term_doc("D", "T-B", "THING", "OLD-TERM", _hash("OLD-TERM"))
    sources = [
        RemapSource("ns-a", "copy-a", {"templates": [tpl_a], "documents": [doc]}),
        RemapSource("ns-b", "copy-b", {
            "terminologies": [{"terminology_id": "L", "value": "KIND", "label": "K"}],
            "terms": [{"term_id": "OLD-TERM", "terminology_id": "L", "value": "foo"}],
            "templates": [tpl_b],
        }),
    ]
    definitions = TemplateDefinitions.of_templates(
        t for s in sources for t in s.entities_by_type.get("templates", [])
    )
    for s in sources:
        s.template_definitions = definitions

    plans = await plan_multi(sources, IDRemapper(), _provision_factory())

    new_term = plans["ns-b"].id_map["terms"]["OLD-TERM"]
    (row,) = plans["ns-a"].rows["documents"]
    assert row["identity_hash"] == _hash(new_term)
    assert plans["ns-a"].rehashed_documents == 1


@pytest.mark.asyncio
async def test_a_keyed_document_on_an_unknown_template_is_counted():
    planner = RemapRestore(_provision_factory()("t"), IDRemapper())
    plan = await planner.plan(
        {"documents": [_term_doc("D", "T-ELSEWHERE", "THING", "X", "archived")]}, "t",
    )
    (row,) = plan.rows["documents"]
    assert row["identity_hash"] == "archived"
    assert plan.summary()["unrehashable_documents"] == 1


@pytest.mark.asyncio
async def test_an_external_template_is_read_from_this_install():
    """The definition of a template the archive references but does not
    carry comes from the template store, under the id it maps to here."""
    engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
    reader = MagicMock()
    reader.read_referents = MagicMock(return_value=[
        {"entity_type": "templates", "entry_id": "OLD-EXT"},
        {"entity_type": "templates", "entry_id": "T-CARRIED"},
        {"entity_type": "terms", "entry_id": "OLD-TERM"},
    ])
    manifest = Manifest(
        format_version="3.0", namespace="src",
        namespace_config=NamespaceConfig(
            namespace_id="019a0000-0000-7000-8000-000000001099", prefix="src", isolation_mode="open",
        ),
        counts=EntityCounts(), referents=3,
    )
    remapper = IDRemapper()
    remapper.add_template_mapping("OLD-EXT", "HERE-EXT")
    client = MagicMock()
    # Every version this install holds is read, so a document is rehashed
    # against the version it is pinned to (CASE-1279, d31e6b21 R2).
    client.get_template_version_numbers = AsyncMock(return_value=[2, 1])

    async def resolved(template_id, version=None):
        fields = [TERM_FIELD] if version == 1 else [TERM_FIELD, {"name": "x", "type": "string"}]
        return {"template_id": "HERE-EXT", "version": version, "identity_fields": ["kind"],
                "fields": fields, "referent_identity_paths": ["kind"]}

    client.get_template_resolved = AsyncMock(side_effect=resolved)
    with patch(
        "document_store.services.backup_engine.get_template_store_client",
        return_value=client,
    ):
        external = await engine._external_template_definitions(
            reader, manifest, remapper, {"T-CARRIED"},
        )
        # A second target of the same run asks the store nothing again.
        await engine._external_template_definitions(reader, manifest, remapper, {"T-CARRIED"})
    client.get_template_version_numbers.assert_awaited_once_with("HERE-EXT")
    assert client.get_template_resolved.await_count == 2
    assert set(external) == {("OLD-EXT", 1), ("OLD-EXT", 2)}
    assert external[("OLD-EXT", 1)]["fields"] == [TERM_FIELD]
    assert external[("OLD-EXT", 1)]["referent_paths"] == ["kind"]


# --- merge / plain: referent paths and external templates ------------------------


def _reader(templates: list[dict[str, Any]]) -> MagicMock:
    reader = MagicMock()
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [dict(t) for t in templates] if et == "templates" else []
    )
    return reader


def test_the_merge_rehash_reads_referent_paths():
    nested_fields = [
        {"name": "name", "type": "string"},
        {"name": "address", "type": "object", "template_ref": "ADDR", "template_ref_version": 1},
    ]
    template = {
        "template_id": "T", "version": 1, "identity_fields": ["name", "address.country"],
        "fields": nested_fields, "referent_identity_paths": ["address.country"],
    }
    data = {"name": "Ada", "address": {"country": "UK"}}

    def doc(term_id: str, hash_: str) -> dict:
        return {"document_id": "D", "template_id": "T", "template_version": 1,
                "identity_hash": hash_, "data": data,
                "term_references": [{"field_path": "address.country", "term_id": term_id}]}

    def expected(term_id: str) -> str:
        return compute_hash(resolved_identity_values(
            data, template["identity_fields"], nested_fields,
            [{"field_path": "address.country", "term_id": term_id}], [],
            ["address.country"],
        ))

    rehash = _IdentityRehash(_reader([template]), ["src"])
    row = rehash.rehash(doc("OLD", expected("OLD")), doc("NEW", expected("OLD")))
    assert row["identity_hash"] == expected("NEW")


def test_the_merge_rehash_reads_an_external_template():
    external = {("T-EXT", 1): {"identity_fields": ["kind"], "fields": [TERM_FIELD], "referent_paths": None}}
    rehash = _IdentityRehash(_reader([]), ["src"], external)
    archived = _term_doc("D", "T-EXT", "THING", "OLD", _hash("OLD"))
    row = rehash.rehash(archived, _term_doc("D", "T-EXT", "THING", "NEW", _hash("OLD")))
    assert row["identity_hash"] == _hash("NEW")


# --- final keys: the template id is mapped once ------------------------------------


@pytest.mark.asyncio
async def test_the_final_key_takes_the_rewritten_template_id():
    """Prefixed ids on a pre-created target: the id minted for T-1 is T-2,
    the archive's OTHER template's old id. Mapping the rewritten row again
    would key the document on T-2's new id (T-3)."""
    minted = iter(["T-2", "T-3", "D-NEW"])

    async def provision(entity_type, keys):
        return [next(minted) for _ in keys]

    planner = RemapRestore(provision, IDRemapper())
    plan = await planner.plan({
        "templates": [
            {"template_id": "T-1", "value": "ONE", "version": 1},
            {"template_id": "T-2", "value": "TWO", "version": 1},
        ],
        "documents": [{"document_id": "D", "template_id": "T-1", "template_value": "ONE",
                       "version": 1, "identity_hash": "h", "data": {}}],
    }, "t")
    (row,) = plan.rows["documents"]
    assert row["template_id"] == "T-2"
    assert plan.final_document_keys("t")["D-NEW"]["template_id"] == "T-2"


# --- redirected merge: reference snapshots name the target -------------------------


def test_a_redirect_renames_the_reference_snapshots():
    engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
    doc = {"namespace": "src", "references": [
        {"field_path": "a", "resolved": {"document_id": "X", "namespace": "src"}},
        {"field_path": "b", "resolved": {"document_id": "Y", "namespace": "third"}},
    ]}
    moved = engine._rewrite_namespace("documents", doc, "src", "tgt")
    assert moved["namespace"] == "tgt"
    assert [r["resolved"]["namespace"] for r in moved["references"]] == ["tgt", "third"]
    assert doc["references"][0]["resolved"]["namespace"] == "src"  # input untouched


# --- registry rows: search values follow the keys ----------------------------------


def _entry() -> dict[str, Any]:
    return {
        "entry_id": "E", "namespace": "tgt", "entity_type": "documents",
        "primary_composite_key": {"ns": "tgt", "template_id": "T", "identity_hash": "NEW-HASH"},
        "primary_composite_key_hash": "x",
        "synonyms": [
            {"namespace": "tgt", "entity_type": "documents", "composite_key": {"ns": "tgt", "ext": "A-1"},
             "composite_key_hash": "y"},
            {"namespace": "tgt", "entity_type": "documents", "composite_key": {"ns": "tgt", "ext": "OLD"},
             "composite_key_hash": "z", "status": "inactive"},
        ],
        "search_values": ["OLD-HASH", "src", "T"],
    }


@pytest.mark.asyncio
async def test_search_values_are_derived_as_the_registry_derives_them(client):
    """``client`` brings the Registry models up."""
    entry = _entry()
    model = RegistryEntry.model_validate({**entry, "search_values": []})
    model.rebuild_search_values()
    assert registry_search_values(entry) == model.search_values == ["A-1", "NEW-HASH", "T", "tgt"]


@pytest.mark.asyncio
async def test_restored_registry_rows_carry_search_values_of_their_keys():
    engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
    collection = MagicMock()
    collection.insert_many = AsyncMock()
    batch = [_entry()]
    await engine._insert_batch(collection, batch, "registry_entries")
    (written,) = collection.insert_many.await_args.args[0]
    assert written["search_values"] == ["A-1", "NEW-HASH", "T", "tgt"]


# --- inheritance and missing versions (CASE-1279, d31e6b21 R1/R3) ----------------


def test_an_inherited_identity_is_rehashed():
    """An archived child template stores neither its inherited fields nor its
    identity_fields: the rehash composes them along the pinned parent."""
    parent = {"template_id": "P", "version": 3, "identity_fields": ["kind"],
              "fields": [TERM_FIELD]}
    child = {"template_id": "C", "version": 1, "identity_fields": [], "extends": "P",
             "extends_version": 3, "fields": [{"name": "note", "type": "string"}]}
    rehash = _IdentityRehash(_reader([parent, child]), ["src"])
    archived = _term_doc("D", "C", "CHILD", "OLD", _hash("OLD"))
    row = rehash.rehash(archived, _term_doc("D", "C", "CHILD", "NEW", _hash("OLD")))
    assert row["identity_hash"] == _hash("NEW")


def test_a_version_the_archive_lacks_is_rehashed_by_another_version():
    """identity_fields cannot change between versions, so a document pinned to
    a version the archive does not carry is rehashed by one it does."""
    template = {"template_id": "T", "version": 2, "identity_fields": ["kind"],
                "fields": [TERM_FIELD]}
    rehash = _IdentityRehash(_reader([template]), ["src"])
    archived = _term_doc("D", "T", "THING", "OLD", _hash("OLD"))  # pinned to v1
    row = rehash.rehash(archived, _term_doc("D", "T", "THING", "NEW", _hash("OLD")))
    assert row["identity_hash"] == _hash("NEW")


def test_a_parent_known_nowhere_leaves_no_definition():
    child = {"template_id": "C", "version": 1, "identity_fields": [], "extends": "GONE",
             "extends_version": 1, "fields": []}
    definitions = TemplateDefinitions.of_templates([child])
    assert definitions.lookup({"template_id": "C", "template_version": 1}) is None


@pytest.mark.asyncio
async def test_an_unreadable_identity_is_counted_not_passed():
    """A keyed row whose identity values cannot be read keeps its hash and is
    counted, as one whose template is unknown is."""
    template = {"template_id": "T", "value": "THING", "version": 1,
                "identity_fields": ["missing"], "fields": [{"name": "missing", "type": "string"}]}
    definitions = TemplateDefinitions.of_templates([template])
    planner = RemapRestore(_provision_factory()("t"), IDRemapper(), template_definitions=definitions)
    plan = await planner.plan(
        {"templates": [template],
         "documents": [_term_doc("D", "T", "THING", "X", "archived")]}, "t",
    )
    assert plan.summary()["unrehashable_documents"] == 1
