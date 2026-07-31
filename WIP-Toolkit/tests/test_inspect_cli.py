"""CLI tests for the grown `inspect` verb, against real archives.

No mocked reader: the verb's whole job is to render what the model computed
from a real file, and a mocked reader would verify neither half.
"""

import json

import pytest
from click.testing import CliRunner
from wip_archive.archive import ArchiveWriter
from wip_archive.models import EntityCounts, Manifest, NamespaceEntry
from wip_toolkit.cli import main

NS = "demo"
TPL_MONSTER = "tpl-monster"
TPL_SPELL = "tpl-spell"
TPL_EDGE = "tpl-edge"
LOV_SCHOOL = "lov-school"


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


@pytest.fixture
def archive(tmp_path):
    rows = {
        "terminologies": [
            {"terminology_id": LOV_SCHOOL, "value": "SCHOOL", "status": "active"}
        ],
        "terms": [
            {
                "term_id": "term-fire",
                "terminology_id": LOV_SCHOOL,
                "value": "FIRE",
                "status": "active",
                "aliases": ["Fire", "FLAME"],
            },
            {
                "term_id": "term-void",
                "terminology_id": LOV_SCHOOL,
                "value": "VOID",
                "status": "active",
            },
        ],
        "templates": [
            {
                "template_id": TPL_MONSTER,
                "version": 1,
                "value": "MONSTER",
                "usage": "entity",
                "versioned": True,
                "status": "active",
                "identity_fields": ["name"],
                "fields": [],
            },
            {
                "template_id": TPL_SPELL,
                "version": 1,
                "value": "SPELL",
                "usage": "entity",
                "versioned": True,
                "status": "active",
                "identity_fields": ["name"],
                "fields": [
                    {
                        "name": "school",
                        "type": "term",
                        "terminology_ref": LOV_SCHOOL,
                    }
                ],
            },
            {
                "template_id": TPL_EDGE,
                "version": 1,
                "value": "MONSTER_HAS_SPELL",
                "usage": "relationship",
                "versioned": False,
                "status": "active",
                "identity_fields": ["source_ref", "target_ref"],
                "source_templates": [TPL_MONSTER],
                "target_templates": [TPL_SPELL],
                "fields": [],
            },
        ],
        "documents": [
            {
                "document_id": "mon-1",
                "template_id": TPL_MONSTER,
                "template_version": 1,
                "version": 1,
                "identity_hash": "h1",
                "data": {},
            },
            {
                "document_id": "spell-1",
                "template_id": TPL_SPELL,
                "template_version": 1,
                "version": 1,
                "identity_hash": "h2",
                "data": {},
                "term_references": [{"term_id": "term-fire"}],
            },
            {
                "document_id": "edge-1",
                "template_id": TPL_EDGE,
                "template_version": 1,
                "version": 1,
                "identity_hash": "h3",
                "data": {},
                "references": [
                    {
                        "field_path": "source_ref",
                        "reference_type": "document",
                        "resolved": {"document_id": "mon-1", "namespace": NS},
                    },
                    {
                        "field_path": "target_ref",
                        "reference_type": "document",
                        "resolved": {"document_id": "spell-1", "namespace": NS},
                    },
                ],
            },
        ],
        "registry_entries": [
            _registry(TPL_MONSTER, "templates"),
            _registry(TPL_SPELL, "templates"),
            _registry(TPL_EDGE, "templates"),
            _registry(LOV_SCHOOL, "terminologies"),
            _registry("term-fire", "terms"),
            _registry("term-void", "terms"),
            _registry("mon-1", "documents"),
            _registry("spell-1", "documents"),
            _registry("edge-1", "documents"),
        ],
    }
    path = tmp_path / "demo.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    counts = {}
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
        counts[entity_type] = len(entities)
    entity_counts = EntityCounts(**counts)
    writer.write(
        Manifest(
            namespace=NS,
            counts=entity_counts,
            namespaces=[NamespaceEntry(prefix=NS, counts=entity_counts)],
        )
    )
    return path


def run(*args):
    return CliRunner().invoke(main, ["inspect", *args])


def flat(output: str) -> str:
    """Reduce rendered tables to their prose, so a phrase can be asserted on.

    Rich breaks a cell's text across lines to fit the terminal and puts a
    column border between the fragments, so asserting on any multi-word
    phrase would really be asserting about the terminal width. Dropping the
    box-drawing characters and collapsing whitespace tests the message.
    Long single words are still truncated with an ellipsis by rich — assert
    those against the JSON output, which is the contract for exact strings.
    """
    without_borders = "".join(
        " " if "─" <= ch <= "╿" else ch for ch in output
    )
    return " ".join(without_borders.split())


