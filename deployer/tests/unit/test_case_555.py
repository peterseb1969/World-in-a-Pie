"""CASE-555 — repo-root discovery must not hard-depend on `.git`.

`find_repo_root` now recognizes the WIP repo by its shape (an ancestor
holding both `components/` and `deployer/`) with `.git` as a fallback,
so git-less trees (release tarballs, `git archive` exports, vendored
copies) resolve without `--repo-root` / `WIP_REPO_ROOT`. The failure
message names both overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.discovery import find_repo_root


def _make_shape(root: Path) -> None:
    (root / "components").mkdir()
    (root / "deployer").mkdir()


class TestGitlessTree:
    def test_shape_resolves_without_git(self, tmp_path: Path) -> None:
        # A tarball/git-archive export: shape dirs, no .git anywhere.
        _make_shape(tmp_path)
        assert find_repo_root(tmp_path) == tmp_path.resolve()

    def test_resolves_from_nested_start(self, tmp_path: Path) -> None:
        _make_shape(tmp_path)
        nested = tmp_path / "deployer" / "src" / "wip_deploy"
        nested.mkdir(parents=True)
        assert find_repo_root(nested) == tmp_path.resolve()

    def test_shape_requires_both_dirs(self, tmp_path: Path) -> None:
        # components/ alone is not the WIP shape — and with no .git
        # either, resolution fails.
        (tmp_path / "components").mkdir()
        with pytest.raises(FileNotFoundError):
            find_repo_root(tmp_path)

    def test_shape_dirs_must_be_directories(self, tmp_path: Path) -> None:
        # Plain files named like the markers don't count.
        (tmp_path / "components").touch()
        (tmp_path / "deployer").touch()
        with pytest.raises(FileNotFoundError):
            find_repo_root(tmp_path)


class TestGitFallback:
    def test_git_only_tree_still_resolves(self, tmp_path: Path) -> None:
        # A clone without the shape dirs (pre-shape behavior preserved).
        (tmp_path / ".git").mkdir()
        assert find_repo_root(tmp_path) == tmp_path.resolve()

    def test_shape_outranks_nested_git(self, tmp_path: Path) -> None:
        # An unrelated git checkout nested inside the WIP tree must not
        # shadow the repo root: the shape pass completes first.
        _make_shape(tmp_path)
        nested_clone = tmp_path / "apps" / "some-app"
        nested_clone.mkdir(parents=True)
        (nested_clone / ".git").mkdir()
        assert find_repo_root(nested_clone) == tmp_path.resolve()


class TestErrorMessage:
    def test_error_names_the_overrides(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError) as exc_info:
            find_repo_root(tmp_path)
        message = str(exc_info.value)
        assert "--repo-root" in message
        assert "WIP_REPO_ROOT" in message
        assert "components/ + deployer/" in message
