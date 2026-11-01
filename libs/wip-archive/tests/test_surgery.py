"""The surgery core: the rewrite hook, strict identity, and the shared
recomputation helpers."""

import pytest
from test_transform import DOC_M, MONSTER_FIELDS, NS, _build, _keep_all, _run
from wip_archive.archive import ArchiveReader
from wip_archive.surgery import (
    IdentityChangeError,
    identity_hash,
    rename_paths,
    require_same_identity,
    stamp,
)

TEMPLATE = {"identity_fields": ["name"], "fields": MONSTER_FIELDS}


def _documents(path):
    with ArchiveReader(path) as reader:
        return list(reader.read_entities("documents", namespace=NS))


class TestRewriteHook:
    def test_a_rewrite_replaces_a_row_with_the_rows_it_returns(self, tmp_path):
        src = _build(tmp_path / "in.zip")
        out = tmp_path / "out.zip"

        def rewrite(_ns, entity_type, row):
            if entity_type == "documents" and row["document_id"] == DOC_M and row["version"] == 2:
                return [{**row, "data": {**row["data"], "note": "x"}}]
            return [row]

        result = _run(src, out, _keep_all, rewrite_row=rewrite)
        [edited] = [d for d in _documents(out) if d["document_id"] == DOC_M and d["version"] == 2]
        assert edited["data"]["note"] == "x"
        assert result.hop["summary"]["documents_rewritten"] == 1

    def test_strict_identity_makes_a_mismatch_an_error(self, tmp_path):
        src = _build(tmp_path / "in.zip")

        def rename_smaug(_ns, entity_type, row):
            # An edit that changes an identity value without recomputing the
            # stored identity hash: exactly what surgery must never do.
            if entity_type == "documents" and row["document_id"] == DOC_M:
                return [{**row, "data": {**row["data"], "name": "Not Smaug"}}]
            return [row]

        lenient = _run(src, tmp_path / "a.zip", _keep_all, rewrite_row=rename_smaug)
        strict = _run(src, tmp_path / "b.zip", _keep_all, rewrite_row=rename_smaug,
                      strict_identity=True)
        assert lenient.verdict == "warn"
        assert strict.verdict == "fail"
        assert {f.finding_class: f.severity for f in strict.findings}["identity_hash_mismatch"] \
            == "error"


class TestHelpers:
    def test_require_same_identity_refuses_an_identity_change(self):
        data = {"name": "Smaug", "colour": "red"}
        row = {"document_id": "d", "version": 1,
               "identity_hash": identity_hash(data, TEMPLATE, [], [])}
        require_same_identity(row, {**data, "colour": "gold"}, TEMPLATE, [], [])
        with pytest.raises(IdentityChangeError, match="fork"):
            require_same_identity(row, {**data, "name": "Glaurung"}, TEMPLATE, [], [])

    def test_an_identity_less_template_has_nothing_to_keep(self):
        assert identity_hash({"a": 1}, {"identity_fields": [], "fields": []}, [], []) is None

    def test_rename_paths_moves_snapshots_of_a_renamed_field(self):
        moved = rename_paths(
            [{"field_path": "hue", "term_id": "t"}, {"field_path": "hue[1]", "term_id": "u"},
             {"field_path": "hues", "term_id": "v"}, {"field_path": "lair.city"}],
            {"colour": "hue"},
        )
        assert [m["field_path"] for m in moved] == ["colour", "colour[1]", "hues", "lair.city"]

    def test_stamp_has_the_archive_form(self):
        from datetime import UTC, datetime

        assert stamp(datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)) == "2026-11-01 12:00:00.000000"
