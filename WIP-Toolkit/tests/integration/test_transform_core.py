"""The shaping-transform core against the real engines, both ends.

A real engine backup is cut with `wip_archive.transform.run_transform`
(SPELL documents dropped, MONSTER documents and a MONSTER→SPELL relationship
kept), and the result is restored fresh by the real engine beside the source
namespace. What this proves is the plan's central claim (design doc §7.1):
a reference crossing the cut still resolves on an install that holds the cut
entity, because the cut entity travels as a referent the door resolves by its
keys, not by its old id.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from wip_archive.archive import ArchiveReader
from wip_archive.transform import file_sha256, run_transform

NS = "toolkit-transform-it"
NS_COPY = "toolkit-transform-it-copy"

pytestmark = pytest.mark.usefixtures("stack")


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


def _seed(client) -> dict:
    ids: dict = {}
    client.put("registry", f"/namespaces/{NS}",
               json={"description": "Transform core", "isolation_mode": "open"})
    spell = _created(client.post("template-store", "/templates", json=[{
        "value": "SPELL", "label": "Spell", "namespace": NS,
        "identity_fields": ["name"],
        "fields": [{"name": "name", "label": "Name", "type": "string", "mandatory": True}],
    }]))
    ids["spell_template"] = spell["id"]
    monster = _created(client.post("template-store", "/templates", json=[{
        "value": "MONSTER", "label": "Monster", "namespace": NS,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "favourite", "label": "Favourite spell", "type": "reference",
             "reference_type": "document", "target_templates": ["SPELL"]},
        ],
    }]))
    ids["monster_template"] = monster["id"]
    edge = _created(client.post("template-store", "/templates", json=[{
        "value": "MONSTER_KNOWS", "label": "Monster knows", "namespace": NS,
        "usage": "relationship", "versioned": False,
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": ["MONSTER"], "target_templates": ["SPELL"],
        "fields": [
            {"name": "source_ref", "label": "Monster", "type": "reference",
             "reference_type": "document", "target_templates": ["MONSTER"], "mandatory": True},
            {"name": "target_ref", "label": "Spell", "type": "reference",
             "reference_type": "document", "target_templates": ["SPELL"], "mandatory": True},
        ],
    }]))
    ids["edge_template"] = edge["id"]

    fireball = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["spell_template"], "namespace": NS, "data": {"name": "Fireball"}},
    ]))
    ids["doc_fireball"] = fireball["document_id"]
    smaug = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": NS,
         "data": {"name": "Smaug", "favourite": ids["doc_fireball"]}},
    ]))
    ids["doc_smaug"] = smaug["document_id"]
    knows = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["edge_template"], "namespace": NS,
         "data": {"source_ref": ids["doc_smaug"], "target_ref": ids["doc_fireball"]}},
    ]))
    ids["doc_knows"] = knows["document_id"]
    return ids


def _mongo():
    from document_store.models.backup_job import BackupJob

    return BackupJob.get_motor_collection().database.client


def _backup_engine():
    from document_store.services.backup_engine import DirectBackupEngine

    return DirectBackupEngine(_mongo(), None, lambda _event: None)


def _restore_engine():
    from document_store.services.backup_engine import DirectRestoreEngine

    return DirectRestoreEngine(_mongo(), None, lambda _event: None)


def test_a_cut_reference_resolves_after_a_real_fresh_restore(stack, wip_client, tmp_path):
    ids = _seed(wip_client)
    backup = tmp_path / "backup.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(NS, backup))

    cut = tmp_path / "cut.zip"
    result = run_transform(
        backup, cut, transform="filter", toolkit_version="0.0.0-test",
        params={"templates": ["MONSTER", "MONSTER_KNOWS"], "boundary": "drop"},
        keep_row=lambda _ns, t, row: t != "documents"
        or row["template_id"] != ids["spell_template"],
    )

    # The output: no spell document, the spell's Registry row a referent, the
    # kept rows exactly the engine's, and a verified hop.
    with ArchiveReader(cut) as reader, ArchiveReader(backup) as original:
        kept = list(reader.read_entities("documents", namespace=NS))
        assert {d["document_id"] for d in kept} == {ids["doc_smaug"], ids["doc_knows"]}
        assert kept == [d for d in original.read_entities("documents", namespace=NS)
                        if d["document_id"] != ids["doc_fireball"]]
        referents = {r["entry_id"] for r in reader.read_referents()}
        assert ids["doc_fireball"] in referents
        carried = {r["entry_id"] for r in reader.read_entities("registry_entries", namespace=NS)}
        assert ids["doc_fireball"] not in carried
    assert result.hop["input"]["sha256"] == file_sha256(backup)
    assert result.verdict == "pass", [f.to_dict() for f in result.model.findings()]

    # The door: a fresh restore beside the source, on the install that still
    # holds Fireball. Both the plain reference and the relationship's target
    # resolve to the original spell through its referent row.
    stack.portal.call(lambda: _restore_engine().run_remap(Path(cut), NS_COPY))

    docs = wip_client.get("document-store", "/documents",
                          params={"namespace": NS_COPY, "page_size": 100})["items"]
    by_template = {}
    for doc in docs:
        by_template.setdefault(doc["template_id"], []).append(doc)
    assert len(docs) == 2, docs

    templates = {t["value"]: t["template_id"] for t in wip_client.get(
        "template-store", "/templates", params={"namespace": NS_COPY})["items"]}
    [smaug] = by_template[templates["MONSTER"]]
    [knows] = by_template[templates["MONSTER_KNOWS"]]
    assert smaug["document_id"] != ids["doc_smaug"]  # fresh: re-minted

    favourite = next(r for r in smaug["references"] if r["field_path"] == "favourite")
    assert favourite["resolved"]["document_id"] == ids["doc_fireball"]
    target = next(r for r in knows["references"] if r["field_path"] == "target_ref")
    assert target["resolved"]["document_id"] == ids["doc_fireball"]
    source = next(r for r in knows["references"] if r["field_path"] == "source_ref")
    assert source["resolved"]["document_id"] == smaug["document_id"]
