"""Multi-namespace fresh restore planning.

What multi adds over single is exactly what can fail silently: a reference
crossing archived namespaces that stays on its old id, a Registry key built
from an old parent id, and two sources collapsing into one target whose
same-shaped keys would silently MERGE under the Registry's upsert instead of
failing. These tests pin all three, plus the mapping-resolution contract.
"""

from __future__ import annotations

import pytest
from wip_archive.remap import IDRemapper

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    RestoreEngineError,
)
from document_store.services.remap_restore import (
    RemapCollisionError,
    RemapSource,
    plan_multi,
)


def _provision_factory():
    """Per-target Registry stand-in; records every call with its target."""
    calls: list[tuple[str, str, list[dict]]] = []
    counter = {"n": 0}

    def for_target(target: str):
        async def provision(entity_type, keys):
            calls.append((target, entity_type, keys))
            ids = []
            for _ in keys:
                counter["n"] += 1
                ids.append(f"NEW-{target}-{counter['n']}")
            return ids
        return provision

    for_target.calls = calls  # type: ignore[attr-defined]
    return for_target


def _template(tid, value):
    return {"template_id": tid, "value": value, "version": 1}


def _doc(did, tid, value, data, identity_hash="hash-1"):
    return {
        "document_id": did, "template_id": tid, "template_value": value,
        "data": data, "identity_hash": identity_hash, "version": 1,
    }


@pytest.mark.asyncio
async def test_cross_source_template_pin_follows_the_new_id():
    """A ns-a document pinned to ns-b's template (legal on the live create
    path) must end up on that template's NEW id — in its rewritten row AND
    in the Registry key it was provisioned under. Source-major planning
    fails both; a per-source id_map fails the second."""
    tpl_b = _template("OLD-TPL-B", "SHARED_SCHEMA")
    doc_a = _doc("OLD-DOC-A", "OLD-TPL-B", "SHARED_SCHEMA", {"name": "x"})

    factory = _provision_factory()
    plans = await plan_multi(
        [
            RemapSource("ns-a", "copy-a", {"documents": [doc_a]}),
            RemapSource("ns-b", "copy-b", {"templates": [tpl_b]}),
        ],
        IDRemapper(),
        factory,
    )

    new_tpl_id = plans["ns-b"].id_map["templates"]["OLD-TPL-B"]
    row = plans["ns-a"].rows["documents"][0]
    assert row["template_id"] == new_tpl_id
    assert row["namespace"] == "copy-a"

    doc_calls = [c for c in factory.calls if c[1] == "documents"]
    assert doc_calls[0][0] == "copy-a"
    assert doc_calls[0][2][0]["template_id"] == new_tpl_id


@pytest.mark.asyncio
async def test_n_to_one_key_collision_refuses():
    """Same template value from two sources into one target: the Registry
    upsert would silently merge them — the plan refuses instead."""
    with pytest.raises(RemapCollisionError) as exc:
        await plan_multi(
            [
                RemapSource("ns-a", "one", {"templates": [_template("T-A", "TRIAL")]}),
                RemapSource("ns-b", "one", {"templates": [_template("T-B", "TRIAL")]}),
            ],
            IDRemapper(),
            _provision_factory(),
        )
    assert "ns-a" in str(exc.value) and "ns-b" in str(exc.value)
    assert "merge" in str(exc.value)


