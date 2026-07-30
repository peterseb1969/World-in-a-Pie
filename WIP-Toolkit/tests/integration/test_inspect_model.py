"""The analysis model against a REAL server-engine backup.

The unit suite builds archives by hand, which proves the model reads the
format it is told to read. This proves it reads what the backup engine
actually writes — the seam where a wrong belief about a field name or a
reference shape would otherwise survive every green unit test and only
surface when an operator points `inspect` at a production archive.

Its own namespace, deliberately: the golden round-trip wipes the instance
mid-test, so sharing a namespace would make these two tests order-dependent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from wip_archive.archive import ArchiveReader
from wip_archive.model import ArchiveModel

NS = "toolkit-inspect-it"

pytestmark = pytest.mark.usefixtures("stack")


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


def _seed(client) -> dict:
    """A corpus with the shapes the analyses are about.

    Two entity templates joined by an edge type, a vocabulary with one term
    nobody uses, a document with two versions, and two relationship
    documents that touch one monster but both spells — so every count the
    assertions make below is a number a human can check by reading this.
    """
    ids: dict = {}
    client.put(
        "registry", f"/namespaces/{NS}",
        json={"description": "Toolkit inspect analysis", "isolation_mode": "open"},
    )

    terminology = _created(client.post(
        "def-store", "/terminologies",
        json=[{"value": "CREATURE_TYPE", "label": "Creature Type", "namespace": NS}],
    ))
    ids["terminology"] = terminology["id"]

    terms = client.post(
        "def-store", f"/terminologies/{ids['terminology']}/terms",
        json=[
            {"value": "dragon", "label": "Dragon", "aliases": ["drake", "wyrm"]},
            {"value": "beast", "label": "Beast"},
            {"value": "undead", "label": "Undead"},
        ],
        params={"namespace": NS},
    )
    assert terms["succeeded"] == 3, terms
    ids["term_dragon"] = terms["results"][0]["id"]
    ids["term_beast"] = terms["results"][1]["id"]

    relations = client.post(
        "def-store", "/ontology/term-relations",
        json=[{
            "source_term_id": ids["term_dragon"],
            "target_term_id": ids["term_beast"],
            "relation_type": "is_a",
        }],
        params={"namespace": NS},
    )
    assert relations["succeeded"] == 1, relations

    monster = _created(client.post("template-store", "/templates", json=[{
        "value": "MONSTER",
        "label": "Monster",
        "namespace": NS,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "creature_type", "label": "Creature Type", "type": "term",
             "terminology_ref": "CREATURE_TYPE", "mandatory": True},
        ],
    }]))
    ids["monster_template"] = monster["id"]

    spell = _created(client.post("template-store", "/templates", json=[{
        "value": "SPELL",
        "label": "Spell",
        "namespace": NS,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
        ],
    }]))
    ids["spell_template"] = spell["id"]

    edge = _created(client.post("template-store", "/templates", json=[{
        "value": "MONSTER_HAS_SPELL",
        "label": "Monster has spell",
        "namespace": NS,
        "usage": "relationship",
        "versioned": False,
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": ["MONSTER"],
        "target_templates": ["SPELL"],
        "fields": [
            {"name": "source_ref", "label": "Monster", "type": "reference",
             "reference_type": "document", "target_templates": ["MONSTER"],
             "mandatory": True},
            {"name": "target_ref", "label": "Spell", "type": "reference",
             "reference_type": "document", "target_templates": ["SPELL"],
             "mandatory": True},
        ],
    }]))
    ids["edge_template"] = edge["id"]

    docs = client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Smaug", "creature_type": "dragon"}},
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Lich King", "creature_type": "undead"}},
        {"template_id": ids["spell_template"], "namespace": NS,
         "data": {"name": "Fireball"}},
        {"template_id": ids["spell_template"], "namespace": NS,
         "data": {"name": "Frost Nova"}},
    ])
    assert docs["succeeded"] == 4, docs
    ids["doc_smaug"] = docs["results"][0]["document_id"]
    ids["doc_fireball"] = docs["results"][2]["document_id"]
    ids["doc_frostnova"] = docs["results"][3]["document_id"]

    # Same identity, changed payload → version 2.
    smaug_v2 = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Smaug", "creature_type": "beast"}},
    ]))
    assert smaug_v2["version"] == 2

    edges = client.post("document-store", "/documents", json=[
        {"template_id": ids["edge_template"], "namespace": NS,
         "data": {"source_ref": ids["doc_smaug"],
                  "target_ref": ids["doc_fireball"]}},
        {"template_id": ids["edge_template"], "namespace": NS,
         "data": {"source_ref": ids["doc_smaug"],
                  "target_ref": ids["doc_frostnova"]}},
    ])
    assert edges["succeeded"] == 2, edges
    return ids


def _engine_backup(stack, archive_path: Path) -> None:
    """Produce the archive with the server's own backup engine."""
    from document_store.models.backup_job import BackupJob
    from document_store.services.backup_engine import DirectBackupEngine

    async def _run() -> None:
        mongo = BackupJob.get_motor_collection().database.client
        engine = DirectBackupEngine(mongo, None, lambda _event: None)
        await engine.run_backup(NS, archive_path)

    stack.portal.call(_run)


