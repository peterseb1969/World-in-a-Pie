"""Tests for the offline archive analysis model.

Every fixture is a hand-built archive: ``ArchiveWriter`` is importable, so
the model is testable without a running instance, which is the point of an
offline analysis core.
"""

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.model import SCHEMA_VERSION, ArchiveModel
from wip_archive.models import EntityCounts, Manifest, NamespaceEntry

NS = "demo"

TPL_MONSTER = "0190c000-0000-7000-0000-00000000m001"
TPL_SPELL = "0190c000-0000-7000-0000-00000000s001"
TPL_EDGE = "0190c000-0000-7000-0000-00000000e001"
TPL_RECIPE = "0190c000-0000-7000-0000-00000000r001"
LOV_SCHOOL = "0190a000-0000-7000-0000-0000000000v1"
LOV_CUISINE = "0190a000-0000-7000-0000-0000000000v2"


def _template(
    template_id,
    value,
    *,
    version=1,
    usage="entity",
    versioned=True,
    fields=None,
    identity_fields=("name",),
    source_templates=None,
    target_templates=None,
    extends=None,
):
    row = {
        "template_id": template_id,
        "version": version,
        "value": value,
        "label": value.title(),
        "usage": usage,
        "versioned": versioned,
        "status": "active",
        "identity_fields": list(identity_fields),
        "fields": fields or [],
    }
    if extends:
        row["extends"] = extends
    if source_templates is not None:
        row["source_templates"] = source_templates
    if target_templates is not None:
        row["target_templates"] = target_templates
    return row


def _document(
    document_id,
    template_id,
    *,
    version=1,
    template_version=1,
    references=None,
    term_references=None,
    file_references=None,
    identity_hash="hash",
):
    return {
        "document_id": document_id,
        "template_id": template_id,
        "template_version": template_version,
        "version": version,
        "identity_hash": identity_hash,
        "data": {},
        "references": references or [],
        "term_references": term_references or [],
        "file_references": file_references or [],
    }


def _doc_ref(field_path, document_id, *, namespace=NS, template_id=None):
    return {
        "field_path": field_path,
        "reference_type": "document",
        "lookup_value": document_id,
        "resolved": {
            "document_id": document_id,
            "namespace": namespace,
            "template_id": template_id,
        },
    }


def _registry(entry_id, entity_type):
    return {
        "entry_id": entry_id,
        "namespace": NS,
        "entity_type": entity_type,
        "primary_composite_key": {},
        "primary_composite_key_hash": "h",
        "synonyms": [],
        "status": "active",
    }


def build_archive(path, rows, *, namespace=NS, include_files=False, blobs=None):
    """Write one namespace of raw entity rows to a v3 archive."""
    writer = ArchiveWriter(path, default_namespace=namespace)
    counts = {}
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=namespace)
        counts[entity_type] = len(entities)
    for file_id, data in (blobs or {}).items():
        writer.add_blob(file_id, data)
    entity_counts = EntityCounts(**counts)
    writer.write(
        Manifest(
            namespace=namespace,
            include_files=include_files,
            counts=entity_counts,
            namespaces=[NamespaceEntry(prefix=namespace, counts=entity_counts)],
        )
    )
    return path


