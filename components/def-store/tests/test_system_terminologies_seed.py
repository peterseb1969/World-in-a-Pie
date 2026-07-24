"""Pin the seeded ontology relation types against their documented contract.

The relation types in ``_ONTOLOGY_RELATIONSHIP_TYPES`` are a SEEDED DEFAULT,
not an immutable set: apps can add their own types, and OBO Graph import
auto-creates any type it references. But the seeded baseline is what the served
surfaces advertise (``wip://conventions``, ``wip://data-model``, and the
``create_term_relations`` tool description generated from
``components/mcp-server/tools.yaml``). This test pins that baseline so it cannot
drift from the docs silently.

If you add or remove a seeded relation type, update this set AND those doc
surfaces in the same change, then regenerate ``_generated_schemas.py``.
"""

from def_store.services.system_terminologies import (
    SYSTEM_TERMINOLOGIES,
    TERM_RELATION_TYPES_TERMINOLOGY_VALUE,
)

# The relation types advertised by the served resources and tool descriptions.
EXPECTED_RELATION_TYPES = {
    "is_a",
    "has_subtype",
    "part_of",
    "has_part",
    "maps_to",
    "mapped_from",
    "related_to",
    "finding_site",
    "causative_agent",
    "regulates",
    "positively_regulates",
    "negatively_regulates",
}


def _relation_type_seed() -> dict:
    for term_def in SYSTEM_TERMINOLOGIES:
        if term_def["value"] == TERM_RELATION_TYPES_TERMINOLOGY_VALUE:
            return term_def
    raise AssertionError(
        f"{TERM_RELATION_TYPES_TERMINOLOGY_VALUE} not seeded in SYSTEM_TERMINOLOGIES"
    )


def test_relation_type_seed_matches_documented_set():
    """Seeded relation types are exactly the set the docs advertise."""
    seeded = {t["value"] for t in _relation_type_seed()["terms"]}
    assert seeded == EXPECTED_RELATION_TYPES, (
        "Seeded ontology relation types drifted from the documented set. "
        "Update the seed AND the wip://conventions / wip://data-model resource "
        "text plus components/mcp-server/tools.yaml, then regenerate schemas."
    )


def test_relation_type_seed_values_unique():
    """No duplicate relation-type values in the seed."""
    values = [t["value"] for t in _relation_type_seed()["terms"]]
    assert len(values) == len(set(values))
