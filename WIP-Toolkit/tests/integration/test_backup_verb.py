"""`wip-toolkit backup`'s client half against the real backup endpoint:
start the job, follow it, download the archive the engine wrote."""

from __future__ import annotations

import pytest
from wip_archive.archive import ArchiveReader
from wip_toolkit.backup import build_request, download_archive, start_backup
from wip_toolkit.restore import wait_for_job

NS = "toolkit-backup-it"

pytestmark = pytest.mark.usefixtures("stack")


def test_the_server_backup_is_followed_and_downloaded(wip_client, tmp_path):
    wip_client.put("registry", f"/namespaces/{NS}",
                   json={"description": "backup verb", "isolation_mode": "open"})
    created = wip_client.post("template-store", "/templates", json=[{
        "value": "NOTE", "label": "Note", "namespace": NS, "identity_fields": ["title"],
        "fields": [{"name": "title", "label": "Title", "type": "string", "mandatory": True}],
    }])["results"][0]
    assert created["status"] == "created", created
    docs = wip_client.post("document-store", "/documents", json=[
        {"template_id": created["id"], "namespace": NS, "data": {"title": "one"}}])
    assert docs["succeeded"] == 1, docs

    snapshot = start_backup(wip_client, NS, build_request())
    assert snapshot["job_id"].startswith("bkp-")
    final = wait_for_job(wip_client, snapshot["job_id"], poll_interval=0.05, timeout=60)
    assert final["status"] == "complete", final

    out = tmp_path / "nested" / "note.zip"
    size = download_archive(wip_client, final["job_id"], out)
    assert size == out.stat().st_size > 0
    assert not (tmp_path / "nested" / "note.zip.partial").exists()
    with ArchiveReader(out) as reader:
        assert reader.list_namespaces() == [NS]
        [doc] = list(reader.read_entities("documents", namespace=NS))
        assert doc["data"] == {"title": "one"}
        assert "derived_from" not in reader.read_manifest_raw()


def test_a_retired_option_is_refused_by_the_server(wip_client):
    """build_request sends only the engine's live options; the endpoint
    refuses the retired ones, so sending one must fail loudly."""
    from wip_toolkit.client import WIPClientError

    with pytest.raises(WIPClientError):
        start_backup(wip_client, NS, {**build_request(), "latest_only": True})
