"""Tests for discovery.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.discovery import discover, find_repo_root

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


# ────────────────────────────────────────────────────────────────────
# find_repo_root
# ────────────────────────────────────────────────────────────────────


class TestFindRepoRoot:
    def test_from_deployer_src_finds_repo_root(self) -> None:
        # Find repo root starting from a known deep path.
        start = REPO_ROOT / "deployer" / "src" / "wip_deploy"
        assert start.exists()
        root = find_repo_root(start)
        assert root == REPO_ROOT

    def test_no_git_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            find_repo_root(tmp_path)


# ────────────────────────────────────────────────────────────────────
# discover — against real repo
# ────────────────────────────────────────────────────────────────────


class TestDiscoverReal:
    def test_discovers_all_real_manifests(self) -> None:
        result = discover(REPO_ROOT)
        assert result.ok, f"unexpected errors: {result.errors}"
        names = {c.metadata.name for c in result.components}
        assert "registry" in names
        assert "mongodb" in names
        assert "auth-gateway" in names
        assert "router" in names
        assert len(result.components) >= 14

        app_names = {a.metadata.name for a in result.apps}
        assert app_names == {"dnd", "clintrial", "react-console"}

    def test_discovers_infrastructure_components(self) -> None:
        result = discover(REPO_ROOT)
        infra = {
            c.metadata.name
            for c in result.components
            if c.metadata.category == "infrastructure"
        }
        # minio is category=optional (toggled via --add minio), not infra.
        assert {"mongodb", "postgres", "nats", "dex", "auth-gateway"} <= infra


# ────────────────────────────────────────────────────────────────────
# discover — synthetic fixtures (error reporting)
# ────────────────────────────────────────────────────────────────────


class TestDiscoverErrorHandling:
    def _make_repo(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        return tmp_path

    def test_empty_repo(self, tmp_path: Path) -> None:
        root = self._make_repo(tmp_path)
        result = discover(root)
        assert result.ok
        assert result.components == []
        assert result.apps == []

    def test_bad_yaml_collected_not_raised(self, tmp_path: Path) -> None:
        root = self._make_repo(tmp_path)
        bad = root / "components" / "broken" / "wip-component.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_text("not: yaml: [unclosed")

        result = discover(root)
        assert not result.ok
        assert len(result.errors) == 1
        assert "YAML parse" in str(result.errors[0])

    def test_schema_violation_collected(self, tmp_path: Path) -> None:
        root = self._make_repo(tmp_path)
        bad = root / "components" / "foo" / "wip-component.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_text(
            "api_version: wip.dev/v1\n"
            "kind: Component\n"
            "metadata:\n"
            "  name: foo\n"
            "  category: not-a-real-category\n"
            "  description: broken\n"
            "spec:\n"
            "  image: {name: wip-foo}\n"
        )

        result = discover(root)
        assert not result.ok
        assert len(result.errors) == 1
        assert "spec validation" in str(result.errors[0])

    def test_non_mapping_yaml_collected(self, tmp_path: Path) -> None:
        root = self._make_repo(tmp_path)
        bad = root / "components" / "foo" / "wip-component.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_text("- just\n- a\n- list\n")

        result = discover(root)
        assert not result.ok
        assert "YAML mapping" in str(result.errors[0])

    def test_partial_failure_returns_good_and_bad(self, tmp_path: Path) -> None:
        """A broken manifest must not suppress siblings."""
        root = self._make_repo(tmp_path)

        good = root / "components" / "a" / "wip-component.yaml"
        good.parent.mkdir(parents=True)
        good.write_text(
            "api_version: wip.dev/v1\n"
            "kind: Component\n"
            "metadata:\n"
            "  name: a\n"
            "  category: core\n"
            "  description: good one\n"
            "spec:\n"
            "  image: {name: wip-a}\n"
        )

        bad = root / "components" / "b" / "wip-component.yaml"
        bad.parent.mkdir(parents=True)
        bad.write_text("not: yaml: [broken")

        result = discover(root)
        assert len(result.components) == 1
        assert result.components[0].metadata.name == "a"
        assert len(result.errors) == 1