@pytest.fixture
def bestiary(tmp_path):
    """Two entity templates joined by an edge type, plus a lone island."""
    rows = {
        "terminologies": [
            {"terminology_id": LOV_SCHOOL, "value": "SCHOOL", "status": "active"},
            {"terminology_id": LOV_CUISINE, "value": "CUISINE", "status": "active"},
        ],
        "terms": [
            {
                "term_id": "term-fire",
                "terminology_id": LOV_SCHOOL,
                "value": "FIRE",
                "status": "active",
            },
            {
                "term_id": "term-ice",
                "terminology_id": LOV_SCHOOL,
                "value": "ICE",
                "status": "deprecated",
            },
            {
                "term_id": "term-unused",
                "terminology_id": LOV_SCHOOL,
                "value": "VOID",
                "status": "active",
            },
        ],
        "templates": [
            _template(TPL_MONSTER, "MONSTER"),
            _template(
                TPL_SPELL,
                "SPELL",
                fields=[
                    {
                        "name": "school",
                        "type": "term",
                        "terminology_ref": LOV_SCHOOL,
                    }
                ],
            ),
            _template(
                TPL_EDGE,
                "MONSTER_HAS_SPELL",
                usage="relationship",
                versioned=False,
                identity_fields=("source_ref", "target_ref"),
                source_templates=[TPL_MONSTER],
                target_templates=[TPL_SPELL],
                fields=[
                    {
                        "name": "source_ref",
                        "type": "reference",
                        "reference_type": "document",
                        "target_templates": [TPL_MONSTER],
                    },
                    {
                        "name": "target_ref",
                        "type": "reference",
                        "reference_type": "document",
                        "target_templates": [TPL_SPELL],
                    },
                ],
            ),
            _template(
                TPL_RECIPE,
                "RECIPE",
                fields=[
                    {
                        "name": "cuisine",
                        "type": "term",
                        "terminology_ref": LOV_CUISINE,
                    }
                ],
            ),
        ],
        "documents": [
            _document("mon-1", TPL_MONSTER),
            _document("mon-2", TPL_MONSTER),
            _document(
                "spell-1",
                TPL_SPELL,
                term_references=[{"term_id": "term-fire"}],
            ),
            _document(
                "spell-2",
                TPL_SPELL,
                term_references=[{"term_id": "term-ice"}],
            ),
            _document(
                "edge-1",
                TPL_EDGE,
                references=[
                    _doc_ref("source_ref", "mon-1", template_id=TPL_MONSTER),
                    _doc_ref("target_ref", "spell-1", template_id=TPL_SPELL),
                ],
            ),
            _document("rec-1", TPL_RECIPE),
        ],
        "registry_entries": [
            _registry(TPL_MONSTER, "templates"),
            _registry(TPL_SPELL, "templates"),
            _registry(TPL_EDGE, "templates"),
            _registry(TPL_RECIPE, "templates"),
            _registry(LOV_SCHOOL, "terminologies"),
            _registry(LOV_CUISINE, "terminologies"),
            _registry("term-fire", "terms"),
            _registry("term-ice", "terms"),
            _registry("term-unused", "terms"),
            _registry("mon-1", "documents"),
            _registry("mon-2", "documents"),
            _registry("spell-1", "documents"),
            _registry("spell-2", "documents"),
            _registry("edge-1", "documents"),
            _registry("rec-1", "documents"),
        ],
    }
    return build_archive(tmp_path / "bestiary.zip", rows)


def load(path, **kwargs):
    with ArchiveReader(path) as reader:
        return ArchiveModel.load(reader, **kwargs)


class TestEntityIndex:
    def test_indexes_definitions_and_documents(self, bestiary):
        model = load(bestiary)
        assert set(model.templates) == {
            TPL_MONSTER,
            TPL_SPELL,
            TPL_EDGE,
            TPL_RECIPE,
        }
        assert len(model.terminologies) == 2
        assert len(model.terms) == 3
        assert len(model.documents) == 6
        assert model.namespaces == [NS]

    def test_template_versions_are_separate_nodes(self, tmp_path):
        """Declared references belong to a version, not to the template."""
        rows = {
            "templates": [
                _template(TPL_SPELL, "SPELL", version=1, fields=[
                    {"name": "school", "type": "term", "terminology_ref": LOV_SCHOOL}
                ]),
                _template(TPL_SPELL, "SPELL", version=2, fields=[]),
            ],
            "terminologies": [
                {"terminology_id": LOV_SCHOOL, "value": "SCHOOL"},
            ],
            "registry_entries": [
                _registry(TPL_SPELL, "templates"),
                _registry(LOV_SCHOOL, "terminologies"),
            ],
        }
        model = load(build_archive(tmp_path / "versions.zip", rows))
        spell = model.templates[TPL_SPELL]
        assert set(spell.versions) == {1, 2}
        assert spell.versions[1].declared_terminologies == {LOV_SCHOOL}
        assert spell.versions[2].declared_terminologies == set()
        # The roll-up keeps v1's dependency: an archive holding documents
        # pinned to v1 still needs that terminology.
        assert model.declared_terminologies(TPL_SPELL) == {LOV_SCHOOL}

    def test_document_index_keeps_only_the_pin_and_max_version(self, tmp_path):
        rows = {
            "templates": [_template(TPL_MONSTER, "MONSTER")],
            "documents": [
                _document("mon-1", TPL_MONSTER, version=1),
                _document("mon-1", TPL_MONSTER, version=2),
                _document("mon-1", TPL_MONSTER, version=3),
            ],
            "registry_entries": [
                _registry(TPL_MONSTER, "templates"),
                _registry("mon-1", "documents"),
            ],
        }
        model = load(build_archive(tmp_path / "hist.zip", rows))
        entry = model.documents["mon-1"]
        assert entry.max_version == 3
        assert entry.version_count == 3
        assert model.templates[TPL_MONSTER].document_version_count == 3
        assert len(model.templates[TPL_MONSTER].document_ids) == 1


