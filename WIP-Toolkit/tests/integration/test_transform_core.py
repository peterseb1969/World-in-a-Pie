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

import json
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


def _seed(client, ns: str = NS) -> dict:
    ids: dict = {}
    client.put("registry", f"/namespaces/{ns}",
               json={"description": "Transform core", "isolation_mode": "open"})
    spell = _created(client.post("template-store", "/templates", json=[{
        "value": "SPELL", "label": "Spell", "namespace": ns,
        "identity_fields": ["name"],
        "fields": [{"name": "name", "label": "Name", "type": "string", "mandatory": True}],
    }]))
    ids["spell_template"] = spell["id"]
    monster = _created(client.post("template-store", "/templates", json=[{
        "value": "MONSTER", "label": "Monster", "namespace": ns,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "favourite", "label": "Favourite spell", "type": "reference",
             "reference_type": "document", "target_templates": ["SPELL"]},
        ],
    }]))
    ids["monster_template"] = monster["id"]
    edge = _created(client.post("template-store", "/templates", json=[{
        "value": "MONSTER_KNOWS", "label": "Monster knows", "namespace": ns,
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
        {"template_id": ids["spell_template"], "namespace": ns, "data": {"name": "Fireball"}},
    ]))
    ids["doc_fireball"] = fireball["document_id"]
    smaug = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": ns,
         "data": {"name": "Smaug", "favourite": ids["doc_fireball"]}},
    ]))
    ids["doc_smaug"] = smaug["document_id"]
    knows = _created(client.post("document-store", "/documents", json=[
        {"template_id": ids["edge_template"], "namespace": ns,
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


FILTER_NS = "toolkit-filter-it"
FILTER_COPY = "toolkit-filter-it-copy"


def test_filter_cli_on_a_real_backup_brings_relationships_and_restores(
    stack, wip_client, tmp_path
):
    """`wip-toolkit filter MONSTER` on an engine backup: the relationship
    comes with its source, both spell references cross the cut as referents,
    and a real fresh restore resolves them to the original spell."""
    from click.testing import CliRunner
    from wip_toolkit.cli import main

    ids = _seed(wip_client, FILTER_NS)
    backup = tmp_path / "backup.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(FILTER_NS, backup))

    cut = tmp_path / "monsters.zip"
    result = CliRunner().invoke(main, ["filter", str(backup), str(cut), "MONSTER", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "pass", payload["findings"]
    summary = payload["hop"]["summary"]
    assert summary["selected_documents"] == 1
    assert summary["relationship_documents"] == 1
    assert summary["cut_documents"] == 1
    with ArchiveReader(cut) as reader:
        assert {d["document_id"] for d in reader.read_entities("documents", namespace=FILTER_NS)} \
            == {ids["doc_smaug"], ids["doc_knows"]}
        assert [r["entry_id"] for r in reader.read_referents()] == [ids["doc_fireball"]]

    stack.portal.call(lambda: _restore_engine().run_remap(Path(cut), FILTER_COPY))
    docs = wip_client.get("document-store", "/documents",
                          params={"namespace": FILTER_COPY, "page_size": 100})["items"]
    assert len(docs) == 2, docs
    targets = {
        r["field_path"]: r["resolved"]["document_id"]
        for doc in docs for r in doc["references"]
        if r["field_path"] in ("favourite", "target_ref")
    }
    assert targets == {"favourite": ids["doc_fireball"], "target_ref": ids["doc_fireball"]}


PRUNE_NS = "toolkit-prune-it"
PRUNE_COPY = "toolkit-prune-it-copy"


def test_prune_on_a_real_backup_drops_only_the_unused_and_restores(
    stack, wip_client, tmp_path
):
    """An engine backup with one template nothing uses (and a vocabulary only
    it declares): prune removes those two, keeps every document, and the
    output restores fresh through the real engine."""
    from click.testing import CliRunner
    from wip_toolkit.cli import main

    ids = _seed(wip_client, PRUNE_NS)
    unused_lov = _created(wip_client.post("def-store", "/terminologies", json=[
        {"value": "UNUSED_MOOD", "label": "Unused mood", "namespace": PRUNE_NS}]))
    unused = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "UNUSED", "label": "Unused", "namespace": PRUNE_NS,
        "fields": [{"name": "mood", "label": "Mood", "type": "term",
                    "terminology_ref": "UNUSED_MOOD"}],
    }]))
    backup = tmp_path / "backup.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(PRUNE_NS, backup))

    out = tmp_path / "pruned.zip"
    result = CliRunner().invoke(main, ["prune", str(backup), str(out), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "pass", payload["findings"]
    assert payload["hop"]["summary"]["templates_pruned"] == [f"{PRUNE_NS}:UNUSED"]
    assert payload["hop"]["summary"]["terminologies_pruned"] == [f"{PRUNE_NS}:UNUSED_MOOD"]
    with ArchiveReader(out) as reader, ArchiveReader(backup) as original:
        assert list(reader.read_entities("documents", namespace=PRUNE_NS)) \
            == list(original.read_entities("documents", namespace=PRUNE_NS))
        templates = {t["template_id"] for t in reader.read_entities("templates", namespace=PRUNE_NS)}
        assert unused["id"] not in templates and ids["monster_template"] in templates
        carried = {r["entry_id"] for r in reader.read_entities("registry_entries",
                                                                namespace=PRUNE_NS)}
        assert unused["id"] not in carried and unused_lov["id"] not in carried
        assert list(reader.read_referents()) == []

    stack.portal.call(lambda: _restore_engine().run_remap(Path(out), PRUNE_COPY))
    restored = wip_client.get("template-store", "/templates",
                              params={"namespace": PRUNE_COPY})["items"]
    assert {t["value"] for t in restored} == {"SPELL", "MONSTER", "MONSTER_KNOWS"}
    docs = wip_client.get("document-store", "/documents",
                          params={"namespace": PRUNE_COPY, "page_size": 100})["items"]
    assert len(docs) == 3


LATEST_NS = "toolkit-latest-it"
LATEST_COPY = "toolkit-latest-it-copy"


def test_latest_only_on_a_real_backup_keeps_pinned_versions_and_restores(
    stack, wip_client, tmp_path
):
    """Smaug reaches v3; a HOARD document pinned Smaug while it was v1.
    latest-only keeps v3 (newest) and v1 (pinned), drops v2, and the output
    restores fresh through the real engine."""
    from click.testing import CliRunner
    from wip_toolkit.cli import main

    ids = _seed(wip_client, LATEST_NS)
    hoard = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "HOARD", "label": "Hoard", "namespace": LATEST_NS,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "keeper", "label": "Keeper", "type": "reference",
             "reference_type": "document", "target_templates": [ids["monster_template"]],
             "version_strategy": "pinned"},
        ],
    }]))
    _created(wip_client.post("document-store", "/documents", json=[
        {"template_id": hoard["id"], "namespace": LATEST_NS,
         "data": {"name": "Lonely Mountain", "keeper": ids["doc_smaug"]}}]))
    for favourite in (None, ids["doc_fireball"]):
        data = {"name": "Smaug"} if favourite is None else {"name": "Smaug", "favourite": favourite}
        _created(wip_client.post("document-store", "/documents", json=[
            {"template_id": ids["monster_template"], "namespace": LATEST_NS, "data": data}]))
    versions = wip_client.get("document-store", f"/documents/{ids['doc_smaug']}/versions",
                              params={"namespace": LATEST_NS})["versions"]
    assert sorted(v["version"] for v in versions) == [1, 2, 3]

    backup = tmp_path / "backup.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(LATEST_NS, backup))
    with ArchiveReader(backup) as reader:
        [hoard_row] = [d for d in reader.read_entities("documents", namespace=LATEST_NS)
                       if d["template_id"] == hoard["id"]]
    [keeper] = [r for r in hoard_row["references"] if r["field_path"] == "keeper"]
    # The platform's own record of a pinned reference: the strategy, and the
    # version it saw.
    assert keeper["version_strategy"] == "pinned"
    assert keeper["resolved"]["version"] == 1

    out = tmp_path / "latest.zip"
    result = CliRunner().invoke(main, ["latest-only", str(backup), str(out), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "pass", payload["findings"]
    assert payload["hop"]["summary"]["versions_dropped"] == 1
    assert payload["hop"]["summary"]["pinned_versions_kept"] == 1
    with ArchiveReader(out) as reader:
        smaug = sorted(d["version"] for d in reader.read_entities("documents", namespace=LATEST_NS)
                       if d["document_id"] == ids["doc_smaug"])
        assert smaug == [1, 3]
        assert reader.read_manifest_raw()["include_all_versions"] is False

    stack.portal.call(lambda: _restore_engine().run_remap(Path(out), LATEST_COPY))
    docs = wip_client.get("document-store", "/documents",
                          params={"namespace": LATEST_COPY, "page_size": 100,
                                  "latest_only": "true"})["items"]
    restored_smaug = next(d for d in docs if d["data"].get("name") == "Smaug")
    assert restored_smaug["data"].get("favourite") is not None
    copy_versions = wip_client.get(
        "document-store", f"/documents/{restored_smaug['document_id']}/versions",
        params={"namespace": LATEST_COPY})["versions"]
    assert sorted(v["version"] for v in copy_versions) == [1, 3]


STRIP_NS = "toolkit-strip-it"
STRIP_COPY = "toolkit-strip-it-copy"


def test_strip_blobs_on_a_real_backup_cuts_files_and_the_door_finds_them(
    stack, wip_client, tmp_path
):
    """A document holds a file. strip-blobs cuts the file row to a referent
    keyed by its checksum; a real fresh restore on the install that still
    holds the file points the restored document at that very file.

    The harness has no object store, so the file is registered through the
    Registry and its metadata row inserted directly: the same Registry key
    (checksum, namespace) and row an upload creates.
    """
    from click.testing import CliRunner
    from wip_toolkit.cli import main

    from document_store.models.file import File, FileStatus

    wip_client.put("registry", f"/namespaces/{STRIP_NS}",
                   json={"description": "strip-blobs", "isolation_mode": "open"})
    checksum = "c0ffee" * 10 + "abcd"
    registered = wip_client.post("registry", "/entries/register", json=[{
        "namespace": STRIP_NS, "entity_type": "files",
        "composite_key": {"checksum": checksum}, "metadata": {"type": "file"},
    }])["results"][0]
    file_id = registered["registry_id"]

    async def _insert_file() -> None:
        await File(namespace=STRIP_NS, file_id=file_id, filename="scroll.txt",
                   content_type="text/plain", size_bytes=5, checksum=checksum,
                   storage_key=file_id, status=FileStatus.ACTIVE).insert()

    stack.portal.call(_insert_file)
    scroll = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "SCROLL", "label": "Scroll", "namespace": STRIP_NS,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "scan", "label": "Scan", "type": "file",
             "file_config": {"allowed_types": ["text/*"], "max_size_mb": 1}},
        ],
    }]))
    _created(wip_client.post("document-store", "/documents", json=[
        {"template_id": scroll["id"], "namespace": STRIP_NS,
         "data": {"name": "Fire spell", "scan": file_id}}]))

    backup = tmp_path / "backup.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(STRIP_NS, backup))
    out = tmp_path / "stripped.zip"
    result = CliRunner().invoke(main, ["strip-blobs", str(backup), str(out), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "pass", payload["findings"]
    with ArchiveReader(out) as reader:
        assert list(reader.read_entities("files", namespace=STRIP_NS)) == []
        [referent] = list(reader.read_referents())
    assert referent["entry_id"] == file_id
    assert referent["primary_composite_key"] == {"checksum": checksum}

    stack.portal.call(lambda: _restore_engine().run_remap(Path(out), STRIP_COPY))
    [doc] = wip_client.get("document-store", "/documents",
                           params={"namespace": STRIP_COPY, "page_size": 10})["items"]
    assert [f["file_id"] for f in doc["file_references"]] == [file_id]
    assert doc["data"]["scan"] == file_id
    files = wip_client.get("document-store", "/files",
                           params={"namespace": STRIP_COPY})["items"]
    assert files == []  # no content-less file record was created


DIFF_NS = "toolkit-diff-it"


def test_diff_of_two_real_backups_shows_the_new_version(stack, wip_client, tmp_path):
    """Two engine backups of one namespace with an update between them:
    diff reports the new document version and nothing else in documents."""
    from click.testing import CliRunner
    from wip_toolkit.cli import main

    ids = _seed(wip_client, DIFF_NS)
    before = tmp_path / "before.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(DIFF_NS, before))
    _created(wip_client.post("document-store", "/documents", json=[
        {"template_id": ids["monster_template"], "namespace": DIFF_NS,
         "data": {"name": "Smaug"}}]))
    after = tmp_path / "after.zip"
    stack.portal.call(lambda: _backup_engine().run_backup(DIFF_NS, after))

    same = CliRunner().invoke(main, ["diff", str(before), str(before)])
    assert same.exit_code == 0, same.output
    result = CliRunner().invoke(main, ["diff", str(before), str(after), "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    docs = next(t for t in payload["types"]
                if t["namespace"] == DIFF_NS and t["entity_type"] == "documents")
    assert docs["added_samples"] == [f"{ids['doc_smaug']}/2"]
    assert docs["removed"] == 0
    # Writing v2 retires v1 in place: the engine changes exactly its status
    # and updated_at, and diff names those two fields.
    assert docs["changed"] == 1
    assert docs["changed_samples"] == {f"{ids['doc_smaug']}/1": ["status", "updated_at"]}


SPLIT_A = "toolkit-split-a"
SPLIT_B = "toolkit-split-b"
SPLIT_B_COPY = "toolkit-split-b-copy"


def test_split_a_real_two_namespace_backup_and_restore_one_part(stack, wip_client, tmp_path):
    """A two-namespace engine backup where B's document references A's
    spell. split --by namespace writes one part each; B's part carries the
    spell as a referent, and a real fresh restore of B alone points its
    document at the original spell."""
    from click.testing import CliRunner
    from wip_toolkit.cli import main

    ids = _seed(wip_client, SPLIT_A)
    wip_client.put("registry", f"/namespaces/{SPLIT_B}",
                   json={"description": "split B", "isolation_mode": "open"})
    note = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "NOTE", "label": "Note", "namespace": SPLIT_B, "identity_fields": ["title"],
        "fields": [
            {"name": "title", "label": "Title", "type": "string", "mandatory": True},
            {"name": "about", "label": "About", "type": "reference", "reference_type": "document",
             "target_templates": [f"{SPLIT_A}:SPELL"]},
        ],
    }]))
    _created(wip_client.post("document-store", "/documents", json=[
        {"template_id": note["id"], "namespace": SPLIT_B,
         "data": {"title": "On fire", "about": ids["doc_fireball"]}}]))

    backup = tmp_path / "both.zip"
    stack.portal.call(lambda: _backup_engine().run_backup([SPLIT_A, SPLIT_B], backup))
    parts = tmp_path / "parts"
    result = CliRunner().invoke(main, ["split", str(backup), str(parts), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [p["part"] for p in payload["parts"]] == [SPLIT_A, SPLIT_B]
    assert all(p["verdict"] != "fail" for p in payload["parts"]), payload
    with ArchiveReader(parts / f"{SPLIT_B}.zip") as reader:
        assert reader.list_namespaces() == [SPLIT_B]
        assert ids["doc_fireball"] in {r["entry_id"] for r in reader.read_referents()}

    stack.portal.call(lambda: _restore_engine().run_remap(Path(parts / f"{SPLIT_B}.zip"),
                                                          SPLIT_B_COPY))
    [restored] = wip_client.get("document-store", "/documents",
                                params={"namespace": SPLIT_B_COPY})["items"]
    about = next(r for r in restored["references"] if r["field_path"] == "about")
    assert about["resolved"]["document_id"] == ids["doc_fireball"]
