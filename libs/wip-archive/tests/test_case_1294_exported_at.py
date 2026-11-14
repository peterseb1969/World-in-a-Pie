"""A manifest's export time is empty when the archive does not say.

The field used to default to the moment the manifest was read, so every
reader that did not check which fields were set reported its own time as
the archive's export time."""

from datetime import UTC, datetime

from test_latest_only import _write
from wip_archive.archive import ArchiveReader
from wip_archive.model import ArchiveModel
from wip_archive.models import Manifest


def test_a_manifest_that_does_not_say_has_no_export_time():
    assert Manifest().exported_at is None
    assert Manifest.model_validate({"namespace": "ns"}).exported_at is None


def test_a_recorded_export_time_is_kept():
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    assert Manifest.model_validate({"exported_at": when.isoformat()}).exported_at == when


def test_the_model_summary_reports_no_export_time_rather_than_now(tmp_path):
    path = _write(tmp_path / "in.zip", [])
    with ArchiveReader(path) as reader:
        assert ArchiveModel.load(reader).summary()["exported_at"] is None