class TestEdges:
    def test_relationship_endpoint_lists_are_read(self, bestiary):
        """The template-level lists the retired closure walker never read."""
        model = load(bestiary)
        edge = model.templates[TPL_EDGE].latest_version
        assert edge.endpoint_source_templates == {TPL_MONSTER}
        assert edge.endpoint_target_templates == {TPL_SPELL}
        # And they count as declared dependencies of the edge type.
        assert model.declared_out_neighbours(TPL_EDGE) == {TPL_MONSTER, TPL_SPELL}

    def test_actual_document_edges(self, bestiary):
        model = load(bestiary)
        assert model.history.reference_edges[(TPL_EDGE, TPL_MONSTER)] == 1
        assert model.history.reference_edges[(TPL_EDGE, TPL_SPELL)] == 1

    def test_declared_references_resolve_from_value_form(self, tmp_path):
        """Templates store references as submitted — often values, not ids.

        The template-level endpoint lists on a relationship template are
        stored verbatim, so a real archive routinely carries "MONSTER" where
        a naive reader expects a UUID. Both forms name the same edge.
        """
        rows = {
            "terminologies": [{"terminology_id": LOV_SCHOOL, "value": "SCHOOL"}],
            "templates": [
                _template(TPL_MONSTER, "MONSTER"),
                _template(TPL_SPELL, "SPELL", fields=[
                    # value form, the shape a hand-written seed produces
                    {"name": "s", "type": "term", "terminology_ref": "SCHOOL"}
                ]),
                _template(
                    TPL_EDGE,
                    "MONSTER_HAS_SPELL",
                    usage="relationship",
                    source_templates=["MONSTER"],
                    target_templates=["SPELL"],
                ),
            ],
            "registry_entries": [_registry(TPL_EDGE, "templates")],
        }
        model = load(build_archive(tmp_path / "byvalue.zip", rows))
        edge = model.templates[TPL_EDGE].latest_version
        assert edge.endpoint_source_templates == {TPL_MONSTER}
        assert edge.endpoint_target_templates == {TPL_SPELL}
        assert model.declared_terminologies(TPL_SPELL) == {LOV_SCHOOL}
        # Nothing here points outside the archive.
        assert model.external_refs == []
        assert len(model.islands()) == 1

    def test_two_half_endpoint_entries_are_read(self, tmp_path):
        """Format 3.1+ archives declare endpoints as {lookup_value, resolved}.

        The model keeps one handle per entry — the resolved id when present,
        else the lookup — so entries, lookup-only entries (a restore-door
        null), and legacy strings all land in the same resolution machinery.
        """
        rows = {
            "templates": [
                _template(TPL_MONSTER, "MONSTER"),
                _template(TPL_SPELL, "SPELL"),
                _template(
                    TPL_EDGE,
                    "MONSTER_HAS_SPELL",
                    usage="relationship",
                    source_templates=[
                        {"lookup_value": "MONSTER", "resolved": TPL_MONSTER}
                    ],
                    target_templates=[
                        {"lookup_value": "SPELL", "resolved": None}
                    ],
                ),
            ],
            "registry_entries": [_registry(TPL_EDGE, "templates")],
        }
        model = load(build_archive(tmp_path / "entries.zip", rows))
        edge = model.templates[TPL_EDGE].latest_version
        assert edge.endpoint_source_templates == {TPL_MONSTER}
        # resolved: null falls back to the lookup half, which the model's
        # declared-reference resolution turns into the id it names.
        assert edge.endpoint_target_templates == {TPL_SPELL}
        assert model.declared_out_neighbours(TPL_EDGE) == {TPL_MONSTER, TPL_SPELL}

    def test_qualified_reference_crosses_a_namespace_bare_does_not(self, tmp_path):
        """`ns:VALUE` names another namespace; a bare value never does."""
        writer = ArchiveWriter(tmp_path / "cross.zip", default_namespace="a")
        writer.add_entity(
            "terminologies",
            {"terminology_id": LOV_SCHOOL, "value": "SCHOOL"},
            namespace="b",
        )
        writer.add_entity(
            "templates",
            _template(
                TPL_SPELL,
                "SPELL",
                fields=[
                    {"name": "ok", "type": "term", "terminology_ref": "b:SCHOOL"},
                    {"name": "bare", "type": "term", "terminology_ref": "SCHOOL"},
                ],
            ),
            namespace="a",
        )
        writer.write(
            Manifest(
                namespaces=[
                    NamespaceEntry(prefix="a", counts=EntityCounts(templates=1)),
                    NamespaceEntry(prefix="b", counts=EntityCounts(terminologies=1)),
                ]
            )
        )
        model = load(tmp_path / "cross.zip")
        # The qualified form resolved; the bare one looked in namespace "a",
        # found nothing, and is reported as external rather than silently
        # resolved to the same terminology.
        assert model.declared_terminologies(TPL_SPELL) == {LOV_SCHOOL}
        assert [e.target_id for e in model.external_refs] == ["SCHOOL"]

    def test_external_reference_is_labeled_not_followed(self, tmp_path):
        rows = {
            "templates": [
                _template(
                    TPL_SPELL,
                    "SPELL",
                    fields=[
                        {
                            "name": "school",
                            "type": "term",
                            "terminology_ref": "not-in-this-archive",
                        }
                    ],
                )
            ],
            "registry_entries": [_registry(TPL_SPELL, "templates")],
        }
        model = load(build_archive(tmp_path / "ext.zip", rows))
        kinds = {(e.kind, e.target_id) for e in model.external_refs}
        assert ("terminology", "not-in-this-archive") in kinds
        assert model.dangling == []

    def test_dangling_reference_is_distinguished_from_external(self, tmp_path):
        """A ref into a carried namespace whose target is absent is broken."""
        rows = {
            "templates": [_template(TPL_MONSTER, "MONSTER")],
            "documents": [
                _document(
                    "mon-1",
                    TPL_MONSTER,
                    references=[_doc_ref("friend", "ghost-doc", namespace=NS)],
                ),
                _document(
                    "mon-2",
                    TPL_MONSTER,
                    references=[
                        _doc_ref("friend", "far-doc", namespace="elsewhere")
                    ],
                ),
            ],
            "registry_entries": [
                _registry(TPL_MONSTER, "templates"),
                _registry("mon-1", "documents"),
                _registry("mon-2", "documents"),
            ],
        }
        model = load(build_archive(tmp_path / "dangling.zip", rows))
        assert [e.target_id for e in model.dangling] == ["ghost-doc"]
        assert any(
            e.target_id == "far-doc"
            and e.reason == "other-namespace-not-in-archive"
            for e in model.external_refs
        )


