"""Export-flag contracts, end-to-end: real collector → real archive → real engine.

Two seams the mocked suites cannot pin (CASE-823 / CASE-824):

- The default export's version-history contract against the REAL
  document-store: superseded versions are status=inactive there, so the
  default (active-only) export structurally cannot carry them — the archive
  must contain latest-active only AND the manifest must say so honestly.
- The restore engine's identity precondition against a REAL archive the
  CLI collector produced with --skip-synonyms: entity rows without registry
  identity rows refuse up front; allow_missing_identity downgrades the
  refusal to a warning on the event stream.

Same in-process stack as the golden round-trip (see conftest).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from wip_archive.archive import ArchiveReader
from wip_toolkit.export.exporter import run_export

pytestmark = pytest.mark.usefixtures("stack")


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


def test_default_export_is_latest_active_and_manifest_says_so(
    stack, wip_client, tmp_path
):
    ns = "toolkit-it-823"
    wip_client.put(
        "registry", f"/namespaces/{ns}",
        json={"description": "CASE-823 export contract", "isolation_mode": "open"},
    )
    tpl = _created(wip_client.post("template-store", "/templates", json=[{
        "value": "GADGET",
        "label": "Gadget",
        "namespace": ns,
        "identity_fields": ["name"],
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "power", "label": "Power", "type": "integer"},
        ],
    }]))
    v1 = _created(wip_client.post("document-store", "/documents", json=[
        {"template_id": tpl["id"], "namespace": ns,
         "data": {"name": "widget", "power": 1}},
    ]))
    v2 = _created(wip_client.post("document-store", "/documents", json=[
        {"template_id": tpl["id"], "namespace": ns,
         "data": {"name": "widget", "power": 2}},
    ]))
    assert v2["document_id"] == v1["document_id"]
    assert v2["version"] == 2

    # Default export: latest ACTIVE version only, manifest honest, warning
    # emitted. Before CASE-823 the same archive stamped
    # include_all_versions=true while silently dropping v1.
    default_zip = tmp_path / "default.zip"
    events = []
    run_export(
        wip_client, ns, str(default_zip),
        progress_callback=events.append, non_interactive=True,
    )
    assert any(
        e.phase == "warning_version_history_skipped" for e in events
    ), "the lossy default must announce itself"

    with ArchiveReader(str(default_zip)) as reader:
        docs = list(reader.read_entities("documents"))
        assert [d["version"] for d in docs] == [2], (
            "default export must carry exactly the latest active version"
        )
        assert reader.read_manifest().include_all_versions is False

    # --include-inactive is the (only) full-history combination.
    full_zip = tmp_path / "full.zip"
    run_export(wip_client, ns, str(full_zip), include_inactive=True)

    with ArchiveReader(str(full_zip)) as reader:
        docs = list(reader.read_entities("documents"))
        assert sorted(d["version"] for d in docs) == [1, 2]
        assert reader.read_manifest().include_all_versions is True


def test_restore_refuses_archive_without_identity_rows(
    stack, wip_client, tmp_path
):
    ns = "toolkit-it-824"
    wip_client.put(
        "registry", f"/namespaces/{ns}",
        json={"description": "CASE-824 identity precondition", "isolation_mode": "open"},
    )
    terminology = _created(wip_client.post(
        "def-store", "/terminologies",
        json=[{"value": "WIDGET_KIND", "label": "Widget Kind", "namespace": ns}],
    ))
    terms = wip_client.post(
        "def-store", f"/terminologies/{terminology['id']}/terms",
        json=[{"value": "round", "label": "Round"}],
        params={"namespace": ns},
    )
    assert terms["succeeded"] == 1, terms

    # A --skip-synonyms export: entity rows present, registry identity gone.
    archive = tmp_path / "no-identity.zip"
    run_export(
        wip_client, ns, str(archive), skip_synonyms=True, include_inactive=True,
    )
    with ArchiveReader(str(archive)) as reader:
        assert len(list(reader.read_entities("terminologies"))) == 1
        assert len(list(reader.read_entities("registry_entries"))) == 0

    stack.wipe()

    from document_store.models.backup_job import BackupJob
    from document_store.services.backup_engine import (
        DirectRestoreEngine,
        RestoreEngineError,
    )
    from wip_toolkit.client import WIPClientError

    def _engine_restore(*, allow_missing_identity: bool = False) -> list:
        events: list = []

        async def _run() -> None:
            mongo = BackupJob.get_motor_collection().database.client
            engine = DirectRestoreEngine(mongo, None, events.append)
            await engine.run_restore(
                Path(str(archive)),
                target_namespace="",
                allow_missing_identity=allow_missing_identity,
            )

        stack.portal.call(_run)
        return events

    # Refused up front, before anything is created.
    with pytest.raises(RestoreEngineError, match="no registry identity rows"):
        _engine_restore()
    with pytest.raises(WIPClientError) as refused:
        wip_client.get("registry", f"/namespaces/{ns}/stats")
    assert refused.value.status_code == 404

    # The override restores, but the event stream carries the warning.
    events = _engine_restore(allow_missing_identity=True)
    warnings = [e.message for e in events if e.phase == "warning"]
    assert any("NO registry identity rows" in w for w in warnings)
    assert wip_client.get(
        "def-store", "/terminologies", params={"namespace": ns},
    )["total"] == 1
