"""A row whose version is not a number is refused with a typed error that
names the row, by every reader that counts versions: the model's loading,
the definition index, latest-only's pinned references, migrate and
export-csv all go through row_version. A bare ValueError from int() used to
escape instead, and latest-only's pinned-reference pass crashed on it."""

import pytest
from test_latest_only import A, B, _doc, _ref, _write
from wip_archive.archive import ArchiveReader
from wip_archive.definitions import DefinitionIndex
from wip_archive.exceptions import ArchiveError, RowVersionError
from wip_archive.latest_only import plan_latest_only
from wip_archive.model import ArchiveModel, row_version


@pytest.mark.parametrize("value", [None, "", 0, "3", 2.0])
def test_a_usable_version_still_counts(value):
    assert row_version({"version": value}) == (int(value) if value else 1)


def test_a_non_numeric_version_names_the_row():
    with pytest.raises(RowVersionError, match="document D1 has version 'latest'"):
        row_version({"document_id": "D1", "version": "latest"})
    with pytest.raises(RowVersionError, match="template T1 has version"):
        row_version({"template_id": "T1", "version": [1]})
    assert issubclass(RowVersionError, ArchiveError)


def test_the_definition_index_counts_a_template_version_the_same_way():
    index = DefinitionIndex()
    index.add_template({"template_id": "T1", "version": "2"})
    assert ("T1", 2) in index.templates
    with pytest.raises(RowVersionError, match="template T2"):
        index.add_template({"template_id": "T2", "version": "v2"})


def test_a_pin_whose_version_is_not_a_number_names_the_pinning_row(tmp_path):
    documents = [_doc(A, 1), _doc(A, 2, status="active"),
                 _doc(B, 1, status="active", refs=[_ref(A, "latest", "pinned")])]
    path = _write(tmp_path / "in.zip", documents)
    with ArchiveReader(path) as reader:
        model = ArchiveModel.load(reader)
        with pytest.raises(RowVersionError, match=f"document {B} v1's pinned reference "
                                                  f"to document {A} has version 'latest'"):
            plan_latest_only(reader, model)


def test_loading_an_archive_with_a_non_numeric_document_version_is_refused(tmp_path):
    path = _write(tmp_path / "in.zip", [_doc(A, "one", status="active")])
    with ArchiveReader(path) as reader, pytest.raises(RowVersionError, match=f"document {A}"):
        ArchiveModel.load(reader)