class TestClosures:
    def test_schema_closure_follows_declared_references(self, bestiary):
        model = load(bestiary)
        closure = model.schema_closure({TPL_EDGE})
        assert closure.template_ids == {TPL_MONSTER, TPL_SPELL}
        assert closure.terminology_ids == {LOV_SCHOOL}

    def test_extracting_an_endpoint_does_not_require_the_edge_type(self, bestiary):
        """Extraction closure is asymmetric — that asymmetry is the point."""
        model = load(bestiary)
        assert model.schema_closure({TPL_MONSTER}).template_ids == set()
        # ...but the edge type depends on it, so deleting MONSTER is not free.
        report = model.template_report(TPL_MONSTER)
        assert "MONSTER_HAS_SPELL" in report.incoming_declared
        assert report.in_degree_declared == 1

    def test_latest_only_closure_can_differ_from_with_history(self, tmp_path):
        """An old version's reference disappears when history is dropped."""
        rows = {
            "templates": [
                _template(TPL_MONSTER, "MONSTER"),
                _template(TPL_SPELL, "SPELL"),
            ],
            "documents": [
                _document(
                    "mon-1",
                    TPL_MONSTER,
                    version=1,
                    references=[_doc_ref("favourite", "spell-1")],
                ),
                _document("mon-1", TPL_MONSTER, version=2, references=[]),
                _document("spell-1", TPL_SPELL),
            ],
            "registry_entries": [
                _registry(TPL_MONSTER, "templates"),
                _registry(TPL_SPELL, "templates"),
                _registry("mon-1", "documents"),
                _registry("spell-1", "documents"),
            ],
        }
        model = load(build_archive(tmp_path / "dual.zip", rows))
        assert model.data_closure({TPL_MONSTER}).template_ids == {TPL_SPELL}
        assert model.data_closure(
            {TPL_MONSTER}, latest_only=True
        ).template_ids == set()


