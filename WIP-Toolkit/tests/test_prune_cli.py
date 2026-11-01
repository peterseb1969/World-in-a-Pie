"""`wip-toolkit prune`: unused templates go, the rest travels, the hop says
what was removed."""

import json

from click.testing import CliRunner
from test_filter_cli import MONSTER, NS, archive  # noqa: F401 — pytest fixture
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_toolkit.cli import main

UNUSED = "0190c000-0000-7000-0000-0000000000u1"


def run(*args):
    return CliRunner().invoke(main, ["prune", *map(str, args)])


def _with_unused_template(src, tmp_path):
    """The filter fixture plus a template no document pins and no schema
    declares."""
    path = tmp_path / "with-unused.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    with ArchiveReader(src) as reader:
        for entity_type in ("templates", "documents", "registry_entries"):
            for row in reader.read_entities(entity_type, namespace=NS):
                writer.add_entity(entity_type, row, namespace=NS)
    writer.add_entity("templates", {"template_id": UNUSED, "version": 1, "value": "UNUSED",
                                    "namespace": NS, "status": "active", "fields": []},
                      namespace=NS)
    writer.add_entity("registry_entries", {
        "entry_id": UNUSED, "namespace": NS, "entity_type": "templates",
        "primary_composite_key": {}, "primary_composite_key_hash": "h", "synonyms": [],
        "status": "active"}, namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "counts": {},
                  "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def test_an_unused_template_is_pruned_and_recorded(archive, tmp_path):  # noqa: F811
    src = _with_unused_template(archive, tmp_path)
    out = tmp_path / "out.zip"
    result = run(src, out)
    assert result.exit_code == 0, result.output
    assert f"pruned template {NS}:UNUSED" in result.output
    assert "verdict: pass" in result.output
    with ArchiveReader(out) as reader:
        kept = {t["template_id"] for t in reader.read_entities("templates", namespace=NS)}
        [hop] = reader.read_manifest_raw()["derived_from"]
    assert UNUSED not in kept and MONSTER in kept
    assert hop["transform"] == "prune"
    assert hop["summary"]["templates_pruned"] == [f"{NS}:UNUSED"]


def test_nothing_unused_says_so_and_still_writes(archive, tmp_path):  # noqa: F811
    out = tmp_path / "out.zip"
    result = run(archive, out, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hop"]["summary"]["templates_pruned"] == []
    assert out.exists()


def test_an_existing_output_needs_force(archive, tmp_path):  # noqa: F811
    out = tmp_path / "out.zip"
    out.write_bytes(b"keep")
    assert run(archive, out).exit_code == 1
    assert out.read_bytes() == b"keep"
    assert run(archive, out, "--force").exit_code == 0
