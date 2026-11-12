"""scripts/release-notes.py — release-note fragments, the shared-file gate, the release cut.

Each test builds a throwaway repo root with its own docs/releases/ and runs the
script's real entry point against it (--root), so check, preview and cut read
and write exactly what they would in the repository.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release-notes.py"

_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
rn = importlib.util.module_from_spec(_spec)
sys.modules["release_notes"] = rn
_spec.loader.exec_module(rn)


def frag(root: Path, name: str, text: str) -> Path:
    path = root / "docs/releases/unreleased" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(root: Path, *args: str) -> int:
    return rn.main(["--root", str(root), *args])


GOOD = "---\nsection: fixes\ncases: [1260]\npackages: [wip-client]\n---\n\n### A fix\n\nBody.\n"


def test_valid_fragments_pass(tmp_path, capsys):
    frag(tmp_path, "case-1-a.md", GOOD)
    frag(tmp_path, "README.md", "# not a fragment\n")
    assert run(tmp_path, "check") == 0
    assert "1 fragment(s) valid" in capsys.readouterr().out


def test_no_fragment_dir_is_valid(tmp_path):
    assert run(tmp_path, "check") == 0


def test_recreated_shared_file_is_refused(tmp_path, capsys):
    """The gate: a lane that edits or recreates unreleased.md fails CI."""
    (tmp_path / "docs/releases").mkdir(parents=True)
    (tmp_path / "docs/releases/unreleased.md").write_text("# WIP — unreleased\n")
    assert run(tmp_path, "check") == 1
    assert "unreleased.md exists" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text, problem",
    [
        ("### no frontmatter\n", "no frontmatter"),
        ("---\nsection: fixes\n\n### x\n", "not closed"),
        ("---\nsection: misc\n---\n\n### x\n", "section 'misc'"),
        ("---\n---\n\n### x\n", "section ''"),
        ("---\nsection: fixes\nowner: me\n---\n\n### x\n", "unknown frontmatter"),
        ("---\nsection: fixes\npackages: [left-pad]\n---\n\n### x\n", "package 'left-pad'"),
        ("---\nsection: fixes\ncases: 12\n---\n\n### x\n", "cases: expected a [list]"),
        ("---\nsection: fixes\n---\n\n", "body is empty"),
        ("---\nsection: fixes\n---\n\n## Fixes\n\n### x\n", "may not carry # or ##"),
    ],
)
def test_malformed_fragment_is_refused(tmp_path, capsys, text, problem):
    frag(tmp_path, "case-1-bad.md", text)
    assert run(tmp_path, "check") == 1
    assert problem in capsys.readouterr().err


def test_non_markdown_file_is_refused(tmp_path, capsys):
    frag(tmp_path, "case-1-a.txt", GOOD)
    assert run(tmp_path, "check") == 1
    assert "not a .md file" in capsys.readouterr().err


def test_headings_inside_code_fences_are_allowed(tmp_path):
    frag(tmp_path, "case-1-a.md", "---\nsection: fixes\n---\n\n### x\n\n```bash\n# a comment\n```\n")
    assert run(tmp_path, "check") == 0


def test_case_prefix_is_accepted(tmp_path):
    path = frag(tmp_path, "case-1-a.md", "---\nsection: new\ncases: [CASE-12, 13]\n---\n\n### x\n")
    parsed, errors = rn.parse(path)
    assert errors == [] and parsed.cases == [12, 13]


def test_preview_orders_sections_then_file_names(tmp_path, capsys):
    frag(tmp_path, "b.md", "---\nsection: fixes\n---\n\n### second fix\n")
    frag(tmp_path, "a.md", "---\nsection: fixes\n---\n\n### first fix\n")
    frag(tmp_path, "c.md", "---\nsection: action-required\n---\n\n### breaking\n")
    assert run(tmp_path, "preview") == 0
    out = capsys.readouterr().out
    order = [
        out.index(s) for s in ("## Action required", "### breaking", "## Fixes", "### first fix", "### second fix")
    ]
    assert order == sorted(order)
    assert "## New capabilities" not in out  # empty sections are left out


def test_preview_refuses_invalid_fragments(tmp_path):
    frag(tmp_path, "a.md", "### no frontmatter\n")
    assert run(tmp_path, "preview") == 1


def test_cut_writes_the_release_and_removes_fragments(tmp_path, capsys):
    frag(tmp_path, "case-1-a.md", GOOD)
    frag(tmp_path, "case-2-b.md", "---\nsection: new\npackages: [wip-archive]\n---\n\n### A feature\n")
    readme = frag(tmp_path, "README.md", "# format\n")
    assert run(tmp_path, "cut", "2.2.0") == 0
    note = (tmp_path / "docs/releases/v2.2.0.md").read_text()
    assert note.startswith("# WIP v2.2.0\n")
    assert note.index("## New capabilities") < note.index("## Fixes")
    remaining = sorted(p.name for p in (tmp_path / "docs/releases/unreleased").iterdir())
    assert remaining == ["README.md"] and readme.exists()
    assert "packages changed: wip-archive, wip-client" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["2.2", "v2.2.0", "2.2.0-rc1"])
def test_cut_refuses_a_malformed_version(tmp_path, version):
    frag(tmp_path, "case-1-a.md", GOOD)
    assert run(tmp_path, "cut", version) == 1
    assert (tmp_path / "docs/releases/unreleased/case-1-a.md").exists()


def test_cut_refuses_an_existing_release_and_keeps_fragments(tmp_path):
    frag(tmp_path, "case-1-a.md", GOOD)
    (tmp_path / "docs/releases/v2.2.0.md").write_text("released\n")
    assert run(tmp_path, "cut", "2.2.0") == 1
    assert (tmp_path / "docs/releases/v2.2.0.md").read_text() == "released\n"
    assert (tmp_path / "docs/releases/unreleased/case-1-a.md").exists()


def test_cut_refuses_with_nothing_to_release(tmp_path):
    (tmp_path / "docs/releases/unreleased").mkdir(parents=True)
    assert run(tmp_path, "cut", "2.2.0") == 1


def test_repository_fragments_are_valid():
    """The fragments committed in this repository pass the CI check."""
    assert rn.main(["check"]) == 0