class TestIslands:
    def test_connected_templates_form_one_island(self, bestiary):
        model = load(bestiary)
        islands = model.islands()
        assert len(islands) == 2
        big = islands[0]
        assert big.template_ids == {TPL_MONSTER, TPL_SPELL, TPL_EDGE}
        assert LOV_SCHOOL in big.terminology_ids
        lone = islands[1]
        assert lone.template_ids == {TPL_RECIPE}

    def test_shared_terminology_does_not_weld_islands_together(self, tmp_path):
        """A vocabulary used by two unrelated templates is duplicated, flagged."""
        rows = {
            "terminologies": [{"terminology_id": LOV_SCHOOL, "value": "SCHOOL"}],
            "templates": [
                _template(
                    TPL_MONSTER,
                    "MONSTER",
                    fields=[
                        {"name": "s", "type": "term", "terminology_ref": LOV_SCHOOL}
                    ],
                ),
                _template(
                    TPL_RECIPE,
                    "RECIPE",
                    fields=[
                        {"name": "s", "type": "term", "terminology_ref": LOV_SCHOOL}
                    ],
                ),
            ],
            "registry_entries": [
                _registry(TPL_MONSTER, "templates"),
                _registry(TPL_RECIPE, "templates"),
                _registry(LOV_SCHOOL, "terminologies"),
            ],
        }
        model = load(build_archive(tmp_path / "shared.zip", rows))
        assert len(model.islands()) == 2
        assert model.shared_terminologies() == {LOV_SCHOOL: [0, 1]}


class TestEdgeTypeConnectivity:
    def test_quantifies_what_dropping_the_edge_type_disconnects(self, bestiary):
        model = load(bestiary)
        reports = model.edge_type_reports()
        assert len(reports) == 1
        report = reports[0]
        assert report.label == "MONSTER_HAS_SPELL"
        assert report.versioned is False
        assert report.relationship_documents == 1
        assert report.declared_source_templates == ["MONSTER"]
        assert report.declared_target_templates == ["SPELL"]
        by_template = {d["template"]: d for d in report.disconnection}
        # One of two monsters and one of two spells participate.
        assert by_template["MONSTER"]["connected_documents"] == 1
        assert by_template["MONSTER"]["total_documents"] == 2
        assert by_template["MONSTER"]["percent"] == 50.0
        assert by_template["SPELL"]["percent"] == 50.0

    def test_entity_templates_are_not_edge_types(self, bestiary):
        model = load(bestiary)
        assert model.templates[TPL_MONSTER].is_edge_type is False
        assert model.templates[TPL_EDGE].is_edge_type is True


