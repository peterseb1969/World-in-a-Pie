"""A merge compares a definition's references by what they name, not by id.

Provenance: CASE-1095 R1. The definitions pass compared every id-valued field
byte for byte: a template's terminology_ref / template_ref / extends, a term's
parent_term_id. Across installs those ids always differ, so identical
definitions were refused as "different content" for any template that
references something. References are now compared after mapping: through the
mappings the pass itself discovers (same-type references included, which is why
a type is matched whole before any of it is compared) and through the external
mappings for referenced entities the archive does not carry.
"""

from __future__ import annotations

import pytest
from wip_archive.remap import IDRemapper

from .test_merge_definitions import (  # noqa: F401
    NAMESPACE,
    _planner,
    _seed,
    _template,
    _term,
    _terminology,
    db,
)


def _term_field(terminology_id: str) -> dict:
    return {"name": "gender", "type": "term", "terminology_ref": terminology_id}


def _nested_field(template_id: str) -> dict:
    return {"name": "address", "type": "object",
            "template_ref": template_id, "template_ref_version": 1}


@pytest.mark.asyncio
async def test_a_template_referencing_a_terminology_from_another_install_merges(db):  # noqa: F811
    await _seed(db, "terminologies", [_terminology("T-TGT")])
    await _seed(db, "templates", [_template("P-TGT", fields=[_term_field("T-TGT")])])
    plan = await _planner(db).plan({
        "terminologies": [_terminology("T-ARC")],
        "templates": [_template("P-ARC", fields=[_term_field("T-ARC")])],
    }, NAMESPACE)
    assert plan.incompatibilities == []
    assert plan.mapping["templates"] == {"P-ARC": "P-TGT"}
    assert plan.unchanged["templates"] == 1


@pytest.mark.asyncio
async def test_a_real_difference_in_a_reference_is_still_refused(db):  # noqa: F811
    await _seed(db, "terminologies", [_terminology("T-TGT"), _terminology("C-TGT", value="COUNTRY")])
    await _seed(db, "templates", [_template("P-TGT", fields=[_term_field("C-TGT")])])
    plan = await _planner(db).plan({
        "terminologies": [_terminology("T-ARC"), _terminology("C-ARC", value="COUNTRY")],
        "templates": [_template("P-ARC", fields=[_term_field("T-ARC")])],
    }, NAMESPACE)
    assert [i.name for i in plan.incompatibilities] == ["PATIENT v1"]
    assert "fields" in plan.incompatibilities[0].reason


@pytest.mark.asyncio
async def test_same_type_references_extends_and_template_ref_merge(db):  # noqa: F811
    """The archive lists the referencing template before the one it names:
    the comparison waits until the whole type is matched."""
    await _seed(db, "templates", [
        _template("A-TGT", value="ADDRESS"),
        _template("P-TGT", fields=[_nested_field("A-TGT")]),
        _template("E-TGT", value="EMPLOYEE", extends="P-TGT", extends_version=1),
    ])
    plan = await _planner(db).plan({"templates": [
        _template("E-ARC", value="EMPLOYEE", extends="P-ARC", extends_version=1),
        _template("P-ARC", fields=[_nested_field("A-ARC")]),
        _template("A-ARC", value="ADDRESS"),
    ]}, NAMESPACE)
    assert plan.incompatibilities == []
    assert plan.unchanged["templates"] == 3


@pytest.mark.asyncio
async def test_a_terms_parent_merges_through_the_term_mapping(db):  # noqa: F811
    await _seed(db, "terminologies", [_terminology("T-TGT")])
    await _seed(db, "terms", [
        _term("CHILD-TGT", "T-TGT", value="BOY", parent_term_id="M-TGT"),
        _term("M-TGT", "T-TGT", value="M"),
    ])
    plan = await _planner(db).plan({
        "terminologies": [_terminology("T-ARC")],
        "terms": [
            _term("CHILD-ARC", "T-ARC", value="BOY", parent_term_id="M-ARC"),
            _term("M-ARC", "T-ARC", value="M"),
        ],
    }, NAMESPACE)
    assert plan.incompatibilities == []
    assert plan.unchanged["terms"] == 2


@pytest.mark.asyncio
async def test_a_reference_outside_the_archive_compares_through_the_external_mapping(db):  # noqa: F811
    """A terminology in another namespace (wip's shared vocabularies) is not
    in the archive; the referent table maps it, and the comparison uses that."""
    await _seed(db, "templates", [_template("P-TGT", fields=[_term_field("WIP-T-TGT")])])
    external = IDRemapper()
    external.add_terminology_mapping("WIP-T-ARC", "WIP-T-TGT")
    plan = await _planner(db).plan(
        {"templates": [_template("P-ARC", fields=[_term_field("WIP-T-ARC")])]},
        NAMESPACE, external=external,
    )
    assert plan.incompatibilities == []

    without = await _planner(db).plan(
        {"templates": [_template("P-ARC", fields=[_term_field("WIP-T-ARC")])]}, NAMESPACE,
    )
    assert [i.name for i in without.incompatibilities] == ["PATIENT v1"]
