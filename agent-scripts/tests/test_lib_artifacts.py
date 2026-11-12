"""scripts/lib_artifacts.py — computed library versions and the release freeze.

Every case runs against a throwaway repository with a bare "upstream", so the
git paths the scaffold depends on (the anchor commit, the commit count, the
dirty and unpushed checks) run for real. Building is exercised by the golden
suite, which runs the whole scaffold.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "lib_artifacts.py"

_spec = importlib.util.spec_from_file_location("lib_artifacts", SCRIPT)
la = importlib.util.module_from_spec(_spec)
sys.modules["lib_artifacts"] = la
_spec.loader.exec_module(la)

CLIENT = la.PACKAGES["wip-client"]
ARCHIVE = la.PACKAGES["wip-archive"]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def commit(root: Path, message: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def npm_manifest(version: str) -> str:
    return json.dumps({"name": "@wip/client", "version": version, "files": ["dist"]}, indent=2) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    upstream = tmp_path / "upstream.git"
    git(tmp_path, "init", "-q", "--bare", str(upstream))
    root = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(upstream), str(root))
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    commit(
        root,
        "libs at their base versions",
        {
            "libs/wip-client/package.json": npm_manifest("0.108.0"),
            "libs/wip-client/src/index.ts": "export const a = 1;\n",
            "libs/wip-archive/pyproject.toml": '[project]\nname = "wip-archive"\nversion = "0.39.0"\n',
            "README.md": "root\n",
        },
    )
    git(root, "push", "-q", "origin", "HEAD:main")
    return root


def push(root: Path) -> None:
    git(root, "push", "-q", "origin", "HEAD:main")


def test_base_version_when_nothing_changed(repo):
    assert la.compute_version(repo, CLIENT) == "0.108.0"
    assert la.compute_version(repo, ARCHIVE) == "0.39.0"


def test_each_commit_to_the_package_moves_its_patch(repo):
    commit(repo, "c1", {"libs/wip-client/src/index.ts": "export const a = 2;\n"})
    commit(repo, "c2", {"libs/wip-client/src/index.ts": "export const a = 3;\n"})
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.108.2"


def test_commits_elsewhere_do_not_move_it(repo):
    commit(repo, "root only", {"README.md": "changed\n"})
    commit(repo, "archive only", {"libs/wip-archive/src.py": "x = 1\n"})
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.108.0"
    assert la.compute_version(repo, ARCHIVE) == "0.39.1"


def test_a_manifest_edit_that_keeps_the_version_still_counts(repo):
    manifest = json.loads(npm_manifest("0.108.0"))
    manifest["description"] = "typed client"
    commit(repo, "describe", {"libs/wip-client/package.json": json.dumps(manifest, indent=2) + "\n"})
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.108.1"


def test_writing_a_new_base_resets_the_count(repo):
    commit(repo, "c1", {"libs/wip-client/src/index.ts": "export const a = 2;\n"})
    commit(repo, "breaking", {"libs/wip-client/package.json": npm_manifest("0.109.0")})
    commit(repo, "c2", {"libs/wip-client/src/index.ts": "export const a = 3;\n"})
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.109.1"


def test_uncommitted_content_gets_a_dirty_suffix(repo):
    (repo / "libs/wip-client/src/index.ts").write_text("export const a = 9;\n")
    first = la.compute_version(repo, CLIENT)
    assert first.startswith("0.108.0+dirty.")
    (repo / "libs/wip-client/src/index.ts").write_text("export const a = 10;\n")
    assert la.compute_version(repo, CLIENT) not in (first, "0.108.0")


def test_an_untracked_file_is_dirty_too(repo):
    (repo / "libs/wip-client/src/new.ts").write_text("export {};\n")
    assert "+dirty." in la.compute_version(repo, CLIENT)


def test_ignored_files_are_not_dirty(repo):
    commit(repo, "ignore dist", {"libs/wip-client/.gitignore": "dist/\n"})
    push(repo)
    (repo / "libs/wip-client/dist").mkdir()
    (repo / "libs/wip-client/dist/index.js").write_text("built\n")
    assert la.compute_version(repo, CLIENT) == "0.108.1"


def test_unpushed_commits_get_a_local_suffix(repo):
    """Pushed history may later give the same number to different content."""
    commit(repo, "c1", {"libs/wip-client/src/index.ts": "export const a = 2;\n"})
    version = la.compute_version(repo, CLIENT)
    tree = git(repo, "rev-parse", "HEAD:libs/wip-client")[:8]
    assert version == f"0.108.1+local.{tree}"
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.108.1"


def test_unpushed_commits_elsewhere_leave_it_plain(repo):
    commit(repo, "root only", {"README.md": "changed\n"})
    assert la.compute_version(repo, CLIENT) == "0.108.0"


def test_a_shallow_clone_is_refused(repo, tmp_path):
    commit(repo, "c1", {"libs/wip-client/src/index.ts": "export const a = 2;\n"})
    push(repo)
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{tmp_path / 'upstream.git'}", str(shallow))
    with pytest.raises(la.LibError, match="shallow"):
        la.compute_version(shallow, CLIENT)


def test_a_base_that_is_not_x_y_z_is_refused(repo):
    commit(repo, "pre", {"libs/wip-client/package.json": npm_manifest("0.108.0-beta")})
    with pytest.raises(la.LibError, match=r"not X\.Y\.Z"):
        la.compute_version(repo, CLIENT)


def test_freeze_writes_the_computed_version_as_the_base(repo):
    commit(repo, "c1", {"libs/wip-client/src/index.ts": "export const a = 2;\n"})
    commit(repo, "c2", {"libs/wip-client/src/index.ts": "export const a = 3;\n"})
    push(repo)
    assert la.freeze(repo, ["wip-client"]) == ["wip-client 0.108.2"]
    assert json.loads((repo / CLIENT.manifest).read_text())["version"] == "0.108.2"
    git(repo, "commit", "-q", "-am", "release")
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.108.2"  # the release keeps its number
    commit(repo, "c3", {"libs/wip-client/src/index.ts": "export const a = 4;\n"})
    push(repo)
    assert la.compute_version(repo, CLIENT) == "0.108.3"


def test_freeze_refuses_unpushed_content(repo):
    commit(repo, "c1", {"libs/wip-client/src/index.ts": "export const a = 2;\n"})
    with pytest.raises(la.LibError, match="not pushed"):
        la.freeze(repo, ["wip-client"])


def test_set_version_rewrites_only_the_version_line():
    text = '[project]\nname = "wip-archive"\nversion = "0.39.0"\n# version = "old"\n'
    assert la._set_version(text, ARCHIVE, "0.39.7") == text.replace('"0.39.0"', '"0.39.7"')


def test_cli_rejects_an_unknown_package(repo, capsys):
    with pytest.raises(SystemExit):
        la.main(["--root", str(repo), "version", "left-pad"])
    assert "unknown package" in capsys.readouterr().err


def test_cli_prints_versions(repo, capsys):
    assert la.main(["--root", str(repo), "version", "wip-client"]) == 0
    assert capsys.readouterr().out == "wip-client 0.108.0\n"