class TestHealthStats:
    def test_term_usage_splits_used_from_unused(self, bestiary):
        model = load(bestiary)
        usage = model.terminology_usage(LOV_SCHOOL)
        assert usage["terms"] == 3
        assert usage["used"] == 2
        assert usage["unused"] == 1
        assert usage["deprecated_but_referenced"] == ["ICE"]

    def test_version_spread_is_reported_per_template_version(self, tmp_path):
        rows = {
            "templates": [
                _template(TPL_MONSTER, "MONSTER", version=1),
                _template(TPL_MONSTER, "MONSTER", version=2),
            ],
            "documents": [
                _document("mon-1", TPL_MONSTER, template_version=1),
                _document("mon-2", TPL_MONSTER, template_version=2),
                _document("mon-3", TPL_MONSTER, template_version=2),
            ],
            "registry_entries": [
                _registry(TPL_MONSTER, "templates"),
                _registry("mon-1", "documents"),
                _registry("mon-2", "documents"),
                _registry("mon-3", "documents"),
            ],
        }
        model = load(build_archive(tmp_path / "spread.zip", rows))
        report = model.template_report(TPL_MONSTER)
        assert report.docs_per_template_version == {1: 1, 2: 2}

    def test_orphan_blob_and_missing_blob(self, tmp_path):
        rows = {
            "templates": [_template(TPL_MONSTER, "MONSTER")],
            "files": [
                {"file_id": "file-1", "filename": "a.png", "size_bytes": 10},
                {"file_id": "file-2", "filename": "b.png", "size_bytes": 20},
            ],
            "documents": [
                _document(
                    "mon-1", TPL_MONSTER, file_references=[{"file_id": "file-1"}]
                )
            ],
            "registry_entries": [
                _registry(TPL_MONSTER, "templates"),
                _registry("mon-1", "documents"),
                _registry("file-1", "files"),
                _registry("file-2", "files"),
            ],
        }
        path = build_archive(
            tmp_path / "blobs.zip",
            rows,
            include_files=True,
            blobs={"file-1": b"x"},
        )
        model = load(path)
        blobs = model.blob_report()
        assert blobs["orphan_metadata"] == ["file-2"]
        assert blobs["metadata_without_blob"] == ["file-2"]
        assert blobs["referenced"] == 1
        assert blobs["bytes"] == 30


class TestIdentityAndFindings:
    def test_door_refusal_condition_is_visible_before_upload(self, tmp_path):
        """Entity rows with zero registry rows — exactly what restore refuses."""
        rows = {
            "templates": [_template(TPL_MONSTER, "MONSTER")],
            "documents": [_document("mon-1", TPL_MONSTER)],
        }
        model = load(build_archive(tmp_path / "noidentity.zip", rows))
        coverage = model.identity_coverage()
        assert coverage["namespaces"][NS]["door_refuses"] is True
        classes = {f.finding_class for f in model.findings()}
        assert "missing_identity_rows" in classes
        assert model.findings()[0].severity == "error"

    def test_healthy_archive_has_no_error_findings(self, bestiary):
        model = load(bestiary)
        severities = {f.severity for f in model.findings()}
        assert "error" not in severities

    def test_findings_are_ordered_most_severe_first(self, tmp_path):
        rows = {
            "templates": [_template(TPL_MONSTER, "MONSTER")],
            "documents": [
                _document(
                    "mon-1",
                    TPL_MONSTER,
                    references=[_doc_ref("friend", "ghost", namespace=NS)],
                )
            ],
            "files": [{"file_id": "file-2", "filename": "b.png", "size_bytes": 1}],
            "registry_entries": [_registry(TPL_MONSTER, "templates")],
        }
        model = load(build_archive(tmp_path / "mixed.zip", rows))
        ranks = [f.rank for f in model.findings()]
        assert ranks == sorted(ranks)
        assert model.findings()[0].finding_class == "dangling_document_reference"


class TestCountVerification:
    def test_matching_counts_produce_no_mismatch(self, bestiary):
        model = load(bestiary)
        verification = model.count_verification()
        assert verification[NS]["declared"] is True
        assert verification[NS]["mismatches"] == {}

    def test_manifest_that_overstates_its_contents_is_caught(self, tmp_path):
        writer = ArchiveWriter(tmp_path / "lie.zip", default_namespace=NS)
        writer.add_entity("templates", _template(TPL_MONSTER, "MONSTER"))
        lying = EntityCounts(templates=7)
        writer.write(
            Manifest(
                namespace=NS,
                counts=lying,
                namespaces=[NamespaceEntry(prefix=NS, counts=lying)],
            )
        )
        model = load(tmp_path / "lie.zip")
        assert model.count_verification()[NS]["mismatches"]["templates"] == {
            "manifest": 7,
            "actual": 1,
        }
        assert "manifest_count_mismatch" in {
            f.finding_class for f in model.findings()
        }

    def test_manifest_without_namespace_entries_claims_nothing(self, tmp_path):
        """A legacy-shaped manifest is not evidence of a mismatch."""
        writer = ArchiveWriter(tmp_path / "legacy.zip", default_namespace=NS)
        writer.add_entity("templates", _template(TPL_MONSTER, "MONSTER"))
        writer.write(Manifest(namespace=NS, counts=EntityCounts(templates=99)))
        model = load(tmp_path / "legacy.zip")
        assert model.count_verification()[NS]["declared"] is False
        assert model.count_verification()[NS]["mismatches"] == {}
        assert "manifest_count_mismatch" not in {
            f.finding_class for f in model.findings()
        }


