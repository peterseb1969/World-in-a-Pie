"""CASE-962 — install from the wrong root, a registry URL, a silent hostname.

Three defects of one k8s install:
  - `install` (and every fresh-spec verb) had no empty-discovery guard: run
    from another git repository, discovery came back empty and validation
    reported every preset module as an unknown component (known: []).
    The guard the additive verbs had is now one helper both paths share.
  - `--registry https://ghcr.io/x` was taken verbatim and would have made
    every image reference invalid. A scheme, trailing slash or spaces are
    refused with the value to use.
  - `install --target compose|k8s` without --hostname silently used the
    placeholder `wip.local` for the Ingress host, OIDC issuer and TLS name.
    It is now refused; dev keeps `localhost`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from wip_deploy.cli import app
from wip_deploy.spec.deployment import ImagesSpec

runner = CliRunner()
WIP_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def other_repo(tmp_path: Path) -> Path:
    """A git repository that is not a WIP checkout."""
    repo = tmp_path / "FR-YAC"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _not_a_checkout(result, root: Path) -> None:
    assert result.exit_code == 1, result.output
    assert "not a World-in-a-Pie checkout" in result.output
    assert str(root) in result.output
    assert "unknown component" not in result.output


class TestEmptyDiscovery:
    def test_install_names_the_wrong_root(self, other_repo: Path, tmp_path: Path) -> None:
        result = runner.invoke(app, [
            "install", "--target", "k8s", "--name", "kb-work",
            "--hostname", "kb-work.internal", "--registry", "ghcr.io/x",
            "--repo-root", str(other_repo), "--install-dir", str(tmp_path / "inst"),
        ])
        _not_a_checkout(result, other_repo)

    def test_validate_names_the_wrong_root(self, other_repo: Path) -> None:
        result = runner.invoke(app, [
            "validate", "--target", "compose", "--repo-root", str(other_repo),
        ])
        _not_a_checkout(result, other_repo)


class TestRegistry:
    @pytest.mark.parametrize(
        ("given", "use"),
        [
            ("https://ghcr.io/peterseb1969", "ghcr.io/peterseb1969"),
            ("http://localhost:5000/x", "localhost:5000/x"),
            ("ghcr.io/x/", "ghcr.io/x"),
            (" ghcr.io/x", "ghcr.io/x"),
        ],
    )
    def test_not_an_image_prefix_is_refused_with_the_value_to_use(
        self, given: str, use: str
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            ImagesSpec(registry=given)
        assert repr(use) in str(refused.value)

    @pytest.mark.parametrize("ok", ["ghcr.io/peterseb1969", "localhost:5000/x", None])
    def test_image_prefixes_are_accepted(self, ok: str | None) -> None:
        assert ImagesSpec(registry=ok).registry == ok

    def test_install_refuses_a_url(self, tmp_path: Path) -> None:
        result = runner.invoke(app, [
            "install", "--target", "k8s", "--name", "kb-work",
            "--hostname", "kb-work.internal",
            "--registry", "https://ghcr.io/peterseb1969",
            "--repo-root", str(WIP_ROOT), "--install-dir", str(tmp_path / "inst"),
        ])
        assert result.exit_code != 0
        assert "'ghcr.io/peterseb1969'" in result.output


class TestHostname:
    @pytest.mark.parametrize("target", ["compose", "k8s"])
    def test_install_without_hostname_is_refused(self, target: str, tmp_path: Path) -> None:
        result = runner.invoke(app, [
            "install", "--target", target, "--name", "fresh",
            "--registry", "ghcr.io/x",
            "--repo-root", str(WIP_ROOT), "--install-dir", str(tmp_path / "inst"),
        ])
        assert result.exit_code == 2, result.output
        assert "--hostname is required" in result.output
        assert not (tmp_path / "inst").exists()

    def test_reconcile_without_hostname_is_refused(self, tmp_path: Path) -> None:
        # --reconcile silences the existing-install guard, so without this a
        # reconcile would quietly move a live install to the placeholder.
        from tests.unit.test_install_existing_guard import _make_deployment, _persist

        _persist(tmp_path, _make_deployment(hostname="cloud.example.com"))
        result = runner.invoke(app, [
            "install", "--name", "guard-test", "--install-dir", str(tmp_path),
            "--reconcile", "--registry", "ghcr.io/x",
        ])
        assert result.exit_code == 2, result.output
        assert "--hostname is required" in result.output

    def test_dev_keeps_its_default(self, other_repo: Path, tmp_path: Path) -> None:
        # No --hostname on dev passes the hostname check; the empty root
        # then stops it, which proves the check let it through.
        result = runner.invoke(app, [
            "install", "--target", "dev", "--name", "fresh",
            "--repo-root", str(other_repo), "--install-dir", str(tmp_path / "inst"),
        ])
        assert "--hostname is required" not in result.output
        _not_a_checkout(result, other_repo)

    def test_validate_still_previews_with_the_placeholder(self) -> None:
        result = runner.invoke(app, [
            "validate", "--target", "compose", "--repo-root", str(WIP_ROOT),
        ])
        assert "--hostname is required" not in result.output
