"""The golden round-trip: seed → export → verify archive → wipe → restore → verify.

One test, six phases, against the real in-process services (see conftest).
Every count assertion is a seam guard: an exporter that silently drops an
entity class shows up here as a `N != 0` diff (CASE-666 shipped precisely
because no automated check compared archive contents to what was seeded).
The restore phase runs the never-exercised-until-live paths end-to-end:
pre-assigned-ID creates (CASE-660), value resolution at activation
(CASE-665), template-class flag fidelity (CASE-659), and relation restore.

The seed deliberately covers every entity class the archive format carries
except binary files (MinIO isn't mounted; files are exercised by the
document-store's own backup-engine tests): a terminology, terms with
aliases, an ontology relation, two entity templates (identity fields,
header fields, full-text flag), an edge type (usage=relationship,
versioned=false), multi-version documents, and relationship documents.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from wip_archive.archive import ArchiveReader
from wip_toolkit.client import WIPClientError
from wip_toolkit.export.exporter import run_export

NS = "toolkit-it"

pytestmark = pytest.mark.usefixtures("stack")


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


def _seed(client) -> dict:
    """Seed the namespace; returns the IDs later phases assert against."""
    ids: dict = {}

    client.put(
        "registry", f"/namespaces/{NS}",
        json={"description": "Toolkit integration round-trip", "isolation_mode": "open"},
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
        "header_fields": ["name", "power"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "creature_type", "label": "Creature Type", "type": "term",
             "terminology_ref": "CREATURE_TYPE", "mandatory": True},
            {"name": "power", "label": "Power", "type": "integer"},
            {"name": "lore", "label": "Lore", "type": "string",
             "full_text_indexed": True},
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
            {"name": "level", "label": "Level", "type": "integer"},
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
            {"name": "proficiency", "label": "Proficiency", "type": "string"},
        ],
    }]))
    ids["edge_template"] = edge["id"]

    docs = client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Smaug", "creature_type": "dragon", "power": 95,
                  "lore": "The last great fire drake"},
         # The create API wraps the ENTIRE request metadata under stored
         # metadata.custom (document_service.py: custom=request.metadata)
         "metadata": {"source_system": "round-trip-seed"}},
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Lich King", "creature_type": "undead", "power": 88}},
        {"template_id": ids["spell_template"], "namespace": NS,
         "data": {"name": "Fireball", "level": 3}},
        {"template_id": ids["spell_template"], "namespace": NS,
         "data": {"name": "Frost Nova", "level": 4}},
    ])
    assert docs["succeeded"] == 4, docs
    ids["doc_smaug"] = docs["results"][0]["document_id"]
    ids["doc_fireball"] = docs["results"][2]["document_id"]
    ids["doc_frostnova"] = docs["results"][3]["document_id"]

    # Same identity, changed payload → version 2 (identity-based upsert)
    smaug_v2 = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Smaug", "creature_type": "dragon", "power": 97,
                  "lore": "The last great fire drake"},
         "metadata": {"source_system": "round-trip-seed"}},
    ]))
    assert smaug_v2["document_id"] == ids["doc_smaug"]
    assert smaug_v2["version"] == 2

    edges = client.post("document-store", "/documents", json=[
        {"template_id": ids["edge_template"], "namespace": NS,
         "data": {"source_ref": ids["doc_smaug"],
                  "target_ref": ids["doc_fireball"], "proficiency": "master"}},
        {"template_id": ids["edge_template"], "namespace": NS,
         "data": {"source_ref": ids["doc_smaug"],
                  "target_ref": ids["doc_frostnova"], "proficiency": "adept"}},
    ])
    assert edges["succeeded"] == 2, edges
    ids["doc_edge_adept"] = edges["results"][1]["document_id"]

    return ids


# What the seed puts in, the archive must carry — a silent drop of any
# entity class is exactly the CASE-666 failure shape.
EXPECTED_ARCHIVE_COUNTS = {
    "terminologies": 1,
    "terms": 3,
    "term_relations": 1,
    "templates": 3,
    # Smaug v1+v2, Lich King, 2 spells, 2 relationship docs
    "documents": 7,
    # One identity row per registered ENTITY (Smaug's two versions share
    # one): 1 terminology + 3 terms + 3 templates + 6 document entities.
    # These rows are what make the archive restorable by the server
    # engine — it re-inserts them verbatim and re-claims their keys.
    "registry_entries": 13,
}


def test_golden_round_trip(stack, wip_client, tmp_path):
    archive = tmp_path / "round-trip.zip"

    # Phase 1 — seed
    ids = _seed(wip_client)

    # Phase 2 — export, the CASE-666 shape (--include-inactive)
    export_stats = run_export(
        wip_client, NS, str(archive), include_inactive=True,
    )
    assert not export_stats.warnings, export_stats.warnings

    # Phase 3 — the archive carries EVERYTHING that was seeded
    with ArchiveReader(str(archive)) as reader:
        for entity, expected in EXPECTED_ARCHIVE_COUNTS.items():
            actual = len(list(reader.read_entities(entity)))
            assert actual == expected, (
                f"archive carries {actual} {entity}, seeded {expected} — "
                f"the exporter silently dropped data"
            )

    # Phase 4 — catastrophic loss
    stack.wipe()

    # Phase 5 — restore (preserving IDs) into the emptied instance, through
    # the server-side restore ENGINE — the platform's one import write-path
    # (the client-side importer was deleted with its CLI command). The
    # engine reads the CLI-exported archive via the shared wip-archive
    # format contract, which is exactly the seam this round-trip guards.
    def _engine_restore(target_namespace: str = "") -> None:
        from document_store.models.backup_job import BackupJob
        from document_store.services.backup_engine import DirectRestoreEngine

        async def _run() -> None:
            mongo = BackupJob.get_motor_collection().database.client
            engine = DirectRestoreEngine(mongo, None, lambda _event: None)
            await engine.run_restore(
                Path(str(archive)), target_namespace=target_namespace,
            )

        stack.portal.call(_run)

    _engine_restore()

    # The restored corpus matches what was seeded — counted through the
    # public APIs, the same surfaces phase 6 asserts fidelity against.
    assert wip_client.get(
        "def-store", "/terminologies", params={"namespace": NS},
    )["total"] == 1
    assert wip_client.get(
        "def-store", f"/terminologies/{ids['terminology']}/terms",
        params={"namespace": NS},
    )["total"] == 3
    assert wip_client.get(
        "template-store", "/templates", params={"namespace": NS},
    )["total"] == 3
    assert wip_client.get(
        "document-store", "/documents", params={"namespace": NS},
    )["total"] == 7

    # Phase 6 — fidelity
    # Edge type: original ID, active, class flags intact (CASE-659/660)
    edge = wip_client.get(
        "template-store", f"/templates/{ids['edge_template']}",
        params={"namespace": NS},
    )
    assert edge["status"] == "active"
    assert edge["usage"] == "relationship"
    assert edge["versioned"] is False
    assert edge["identity_fields"] == ["source_ref", "target_ref"]

    # Value resolution works without the original session's caches (CASE-665)
    monster = wip_client.get(
        "template-store", "/templates/MONSTER", params={"namespace": NS},
    )
    assert monster["template_id"] == ids["monster_template"]
    assert monster["header_fields"] == ["name", "power"]
    lore = next(f for f in monster["fields"] if f["name"] == "lore")
    assert lore["full_text_indexed"] is True

    # Documents: same IDs, both versions, metadata intact
    versions = wip_client.get(
        "document-store", f"/documents/{ids['doc_smaug']}/versions",
        params={"namespace": NS},
    )
    version_numbers = sorted(v["version"] for v in versions["versions"])
    assert version_numbers == [1, 2]
    latest = wip_client.get(
        "document-store", f"/documents/{ids['doc_smaug']}",
        params={"namespace": NS},
    )
    assert latest["version"] == 2
    assert latest["data"]["power"] == 97
    assert latest["metadata"]["custom"] == {"source_system": "round-trip-seed"}

    # Relationship query works on restored docs (PoNIF #7 endpoints)
    rels = wip_client.get(
        "document-store", f"/documents/{ids['doc_smaug']}/relationships",
        params={"namespace": NS},
    )
    assert rels["total"] == 2

    # The ontology relation survived the round-trip (CASE-666)
    relations = wip_client.get(
        "def-store", "/ontology/term-relations/all",
        params={"namespace": NS, "status": "active"},
    )
    assert relations["total"] == 1
    assert relations["items"][0]["source_term_value"] == "dragon"
    assert relations["items"][0]["relation_type"] == "is_a"

    # Term aliases survived
    dragon = wip_client.get(
        "def-store", f"/terminologies/{ids['terminology']}/terms",
        params={"namespace": NS, "search": "dragon"},
    )
    assert sorted(dragon["items"][0]["aliases"]) == ["drake", "wyrm"]

    # The restored namespace accepts writes addressed BY VALUE — the
    # restored Registry synonyms carrying a real request (CASE-665's
    # ultimate check)
    write = wip_client.post("document-store", "/documents", json=[
        {"template_id": "MONSTER_HAS_SPELL", "namespace": NS,
         "data": {"source_ref": ids["doc_smaug"],
                  "target_ref": ids["doc_frostnova"], "proficiency": "novice"}},
    ])["results"][0]
    # versioned:false + same identity pair as the seeded adept edge →
    # overwrite in place: same document_id, version stays 1, new payload
    # (PoNIF #8). CASE-671: the single-item route reports this overwrite as
    # "skipped" (no version bump), so assert the BEHAVIOR, not the label.
    assert write["status"] != "error", write
    assert write["document_id"] == ids["doc_edge_adept"]
    assert write["version"] == 1
    overwritten = wip_client.get(
        "document-store", f"/documents/{ids['doc_edge_adept']}",
        params={"namespace": NS},
    )
    assert overwritten["version"] == 1
    assert overwritten["data"]["proficiency"] == "novice"

    # Phase 7 — CASE-668 class: an ID-preserving restore into a SECOND
    # namespace while the restored entities still own the archived IDs must
    # be refused up front, before creating anything (the live incident
    # half-proceeded: target namespace created, then a per-entity
    # clean-target failure cascade). The engine refuses at preflight —
    # re-namespacing is remap mode's job.
    from document_store.services.backup_engine import RestoreEngineError

    with pytest.raises(RestoreEngineError, match="re-namespace"):
        _engine_restore("toolkit-it-2")
    # The refusal must come BEFORE anything is created — the target
    # namespace must not exist afterward.
    with pytest.raises(WIPClientError) as refused:
        wip_client.get("registry", "/namespaces/toolkit-it-2/stats")
    assert refused.value.status_code == 404