class TestTerminologyDive:
    def test_terms_carry_usage_aliases_and_relation_degree(self, tmp_path):
        rows = {
            "terminologies": [{"terminology_id": LOV_SCHOOL, "value": "SCHOOL"}],
            "terms": [
                {
                    "term_id": "term-fire",
                    "terminology_id": LOV_SCHOOL,
                    "value": "FIRE",
                    "status": "active",
                    "aliases": ["FLAME"],
                },
                {
                    "term_id": "term-heat",
                    "terminology_id": LOV_SCHOOL,
                    "value": "HEAT",
                    "status": "active",
                },
            ],
            "term_relations": [
                {
                    "source_term_id": "term-heat",
                    "target_term_id": "term-fire",
                    "relation_type": "is_a",
                }
            ],
            "templates": [_template(TPL_SPELL, "SPELL")],
            "documents": [
                _document(
                    "spell-1", TPL_SPELL, term_references=[{"term_id": "term-fire"}]
                )
            ],
            "registry_entries": [_registry(TPL_SPELL, "templates")],
        }
        model = load(build_archive(tmp_path / "dive.zip", rows))
        rows_out = {r["term"]: r for r in model.terminology_terms(LOV_SCHOOL)}
        assert rows_out["FIRE"]["references"] == 1
        assert rows_out["FIRE"]["aliases"] == ["FLAME"]
        assert rows_out["FIRE"]["relations"] == 1
        assert rows_out["HEAT"]["references"] == 0
        assert rows_out["HEAT"]["relations"] == 1
        # Most-referenced first — the dive leads with what is actually in use.
        assert model.terminology_terms(LOV_SCHOOL)[0]["term"] == "FIRE"


class TestJsonContract:
    def test_json_output_carries_a_schema_version(self, bestiary):
        model = load(bestiary)
        payload = model.to_dict()
        assert payload["schema"] == SCHEMA_VERSION
        assert set(payload) == {
            "schema",
            "archive",
            "islands",
            "shared_terminologies",
            "templates",
            "terminologies",
            "edge_types",
            "files",
            "identity",
            "counts",
            "findings",
            "deep_dive",
        }

    def test_deep_dive_is_the_filter_plan(self, bestiary):
        """`inspect A TEMPLATE` prints what `filter A TEMPLATE` would produce."""
        model = load(bestiary)
        payload = model.to_dict(templates=[TPL_EDGE])
        deep = payload["deep_dive"]["templates"]
        assert len(deep) == 1
        assert deep[0]["template"] == "MONSTER_HAS_SPELL"
        assert sorted(deep[0]["schema_closure"]["templates"]) == ["MONSTER", "SPELL"]

    def test_resolve_template_accepts_value_or_id(self, bestiary):
        model = load(bestiary)
        assert model.resolve_template("MONSTER").template_id == TPL_MONSTER
        assert model.resolve_template(TPL_MONSTER).value == "MONSTER"
        assert model.resolve_template("NOPE") is None


class TestNamespaceScoping:
    def test_namespaces_can_be_restricted(self, tmp_path):
        writer = ArchiveWriter(tmp_path / "multi.zip", default_namespace="a")
        writer.add_entity("templates", _template(TPL_MONSTER, "MONSTER"), namespace="a")
        writer.add_entity("templates", _template(TPL_RECIPE, "RECIPE"), namespace="b")
        writer.write(
            Manifest(
                namespaces=[
                    NamespaceEntry(prefix="a", counts=EntityCounts(templates=1)),
                    NamespaceEntry(prefix="b", counts=EntityCounts(templates=1)),
                ]
            )
        )
        both = load(tmp_path / "multi.zip")
        assert len(both.templates) == 2
        only_a = load(tmp_path / "multi.zip", namespaces=["a"])
        assert set(only_a.templates) == {TPL_MONSTER}
