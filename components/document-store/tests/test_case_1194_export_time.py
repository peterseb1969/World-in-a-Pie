"""A restore reports the archive's export time only when the archive
recorded one; the manifest model fills a missing one with the moment it is
read, which is the restore's own time. Provenance: CASE-1194 (3-R13)."""

from datetime import UTC, datetime

from wip_archive.models import EntityCounts, Manifest

from document_store.services.backup_engine import _recorded_export_time


def test_a_recorded_export_time_is_reported():
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    manifest = Manifest.model_validate(
        {"namespace": "ns", "counts": {}, "exported_at": when.isoformat()}
    )
    assert _recorded_export_time(manifest) == when


def test_a_missing_export_time_is_unrecorded_not_now():
    manifest = Manifest.model_validate({"namespace": "ns", "counts": {}})
    assert manifest.exported_at is not None  # the model's fill
    assert _recorded_export_time(manifest) is None


def test_a_manifest_built_in_code_still_answers():
    assert _recorded_export_time(Manifest(namespace="ns", counts=EntityCounts())) is None