# The seed runs once for the whole module. It cannot be a module-scoped
# fixture: the client fixture it needs is function-scoped, and re-seeding per
# test would upsert the same identities into new document versions, moving
# every count these tests assert. Caching the produced archive keeps one seed
# and leaves the assertions reading against a fixed artifact.
_ENGINE_ARCHIVE: Path | None = None


@pytest.fixture()
def model(stack, wip_client, tmp_path_factory):
    global _ENGINE_ARCHIVE
    if _ENGINE_ARCHIVE is None:
        archive = tmp_path_factory.mktemp("inspect") / "engine-backup.zip"
        _seed(wip_client)
        _engine_backup(stack, archive)
        _ENGINE_ARCHIVE = archive
    with ArchiveReader(str(_ENGINE_ARCHIVE)) as reader:
        return ArchiveModel.load(reader)


def test_index_matches_what_was_seeded(model):
    assert model.namespaces == [NS]
    assert len(model.templates) == 3
    assert len(model.terminologies) == 1
    assert len(model.terms) == 3
    # Six document entities, seven rows: Smaug carries two versions.
    assert len(model.documents) == 6
    assert model.summary()["document_versions"] == 7
    assert model.term_relation_count == 1


def test_declared_edges_survive_the_real_writer(model):
    """The engine writes resolved ids; the model must read them as edges."""
    monster = model.resolve_template("MONSTER")
    edge = model.resolve_template("MONSTER_HAS_SPELL")
    spell = model.resolve_template("SPELL")
    assert monster and edge and spell

    # The template-level endpoint lists — the pair the retired closure
    # walker never read — resolve to the two entity templates.
    latest = edge.latest_version
    assert latest.endpoint_source_templates == {monster.template_id}
    assert latest.endpoint_target_templates == {spell.template_id}

    # MONSTER's term field resolves to the seeded terminology.
    terminology = model.resolve_terminology("CREATURE_TYPE")
    assert model.declared_terminologies(monster.template_id) == {
        terminology.terminology_id
    }


def test_actual_edges_come_from_real_reference_snapshots(model):
    monster = model.resolve_template("MONSTER")
    spell = model.resolve_template("SPELL")
    edge = model.resolve_template("MONSTER_HAS_SPELL")

    assert model.history.template_edges[
        (edge.template_id, monster.template_id)
    ] == 2
    assert model.history.template_edges[
        (edge.template_id, spell.template_id)
    ] == 2
    assert model.dangling == []


def test_edge_type_connectivity_on_a_real_archive(model):
    reports = model.edge_type_reports()
    assert len(reports) == 1
    report = reports[0]
    assert report.label == "MONSTER_HAS_SPELL"
    assert report.versioned is False  # the flag survives a real round-trip
    assert report.edge_count == 2
    percents = {d["template"]: d["percent"] for d in report.disconnection}
    # Both edges hang off Smaug, so one of two monsters is connected; both
    # spells are.
    assert percents["MONSTER"] == 50.0
    assert percents["SPELL"] == 100.0


def test_islands_and_closures_on_a_real_archive(model):
    islands = model.islands()
    assert len(islands) == 1  # the edge type joins both entity templates

    edge = model.resolve_template("MONSTER_HAS_SPELL")
    monster = model.resolve_template("MONSTER")
    spell = model.resolve_template("SPELL")
    closure = model.schema_closure({edge.template_id})
    assert closure.template_ids == {monster.template_id, spell.template_id}
    assert closure.terminology_ids == {
        model.resolve_terminology("CREATURE_TYPE").terminology_id
    }
    # Extracting an endpoint does not drag the edge type along.
    assert model.schema_closure({spell.template_id}).template_ids == set()


def test_term_usage_counts_real_documents(model):
    usage = model.terminology_usage(
        model.resolve_terminology("CREATURE_TYPE").terminology_id
    )
    assert usage["terms"] == 3
    # dragon (Smaug v1), undead (Lich King), beast (Smaug v2) are all used
    # across history; nothing is unused once every version is counted.
    assert usage["used"] == 3
    assert usage["unused"] == 0


def test_a_real_engine_archive_is_clean(model):
    """The engine's own output must not trip the model's error findings."""
    coverage = model.identity_coverage()
    assert coverage["namespaces"][NS]["door_refuses"] is False
    assert coverage["missing_registry_rows"] == {}
    assert model.count_verification()[NS]["mismatches"] == {}
    errors = [f for f in model.findings() if f.severity == "error"]
    assert errors == [], errors