@pytest.mark.asyncio
async def test_n_to_one_terminology_collision_refuses():
    """R-07 (CASE-773 matrix): the template collision above is one half of the
    N:1 guard; a same-valued TERMINOLOGY is the other. Two 'MATRIX_STATUS'
    terminologies collapsing from two sources into one target share the Registry
    key {ns, value, label}, so the upsert would silently MERGE them — and their
    terms would then re-parent under a single terminology id. The plan must
    refuse. The refusal is provisioner-independent (_check_target_collisions
    runs BEFORE any provisioning), so the dry run refuses the SAME cell — the
    gap the mapping flagged: no test had a dry run genuinely refuse a colliding
    cell (only the false-refusal it must avoid). The Phase-1 fixture builds the
    same-valued MATRIX_STATUS pair in NS-A/NS-B precisely for this."""
    def _colliding_sources():
        return [
            RemapSource("ns-a", "one", {"terminologies": [
                {"terminology_id": "L-A", "value": "MATRIX_STATUS",
                 "label": "Matrix Status"}]}),
            RemapSource("ns-b", "one", {"terminologies": [
                {"terminology_id": "L-B", "value": "MATRIX_STATUS",
                 "label": "Matrix Status"}]}),
        ]

    # Apply path (real provisioner) refuses, naming both sources and the merge.
    with pytest.raises(RemapCollisionError) as exc:
        await plan_multi(_colliding_sources(), IDRemapper(), _provision_factory())
    assert "ns-a" in str(exc.value) and "ns-b" in str(exc.value)
    assert "merge" in str(exc.value)

    # Dry run refuses the SAME cell — the check precedes provisioning, so the
    # placeholder-minting dry-run provisioner reaches the same verdict.
    with pytest.raises(RemapCollisionError):
        await plan_multi(
            _colliding_sources(),
            IDRemapper(),
            DirectRestoreEngine._dry_run_provisioner(),
        )


@pytest.mark.asyncio
async def test_n_to_one_disjoint_content_lands_in_one_target():
    factory = _provision_factory()
    plans = await plan_multi(
        [
            RemapSource("ns-a", "one", {"templates": [_template("T-A", "ALPHA")]}),
            RemapSource("ns-b", "one", {"templates": [_template("T-B", "BETA")]}),
        ],
        IDRemapper(),
        factory,
    )
    assert plans["ns-a"].rows["templates"][0]["namespace"] == "one"
    assert plans["ns-b"].rows["templates"][0]["namespace"] == "one"
    new_ids = {
        plans["ns-a"].id_map["templates"]["T-A"],
        plans["ns-b"].id_map["templates"]["T-B"],
    }
    assert len(new_ids) == 2


@pytest.mark.asyncio
async def test_reference_snapshots_carry_no_trace_of_the_source():
    """CASE-743, Peter's ruling: after a fresh restore NO data points at the
    original namespaces. The reference snapshot's denormalized namespace
    follows the map, and a lookup_value that was a canonical id follows its
    entity. A sweep over the serialized row catches any future snapshot
    field that would reintroduce the leak."""
    import json

    tpl_b = _template("OLD-TPL-B", "SHARED_SCHEMA")
    doc_b = _doc("OLD-DOC-B", "OLD-TPL-B", "SHARED_SCHEMA", {"name": "target"})
    # The reference lives in BOTH halves of the document: the data payload
    # (here as an array-of-references value — the shape CASE-746 caught
    # leaking — plus a QUALIFIED '<ns>:<id>' scalar, the form the platform
    # actually stores for a cross-namespace ref; the live R-06 matrix cell
    # caught the bare-only fixture missing it, CASE-814) and the
    # references[] snapshot. The serialized-row sweep below must see old ids
    # in neither.
    doc_a = _doc(
        "OLD-DOC-A", "OLD-TPL-B", "SHARED_SCHEMA",
        {"name": "src", "links": ["OLD-DOC-B"], "primary": "ns-b:OLD-DOC-B"},
    )
    doc_a["references"] = [{
        "field_path": "links",
        "reference_type": "document",
        "lookup_value": "OLD-DOC-B",
        "resolved": {
            "document_id": "OLD-DOC-B", "template_id": "OLD-TPL-B",
            "identity_hash": "h", "namespace": "ns-b", "version": 1,
        },
    }, {
        "field_path": "primary",
        "reference_type": "document",
        "lookup_value": "ns-b:OLD-DOC-B",
        "resolved": {
            "document_id": "OLD-DOC-B", "template_id": "OLD-TPL-B",
            "identity_hash": "h", "namespace": "ns-b", "version": 1,
        },
    }]

    remapper = IDRemapper(namespace_map={"ns-a": "copy-a", "ns-b": "copy-b"})
    plans = await plan_multi(
        [
            RemapSource("ns-a", "copy-a", {"documents": [doc_a]}),
            RemapSource("ns-b", "copy-b", {"templates": [tpl_b], "documents": [doc_b]}),
        ],
        remapper,
        _provision_factory(),
    )

    row = next(
        r for r in plans["ns-a"].rows["documents"]
        if r["data"]["name"] == "src"
    )
    ref = row["references"][0]
    new_doc_b = plans["ns-b"].id_map["documents"]["OLD-DOC-B"]
    assert ref["resolved"]["namespace"] == "copy-b"
    assert ref["resolved"]["document_id"] == new_doc_b
    assert ref["lookup_value"] == new_doc_b
    assert row["data"]["links"] == [new_doc_b]
    # The qualified form is rewritten in BOTH halves and keeps its qualified
    # shape — a bare value resolves own-namespace only, so collapsing it
    # would change resolution semantics.
    assert row["data"]["primary"] == f"copy-b:{new_doc_b}"
    assert row["references"][1]["lookup_value"] == f"copy-b:{new_doc_b}"

    # Sweep: nothing anywhere in the row mentions an old id or source ns —
    # including the qualified prefix forms ('ns-b:…'), which the quoted
    # tokens ('"ns-b"') cannot match.
    blob = json.dumps(row)
    for old in ("OLD-DOC-A", "OLD-DOC-B", "OLD-TPL-B",
                '"ns-a"', '"ns-b"', "ns-a:", "ns-b:"):
        assert old not in blob, f"{old} leaked into {blob[:200]}"


