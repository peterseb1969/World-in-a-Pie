"""Fresh / remap restore planning.

This mode keeps no identity: every entity is registered afresh and every
reference between them is rewritten. The two things worth pinning are the ones
that fail silently — a composite key of the wrong shape creates an identity
nothing will ever match, and a reference left pointing at an old id resolves to
nothing.

The Registry is injected as a provisioner rather than called over HTTP, so the
orchestration — key shapes, dependency order, reference rewriting — is tested
without a live instance. Whether the endpoint behaves is pinned separately, in
the registry component's reservation-lifecycle tests.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from wip_toolkit.import_.remap import IDRemapper

from document_store.services.remap_restore import (
    REMAP_ENTITY_ORDER,
    RemapRestore,
    composite_key_for,
)
from wip_auth.document_identity import compute_hash

NAMESPACE = "remapped-ns"


def _provisioner(prefix="NEW"):
    """A Registry stand-in that mints predictable ids and records its calls."""
    calls: list[tuple[str, list[dict]]] = []
    counter = {"n": 0}

    async def provision(entity_type, keys):
        calls.append((entity_type, keys))
        ids = []
        for _ in keys:
            counter["n"] += 1
            ids.append(f"{prefix}-{counter['n']}")
        return ids

    provision.calls = calls  # type: ignore[attr-defined]
    return provision


def _planner(provision=None, identity_fields=None):
    return RemapRestore(
        provision or _provisioner(),
        IDRemapper(),
        identity_fields_by_template=identity_fields,
    )


# ---------------------------------------------------------------------------
# Composite key shapes
# ---------------------------------------------------------------------------


class TestCompositeKeyShapes:
    """A key of the wrong shape does not fail — it creates an identity
    nothing else will ever match. Each shape mirrors the owning service."""

    EMPTY_MAP: ClassVar[dict[str, dict[str, str]]] = {
        t: {} for t in REMAP_ENTITY_ORDER
    }

    def test_terminology_key(self):
        assert composite_key_for(
            "terminologies",
            {"value": "GENDER", "label": "Gender"},
            NAMESPACE,
            self.EMPTY_MAP,
        ) == {"ns": NAMESPACE, "value": "GENDER", "label": "Gender"}

    def test_term_key_uses_the_parents_new_id(self):
        id_map = {**self.EMPTY_MAP, "terminologies": {"OLD-LOV": "NEW-LOV"}}
        assert composite_key_for(
            "terms", {"terminology_id": "OLD-LOV", "value": "M"}, NAMESPACE, id_map
        ) == {"ns": NAMESPACE, "terminology_id": "NEW-LOV", "value": "M"}

    def test_template_key(self):
        assert composite_key_for(
            "templates", {"value": "PATIENT"}, NAMESPACE, self.EMPTY_MAP
        ) == {"ns": NAMESPACE, "type": "template", "value": "PATIENT"}

    def test_file_key_is_the_checksum_alone(self):
        # Deliberately without ns — the entry's own namespace scopes it.
        assert composite_key_for(
            "files", {"checksum": "abc123"}, NAMESPACE, self.EMPTY_MAP
        ) == {"checksum": "abc123"}

    def test_document_key_carries_the_identity_hash(self):
        id_map = {**self.EMPTY_MAP, "templates": {"OLD-TPL": "NEW-TPL"}}
        assert composite_key_for(
            "documents",
            {"template_id": "OLD-TPL", "identity_hash": "h1"},
            NAMESPACE,
            id_map,
        ) == {"ns": NAMESPACE, "template_id": "NEW-TPL", "identity_hash": "h1"}

    def test_an_identity_less_document_gets_an_empty_key(self):
        # Append-only: inventing a key would give the row an identity the
        # platform says it does not have.
        assert composite_key_for(
            "documents",
            {"template_id": "OLD-TPL", "identity_hash": ""},
            NAMESPACE,
            self.EMPTY_MAP,
        ) == {}


# ---------------------------------------------------------------------------
# Provisioning and rewriting
# ---------------------------------------------------------------------------


class TestRemapPlanning:
    @pytest.mark.asyncio
    async def test_every_entity_gets_a_new_id(self):
        planner = _planner()
        plan = await planner.plan(
            {"terminologies": [{"terminology_id": "OLD", "value": "GENDER"}]},
            NAMESPACE,
        )

        assert plan.id_map["terminologies"] == {"OLD": "NEW-1"}
        (row,) = plan.rows["terminologies"]
        assert row["terminology_id"] == "NEW-1"
        assert row["namespace"] == NAMESPACE

    @pytest.mark.asyncio
    async def test_children_point_at_the_parents_new_id(self):
        planner = _planner()
        plan = await planner.plan(
            {
                "terminologies": [{"terminology_id": "OLD-LOV", "value": "GENDER"}],
                "terms": [
                    {"term_id": "OLD-T", "terminology_id": "OLD-LOV", "value": "M"},
                ],
            },
            NAMESPACE,
        )

        (term,) = plan.rows["terms"]
        assert term["terminology_id"] == plan.id_map["terminologies"]["OLD-LOV"]
        assert term["term_id"] != "OLD-T"

    @pytest.mark.asyncio
    async def test_documents_point_at_the_templates_new_id(self):
        planner = _planner()
        plan = await planner.plan(
            {
                "templates": [{"template_id": "OLD-TPL", "value": "PATIENT"}],
                "documents": [{
                    "document_id": "OLD-DOC",
                    "template_id": "OLD-TPL",
                    "identity_hash": "h1",
                    "data": {"age": 1},
                }],
            },
            NAMESPACE,
        )

        (doc,) = plan.rows["documents"]
        assert doc["template_id"] == plan.id_map["templates"]["OLD-TPL"]

    @pytest.mark.asyncio
    async def test_document_to_document_references_are_rewritten(self):
        planner = _planner()
        plan = await planner.plan(
            {
                "templates": [{"template_id": "T", "value": "PATIENT"}],
                "documents": [
                    {"document_id": "D-A", "template_id": "T", "identity_hash": "ha"},
                    {
                        "document_id": "D-B", "template_id": "T",
                        "identity_hash": "hb",
                        "references": [{
                            "field_path": "supervisor",
                            "resolved": {"document_id": "D-A"},
                        }],
                    },
                ],
            },
            NAMESPACE,
        )

        rows = {r["document_id"]: r for r in plan.rows["documents"]}
        new_a = plan.id_map["documents"]["D-A"]
        pointer = rows[plan.id_map["documents"]["D-B"]]
        assert pointer["references"][0]["resolved"]["document_id"] == new_a

    @pytest.mark.asyncio
    async def test_term_relations_follow_their_endpoints(self):
        # A relation IS its endpoints; it has no id of its own to mint.
        planner = _planner()
        plan = await planner.plan(
            {
                "terminologies": [{"terminology_id": "LOV", "value": "G"}],
                "terms": [
                    {"term_id": "A", "terminology_id": "LOV", "value": "a"},
                    {"term_id": "B", "terminology_id": "LOV", "value": "b"},
                ],
                "term_relations": [{
                    "source_term_id": "A", "target_term_id": "B",
                    "relation_type": "is_a",
                }],
            },
            NAMESPACE,
        )

        (relation,) = plan.rows["term_relations"]
        assert relation["source_term_id"] == plan.id_map["terms"]["A"]
        assert relation["target_term_id"] == plan.id_map["terms"]["B"]
        assert relation["relation_type"] == "is_a"
        assert relation["namespace"] == NAMESPACE

    @pytest.mark.asyncio
    async def test_types_are_provisioned_in_dependency_order(self):
        # A term's key embeds its terminology's NEW id, so the parent has to
        # be provisioned first or the key is built from a stale reference.
        provision = _provisioner()
        planner = _planner(provision)
        await planner.plan(
            {
                "terminologies": [{"terminology_id": "LOV", "value": "G"}],
                "terms": [{"term_id": "T", "terminology_id": "LOV", "value": "x"}],
                "templates": [{"template_id": "TPL", "value": "P"}],
            },
            NAMESPACE,
        )

        assert [call[0] for call in provision.calls] == [
            "terminologies", "terms", "templates",
        ]

    @pytest.mark.asyncio
    async def test_all_versions_of_a_document_share_one_new_id(self):
        # Versions of one document are one identity to the Registry: they
        # share (template_id, identity_hash), so asking for an id per ROW
        # collides on the second version. Found live — 65,923 document rows
        # returned 409 on the first chunk.
        provision = _provisioner()
        planner = _planner(provision)

        plan = await planner.plan(
            {
                "templates": [{"template_id": "T", "value": "PATIENT"}],
                "documents": [
                    {"document_id": "D1", "template_id": "T",
                     "identity_hash": "h1", "version": v}
                    for v in (1, 2, 3)
                ],
            },
            NAMESPACE,
        )

        doc_calls = [c for c in provision.calls if c[0] == "documents"]
        assert len(doc_calls[0][1]) == 1, "one key for one document, not one per version"
        rows = plan.rows["documents"]
        assert len(rows) == 3
        assert len({r["document_id"] for r in rows}) == 1
        assert sorted(r["version"] for r in rows) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_all_versions_of_a_template_share_one_new_id(self):
        # Same for templates: every version shares the template's value, so
        # the composite key {ns, type, value} is one identity.
        provision = _provisioner()
        planner = _planner(provision)

        plan = await planner.plan(
            {"templates": [
                {"template_id": "T", "value": "PATIENT", "version": v}
                for v in (1, 2)
            ]},
            NAMESPACE,
        )

        tpl_calls = [c for c in provision.calls if c[0] == "templates"]
        assert len(tpl_calls[0][1]) == 1
        rows = plan.rows["templates"]
        assert len(rows) == 2
        assert len({r["template_id"] for r in rows}) == 1

    @pytest.mark.asyncio
    async def test_distinct_documents_still_get_distinct_ids(self):
        planner = _planner()
        plan = await planner.plan(
            {"documents": [
                {"document_id": "D1", "template_id": "T", "identity_hash": "h1"},
                {"document_id": "D2", "template_id": "T", "identity_hash": "h2"},
            ]},
            NAMESPACE,
        )

        assert len({r["document_id"] for r in plan.rows["documents"]}) == 2

    @pytest.mark.asyncio
    async def test_a_short_provision_response_refuses_to_guess(self):
        async def stingy(entity_type, keys):
            return ["ONLY-ONE"]

        planner = _planner(stingy)

        with pytest.raises(ValueError, match="refusing to guess"):
            await planner.plan(
                {"terminologies": [
                    {"terminology_id": "A", "value": "1"},
                    {"terminology_id": "B", "value": "2"},
                ]},
                NAMESPACE,
            )

    @pytest.mark.asyncio
    async def test_nothing_to_do_is_an_empty_plan(self):
        plan = await _planner().plan({}, NAMESPACE)
        assert all(not rows for rows in plan.rows.values())


# ---------------------------------------------------------------------------
# Identity hashes
# ---------------------------------------------------------------------------


class TestIdentityHashes:
    """A value-based hash survives re-minting — unless the values are ids."""

    @pytest.mark.asyncio
    async def test_a_value_based_hash_is_left_alone(self):
        stored = compute_hash({"patient_id": "P-1"})
        planner = _planner(identity_fields={"PATIENT": ["patient_id"]})

        plan = await planner.plan(
            {
                "templates": [{"template_id": "T", "value": "PATIENT"}],
                "documents": [{
                    "document_id": "D", "template_id": "T",
                    "template_value": "PATIENT",
                    "identity_hash": stored,
                    "data": {"patient_id": "P-1"},
                }],
            },
            NAMESPACE,
        )

        assert plan.rows["documents"][0]["identity_hash"] == stored
        assert plan.rehashed_documents == 0

    @pytest.mark.asyncio
    async def test_an_id_valued_identity_field_forces_a_rehash(self):
        # The identity field holds a document reference, and that id has just
        # changed. Left alone the hash would no longer describe the document's
        # own data — wrong silently, and the next write on that identity would
        # land somewhere else.
        planner = _planner(identity_fields={"VISIT": ["patient_ref"]})

        plan = await planner.plan(
            {
                "templates": [{"template_id": "T", "value": "VISIT"}],
                "documents": [
                    {
                        "document_id": "D-PATIENT", "template_id": "T",
                        "identity_hash": "hp",
                    },
                    {
                        "document_id": "D-VISIT", "template_id": "T",
                        "template_value": "VISIT",
                        "identity_hash": compute_hash({"patient_ref": "D-PATIENT"}),
                        "data": {"patient_ref": "D-PATIENT"},
                    },
                ],
            },
            NAMESPACE,
        )

        new_patient = plan.id_map["documents"]["D-PATIENT"]
        visit = next(
            r for r in plan.rows["documents"] if r.get("template_value") == "VISIT"
        )
        assert visit["data"]["patient_ref"] == new_patient
        assert visit["identity_hash"] == compute_hash({"patient_ref": new_patient})
        assert plan.rehashed_documents == 1

    @pytest.mark.asyncio
    async def test_a_missing_identity_field_leaves_the_hash_as_archived(self):
        # Reported rather than raised: one malformed document should not
        # abort a namespace-sized restore.
        planner = _planner(identity_fields={"PATIENT": ["patient_id"]})

        plan = await planner.plan(
            {
                "templates": [{"template_id": "T", "value": "PATIENT"}],
                "documents": [{
                    "document_id": "D", "template_id": "T",
                    "template_value": "PATIENT",
                    "identity_hash": "as-archived",
                    "data": {"something_else": 1},
                }],
            },
            NAMESPACE,
        )

        assert plan.rows["documents"][0]["identity_hash"] == "as-archived"
        assert plan.rehashed_documents == 0