class TestSummary:
    def test_summary_reports_structure_and_health(self, archive):
        result = run(str(archive))
        assert result.exit_code == 0, result.output
        assert "Archive Summary" in result.output
        assert "Islands" in result.output
        assert "MONSTER_HAS_SPELL" in result.output
        assert "SCHOOL" in result.output
        # Health: one of two terms is unused, and the manifest agrees with
        # what the archive actually carries.
        assert "OK" in result.output

    def test_clean_archive_says_no_findings(self, archive):
        result = run(str(archive))
        assert "No findings." in flat(result.output)

    def test_missing_identity_rows_are_reported_as_an_error(self, tmp_path):
        path = tmp_path / "noidentity.zip"
        writer = ArchiveWriter(path, default_namespace=NS)
        writer.add_entity(
            "templates",
            {"template_id": TPL_MONSTER, "value": "MONSTER", "version": 1, "fields": []},
            namespace=NS,
        )
        counts = EntityCounts(templates=1)
        writer.write(
            Manifest(
                namespace=NS,
                counts=counts,
                namespaces=[NamespaceEntry(prefix=NS, counts=counts)],
            )
        )
        # The exact finding class is a contract of the JSON output; the table
        # renders it for humans and may abbreviate it to fit the terminal.
        payload = json.loads(run(str(path), "--json").output)
        classes = {f["class"] for f in payload["findings"]}
        assert "missing_identity_rows" in classes
        assert payload["identity"]["namespaces"][NS]["door_refuses"] is True

        result = run(str(path))
        assert result.exit_code == 0
        assert "the restore door refuses this archive" in flat(result.output)

    def test_manifest_count_mismatch_is_reported(self, tmp_path):
        """The archive carries two templates; the manifest claims five."""
        path = tmp_path / "mismatch.zip"
        writer = ArchiveWriter(path, default_namespace=NS)
        for template_id in (TPL_MONSTER, TPL_SPELL):
            writer.add_entity(
                "templates",
                {"template_id": template_id, "value": template_id, "version": 1,
                 "fields": []},
                namespace=NS,
            )
        lying = EntityCounts(templates=5)
        writer.write(
            Manifest(
                namespace=NS,
                counts=lying,
                namespaces=[NamespaceEntry(prefix=NS, counts=lying)],
            )
        )
        payload = json.loads(run(str(path), "--json").output)
        classes = {f["class"] for f in payload["findings"]}
        assert "manifest_count_mismatch" in classes
        assert payload["counts"][NS]["mismatches"]["templates"] == {
            "manifest": 5,
            "actual": 2,
        }

        result = run(str(path))
        assert result.exit_code == 0
        assert "Manifest counts disagree" in flat(result.output)


class TestDeepDive:
    def test_template_dive_prints_the_filter_plan(self, archive):
        result = run(str(archive), "MONSTER_HAS_SPELL")
        assert result.exit_code == 0, result.output
        assert "Extraction question" in flat(result.output)
        assert "schema closure" in flat(result.output)
        assert "MONSTER" in result.output and "SPELL" in result.output

    def test_dive_accepts_an_id_as_well_as_a_value(self, archive):
        by_value = run(str(archive), "MONSTER")
        by_id = run(str(archive), TPL_MONSTER)
        assert by_value.exit_code == 0
        assert by_id.exit_code == 0
        assert "Deletion question" in flat(by_id.output)

    def test_unknown_subject_fails_loudly(self, archive):
        result = run(str(archive), "NO_SUCH_TEMPLATE")
        assert result.exit_code == 2
        assert "No such template" in result.output

    def test_terminology_dive_lists_terms_with_usage(self, archive):
        result = run(str(archive), "--terminology", "SCHOOL")
        assert result.exit_code == 0, result.output
        assert "FIRE" in result.output
        assert "VOID" in result.output
        assert "FLAME" in result.output  # aliases

    def test_unknown_terminology_fails_loudly(self, archive):
        result = run(str(archive), "--terminology", "NOPE")
        assert result.exit_code == 2
        assert "No such terminology" in result.output


class TestJson:
    def test_json_is_parseable_and_versioned(self, archive):
        result = run(str(archive), "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["schema"] == "1.0"
        assert payload["archive"]["templates"] == 3
        assert payload["islands"][0]["templates"] == [
            "MONSTER",
            "MONSTER_HAS_SPELL",
            "SPELL",
        ]

    def test_json_deep_dive_is_requested_by_subject(self, archive):
        result = run(str(archive), "SPELL", "--json")
        payload = json.loads(result.output)
        deep = payload["deep_dive"]["templates"]
        assert len(deep) == 1
        assert deep[0]["template"] == "SPELL"
        assert deep[0]["schema_closure"]["terminologies"] == ["SCHOOL"]

    def test_edge_type_connectivity_is_quantified(self, archive):
        payload = json.loads(run(str(archive), "--json").output)
        edge = payload["edge_types"][0]
        assert edge["edge_type"] == "MONSTER_HAS_SPELL"
        assert edge["versioned"] is False
        assert edge["relationship_documents"] == 1
        percents = {d["template"]: d["percent"] for d in edge["disconnection"]}
        assert percents == {"MONSTER": 100.0, "SPELL": 100.0}


class TestShallowProjections:
    def test_show_ids_still_lists_raw_rows(self, archive):
        result = run(str(archive), "--show-ids")
        assert result.exit_code == 0
        assert "Templates IDs" in flat(result.output)

    def test_show_references_still_prints_the_raw_graph(self, archive):
        result = run(str(archive), "--show-references")
        assert result.exit_code == 0
        assert "Template Dependency Graph" in flat(result.output)


class TestNamespaceOption:
    def test_analysis_can_be_restricted_to_one_namespace(self, tmp_path):
        path = tmp_path / "multi.zip"
        writer = ArchiveWriter(path, default_namespace="a")
        writer.add_entity(
            "templates",
            {"template_id": "t-a", "value": "ALPHA", "version": 1, "fields": []},
            namespace="a",
        )
        writer.add_entity(
            "templates",
            {"template_id": "t-b", "value": "BETA", "version": 1, "fields": []},
            namespace="b",
        )
        writer.write(
            Manifest(
                namespaces=[
                    NamespaceEntry(prefix="a", counts=EntityCounts(templates=1)),
                    NamespaceEntry(prefix="b", counts=EntityCounts(templates=1)),
                ]
            )
        )
        both = json.loads(run(str(path), "--json").output)
        assert both["archive"]["templates"] == 2
        only_a = json.loads(run(str(path), "--namespace", "a", "--json").output)
        assert only_a["archive"]["templates"] == 1
