"""CASE-881 — a dev install's source mounts follow the resolved repo root, not the cwd.

The dev target bind-mounts its backend source from a repo root. That root
used to come from a fresh `.git` walk from the cwd inside `_render_tree`,
while discovery and the stamped deployer-state used `--repo-root` / the
stamped root. One redeploy therefore used two roots: run from another clone,
it re-bound a running dev install to that clone's source while the state kept
recording the old one. An explicit `--repo-root` did not reach the mounts at
all (render, install, and every mutation verb).

The contract: `_render_tree` takes the root its caller resolved and never
looks one up; every caller passes the root it discovered from; an explicit
`--repo-root` that moves an install away from its stamped root is announced.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from wip_deploy.cli import _render_tree, app

runner = CliRunner()

WIP_ROOT = Path(__file__).resolve().parents[3]

_DEV_DEPLOYMENT = {
    "metadata": {"name": "test-dev"},
    "spec": {
        "target": "dev",
        "apps": [],
        "modules": {"optional": []},
        "auth": {"mode": "api-key-only", "gateway": False, "users": []},
        "network": {"hostname": "localhost"},
        "platform": {"dev": {}},
        "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
        "apply": {},
    },
}


def _write_state(install_dir: Path, repo_root: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "deployment.deployer-state").write_text(json.dumps({
        "wip_deploy_format_version": 1,
        "deployment": _DEV_DEPLOYMENT,
        "repo_root": str(repo_root),
    }))


def _other_checkout(tmp_path: Path) -> Path:
    """Another git checkout to run the command from (the incident's cwd)."""
    other = tmp_path / "other-clone"
    (other / ".git").mkdir(parents=True)
    return other


def _dev_deployment() -> SimpleNamespace:
    return SimpleNamespace(
        spec=SimpleNamespace(target="dev", platform=SimpleNamespace(
            dev=SimpleNamespace(mode="simple"))),
    )


class _Stop(ValueError):
    """Raised by the patched renderer: the callers turn a render ValueError
    into a clean abort, so the command stops before anything is applied."""


class TestRenderTree:
    def test_dev_mounts_come_from_the_given_root_not_the_cwd(self, tmp_path: Path):
        given = tmp_path / "given-root"
        with (
            patch("wip_deploy.cli.find_repo_root", side_effect=AssertionError(
                "_render_tree must not look the root up itself")),
            patch("wip_deploy.cli.render_dev_simple", return_value="tree") as dev,
        ):
            assert _render_tree(_dev_deployment(), [], [], None, repo_root=given) == "tree"
        assert dev.call_args.kwargs["repo_root"] == given

    def test_dev_without_a_root_fails_naming_the_flag(self, capsys):
        with pytest.raises(typer.Exit) as exc:
            _render_tree(_dev_deployment(), [], [], None, repo_root=None)
        assert exc.value.exit_code == 1
        assert "--repo-root" in capsys.readouterr().err


class TestCallersPassTheResolvedRoot:
    def test_redeploy_from_another_clone_renders_from_the_stamped_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        install_dir = tmp_path / "install"
        _write_state(install_dir, WIP_ROOT)
        monkeypatch.chdir(_other_checkout(tmp_path))

        with (
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._render_tree", side_effect=_Stop("stop")) as render,
        ):
            result = runner.invoke(app, ["redeploy", "--install-dir", str(install_dir)])

        assert render.call_count == 1, result.output
        assert render.call_args.kwargs["repo_root"] == WIP_ROOT
        assert "note: --repo-root" not in result.output

    def test_render_with_repo_root_renders_from_the_flag_not_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(_other_checkout(tmp_path))

        with (
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._render_tree", side_effect=_Stop("stop")) as render,
        ):
            result = runner.invoke(app, [
                "render", "--target", "dev", "--repo-root", str(WIP_ROOT),
                "--output-dir", str(tmp_path / "out"),
            ])

        assert render.call_count == 1, result.output
        assert render.call_args.kwargs["repo_root"] == WIP_ROOT

    def test_an_explicit_root_that_moves_the_install_is_announced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        install_dir = tmp_path / "install"
        stamped = tmp_path / "stamped-clone"
        stamped.mkdir()
        _write_state(install_dir, stamped)
        monkeypatch.chdir(tmp_path)

        with (
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._render_tree", side_effect=_Stop("stop")) as render,
        ):
            result = runner.invoke(app, [
                "redeploy", "--install-dir", str(install_dir),
                "--repo-root", str(WIP_ROOT),
            ])

        assert "note: --repo-root" in result.output
        assert "dev source mounts" in result.output
        assert str(stamped) in result.output
        assert render.call_args.kwargs["repo_root"] == WIP_ROOT
