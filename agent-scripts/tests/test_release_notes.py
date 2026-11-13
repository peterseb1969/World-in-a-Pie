"""scripts/release-notes.py — release-note fragments, the shared-file gate, the release cut.

Each test builds a throwaway repo root with its own docs/releases/ and runs the
script's real entry point against it (--root), so check, preview and cut read
and write exactly what they would in the repository.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    """A clone whose develop holds the five libraries and every platform site, pushed."""
    la = rn._lib_artifacts()
    upstream = tmp_path / "upstream.git"
    git(tmp_path, "init", "-q", "--bare", str(upstream))
    root = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(upstream), str(root))
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    files = {}
    for pkg in la.PACKAGES.values():
        if pkg.kind == "npm":
            files[pkg.manifest] = json.dumps({"name": pkg.name, "version": "0.5.0"}, indent=2) + "\n"
        else:
            files[pkg.manifest] = f'[project]\nname = "{pkg.name}"\nversion = "0.5.0"\n'
    for path, _ in la.PLATFORM_SITES:
        files[path] = '__version__ = "2.1.0"\n' if path.endswith(".py") else '[project]\nversion = "2.1.0"\n'
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    (root / "libs/wip-client/src.ts").write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a client fix")
    git(root, "push", "-q", "origin", "HEAD:develop")
    return root


def test_cut_writes_the_release_and_removes_fragments(release_tree, capsys):
    root = release_tree
    frag(root, "case-1-a.md", GOOD)
    frag(root, "case-2-b.md", "---\nsection: new\npackages: [wip-archive, wip-deploy]\n---\n\n### A feature\n")
    readme = frag(root, "README.md", "# format\n")
    assert run(root, "cut", "2.2.0") == 0
    note = (root / "docs/releases/v2.2.0.md").read_text()
    assert note.startswith("# WIP v2.2.0\n")
    assert note.index("## New capabilities") < note.index("## Fixes")
    remaining = sorted(p.name for p in (root / "docs/releases/unreleased").iterdir())
    assert remaining == ["README.md"] and readme.exists()
    assert "packages the notes name: wip-archive, wip-client, wip-deploy" in capsys.readouterr().out


def test_cut_defines_the_version_of_every_component(release_tree, capsys):
    """Platform components take the release number; each library its computed version."""
    root = release_tree
    frag(root, "case-1-a.md", GOOD)
    assert run(root, "cut", "2.2.0") == 0
    la = rn._lib_artifacts()
    assert set(la.platform_versions(root).values()) == {"2.2.0"}
    assert json.loads((root / "libs/wip-client/package.json").read_text())["version"] == "0.5.1"
    assert 'version = "0.5.0"' in (root / "libs/wip-archive/pyproject.toml").read_text()
    out = capsys.readouterr().out
    assert "platform components 2.1.0 -> 2.2.0" in out and "wip-client 0.5.1" in out


def test_cut_refuses_library_content_not_on_develop_and_changes_nothing(release_tree, capsys):
    root = release_tree
    fragment = frag(root, "case-1-a.md", GOOD)
    (root / "libs/wip-react/src.ts").write_text("y\n")
    git(root, "add", "libs/wip-react/src.ts")
    git(root, "commit", "-q", "-m", "an unpushed react change")
    assert run(root, "cut", "2.2.0") == 1
    assert "wip-react 0.5.1+local." in capsys.readouterr().err
    assert fragment.exists() and not (root / "docs/releases/v2.2.0.md").exists()
    assert set(rn._lib_artifacts().platform_versions(root).values()) == {"2.1.0"}


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