def _resolve(sources, target="", nsmap=None):
    return DirectRestoreEngine._resolve_remap_mapping(
        None, sources, target, nsmap  # type: ignore[arg-type]
    )


class TestResolveRemapMapping:
    def test_map_must_cover_every_archive_namespace(self):
        with pytest.raises(RestoreEngineError, match="unmapped"):
            _resolve(["a", "b"], nsmap={"a": "x"})

    def test_map_must_not_name_strangers(self):
        with pytest.raises(RestoreEngineError, match="not in the archive"):
            _resolve(["a"], nsmap={"a": "x", "ghost": "y"})

    def test_empty_target_refused(self):
        with pytest.raises(RestoreEngineError, match="empty target"):
            _resolve(["a"], nsmap={"a": ""})

    def test_multi_namespace_without_map_refused(self):
        with pytest.raises(RestoreEngineError, match="namespace_map"):
            _resolve(["a", "b"], target="somewhere")

    def test_single_namespace_sugar_still_works(self):
        assert _resolve(["a"], target="copy") == {"a": "copy"}

    def test_archive_order_wins_over_caller_order(self):
        got = _resolve(["a", "b"], nsmap={"b": "y", "a": "x"})
        assert list(got) == ["a", "b"]


@pytest.mark.asyncio
async def test_dry_run_placeholders_unique_across_sources():
    """An N:1 dry run must not report collisions the real run would never
    hit. Registry-minted ids are globally unique; the dry-run placeholders
    must be too. With a placeholder counter per SOURCE, two sources'
    terminologies get the same stand-in id, their terms' Registry keys then
    embed identical parents, and the collapse falsely refuses — found live
    on a kb+library two-into-one dry run whose real run succeeds."""
    from document_store.services.backup_engine import DirectRestoreEngine

    def _terminology(tid, value):
        return {"terminology_id": tid, "value": value, "label": value}

    def _term(tid, parent, value):
        return {"term_id": tid, "terminology_id": parent, "value": value}

    provision_for = DirectRestoreEngine._dry_run_provisioner()
    plans = await plan_multi(
        [
            RemapSource("kb", "one", {
                "terminologies": [_terminology("T-KB", "KB_STATUS")],
                "terms": [_term("TM-KB", "T-KB", "draft")],
            }),
            RemapSource("library", "one", {
                "terminologies": [_terminology("T-LIB", "LIB_STATUS")],
                "terms": [_term("TM-LIB", "T-LIB", "draft")],
            }),
        ],
        IDRemapper(),
        provision_for,
    )

    kb_parent = plans["kb"].id_map["terminologies"]["T-KB"]
    lib_parent = plans["library"].id_map["terminologies"]["T-LIB"]
    assert kb_parent != lib_parent, (
        "dry-run placeholders collided across sources — same-valued terms "
        "under different terminologies would falsely refuse the collapse"
    )
