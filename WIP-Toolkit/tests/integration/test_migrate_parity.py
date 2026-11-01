"""The offline `migrate` surgery writes what live migration writes.

One engine backup is taken before a live `POST /documents/migrate` applies,
one after. `wip-toolkit migrate` runs on the first; its rows must equal the
second's in everything but when and by whom they were written. A document
live migration refuses (a newly mandatory field it lacks) is migrated
offline with a fill, verified, and restored fresh through the real engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from wip_archive.archive import ArchiveReader
from wip_toolkit.cli import main

NS = "toolkit-migrate-surgery-it"
NS_COPY = "toolkit-migrate-surgery-it-copy"

pytestmark = pytest.mark.usefixtures("stack")

# Written by whoever ran the migration, at the time it ran: the only fields
# the live and offline rows are allowed to differ in.
WRITER_FIELDS = {"updated_at", "updated_by", "created_by"}


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


def _engine(kind: str):
    from document_store.models.backup_job import BackupJob
    from document_store.services.backup_engine import DirectBackupEngine, DirectRestoreEngine

    mongo = BackupJob.get_motor_collection().database.client
    cls = DirectBackupEngine if kind == "backup" else DirectRestoreEngine
    return cls(mongo, None, lambda _e: None)


def _rows(path: Path) -> dict:
    with ArchiveReader(path) as reader:
        return {(d["document_id"], d["version"]): d
                for d in reader.read_entities("documents", namespace=NS)}


def _comparable(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in WRITER_FIELDS}


def test_offline_migrate_writes_what_live_migrate_writes(stack, wip_client, tmp_path):
    wip_client.put("registry", f"/namespaces/{NS}", json={"description": "migrate surgery"})
    colour = _created(wip_client.post("def-store", "/terminologies", json=[
        {"value": "COLOUR", "label": "Colour", "namespace": NS}]))
    terms = wip_client.post("def-store", f"/terminologies/{colour['id']}/terms",
                            json=[{"value": v, "label": v.title()} for v in ("red", "blue")],
                            params={"namespace": NS})
    assert terms["succeeded"] == 2, terms
    v1_fields = [
        {"name": "code", "label": "Code", "type": "string", "mandatory": True},
        {"name": "colour", "label": "Colour", "type": "term", "terminology_ref": "COLOUR"},
        {"name": "name", "label": "Name", "type": "string"},
    ]
    template = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "ITEM", "label": "Item", "namespace": NS, "identity_fields": ["code"],
        "fields": v1_fields}]))
    for data in ({"code": "A", "colour": "red", "name": "alpha"},
                 {"code": "E", "colour": "blue", "name": "eps"},
                 {"code": "B", "colour": "blue"}):
        _created(wip_client.post("document-store", "/documents", json=[
            {"template_id": template["id"], "template_version": 1, "namespace": NS,
             "data": data}]))
    v2 = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "ITEM", "label": "Item", "namespace": NS, "identity_fields": ["code"],
        "fields": [v1_fields[0], v1_fields[1],
                   {"name": "title", "label": "Title", "type": "string", "mandatory": True}],
        "renames": {"title": "name"}}]))
    assert v2["version"] == 2, v2

    before = tmp_path / "before.zip"
    stack.portal.call(lambda: _engine("backup").run_backup(NS, before))
    live = wip_client.post("document-store", "/documents/migrate", json={
        "template_id": template["id"], "from_version": 1, "to_version": 2, "dry_run": False,
    }, params={"namespace": NS})
    statuses = sorted(r["status"] for r in live["results"])
    assert statuses == ["error", "updated", "updated"], live  # B lacks the new mandatory title
    after = tmp_path / "after.zip"
    stack.portal.call(lambda: _engine("backup").run_backup(NS, after))

    # Offline, the same migration without fills: B must be skipped, as live.
    mapping = tmp_path / "plain.json"
    mapping.write_text(json.dumps({"template": "ITEM", "to_version": 2}))
    offline = tmp_path / "offline.zip"
    refused = CliRunner().invoke(main, ["migrate", str(before), str(offline), "--mapping",
                                        str(mapping)])
    assert refused.exit_code == 1 and "title: required" in refused.output
    result = CliRunner().invoke(main, ["migrate", str(before), str(offline), "--mapping",
                                       str(mapping), "--skip-failing", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verdict"] == "pass"

    live_rows, offline_rows = _rows(after), _rows(offline)
    assert sorted(live_rows) == sorted(offline_rows)
    # Five rows: A and E retired at v1 and written at v2, B untouched at v1.
    assert sum(1 for (_d, v) in live_rows if v == 2) == 2 and len(live_rows) == 5
    for key in live_rows:
        assert _comparable(offline_rows[key]) == _comparable(live_rows[key]), key

    # The document live refuses, migrated offline with a fill.
    filled_mapping = tmp_path / "filled.json"
    filled_mapping.write_text(json.dumps({"template": "ITEM", "to_version": 2,
                                          "fill": {"title": {"format": "{code}-untitled"}}}))
    filled = tmp_path / "filled.zip"
    result = CliRunner().invoke(main, ["migrate", str(before), str(filled), "--mapping",
                                       str(filled_mapping), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "pass", payload["findings"]
    assert payload["hop"]["summary"]["documents_migrated"] == 3
    assert payload["hop"]["summary"]["fields_filled"] == {"title": 1}

    stack.portal.call(lambda: _engine("restore").run_remap(Path(filled), NS_COPY))
    docs = wip_client.get("document-store", "/documents",
                          params={"namespace": NS_COPY, "latest_only": "true"})["items"]
    restored_b = next(d for d in docs if d["data"]["code"] == "B")
    assert restored_b["version"] == 2 and restored_b["template_version"] == 2
    assert restored_b["data"]["title"] == "B-untitled"
