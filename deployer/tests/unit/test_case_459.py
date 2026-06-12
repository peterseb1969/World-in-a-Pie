"""Regression coverage for CASE-459 — mutation-verb discovery is cwd-bound.

`_load_and_discover_for_mutation` resolved the WIP repo root by walking
up from cwd for ANY `.git`. Run from an app checkout, the walk
"succeeded" with the wrong repo, discovery came back empty (which is
`ok` — empty is not a discovery error), and validation emitted
misdirecting per-item "unknown component/app (known: [])" errors.

Contract under test:

  - Discovery-root resolution order: `--repo-root` flag > `repo_root`
    stamped in the deployer-state envelope > .git walk from cwd.
  - Stale envelope stamp (path no longer a directory) falls through to
    the cwd walk.
  - Zero components AND zero apps → loud exit 1 naming the searched
    path and the escape hatches, never the per-item validation spew.
  - `_persist_deployment` stamps `repo_root` into the envelope when
    given one, and carries an existing stamp forward when not (the
    mutation-verb persists pass None).
  - Pre-CASE-459 state files (no stamp) keep working from the WIP root.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import Result
from typer.testing import CliRunner

from wip_deploy.cli import (
    _load_state_repo_root,
    _persist_deployment,
    app,
)
from wip_deploy.spec.deployment import Deployment

runner = CliRunner()

WIP_ROOT = Path(__file__).resolve().parents[3]

_DEPLOYMENT_DICT = {
    "metadata": {"name": "test-install"},
    "spec": {
        "target": "compose",
        "apps": [{"name": "wip-kb", "enabled": True}],
        "modules": {"optional": []},
        "auth": {"mode": "api-key-only", "gateway": False, "users": []},
        "network": {"hostname": "localhost"},
        "images": {"tag_overrides": {"wip-kb": "sha-old"}},
        "platform": {"compose": {"data_dir": "/tmp/wip-test-data"}},
        "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
        "apply": {},
    },
}


def _write_deployment_state(
    install_dir: Path, repo_root: str | None = None
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "wip_deploy_format_version": 1,
        "deployment": _DEPLOYMENT_DICT,
    }
    if repo_root is not None:
        payload["repo_root"] = repo_root
    (install_dir / "deployment.deployer-state").write_text(
        json.dumps(payload, indent=2)
    )


def _foreign_git_repo(tmp_path: Path) -> Path:
    """A directory that looks like an app checkout: has .git, no manifests."""
    foreign = tmp_path / "some-app-repo"
    (foreign / ".git").mkdir(parents=True)
    return foreign


def _roll(extra: list[str]) -> Result:
    return runner.invoke(
        app, ["app-deploy", "wip-kb", "--tag", "sha-new", *extra]
    )


class TestWrongCwdFailsLoud:
    def test_foreign_git_repo_names_the_cause(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "install"
        _write_deployment_state(install_dir)  # pre-CASE-459: no stamp
        monkeypatch.chdir(_foreign_git_repo(tmp_path))

        result = _roll(["--install-dir", str(install_dir)])

        assert result.exit_code == 1
        assert "no components/apps" in result.output
        assert "not a World-in-a-Pie checkout" in result.output
        assert "--repo-root" in result.output
        # The pre-fix misdirection must be gone.
        assert "unknown app" not in result.output

    def test_no_git_anywhere_still_errors_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "install"
        _write_deployment_state(install_dir)
        bare = tmp_path / "no-git-here"
        bare.mkdir()
        monkeypatch.chdir(bare)

        result = _roll(["--install-dir", str(install_dir)])

        assert result.exit_code == 1
        assert "no .git directory found" in result.output


class TestDurableAnchors:
    def test_envelope_stamp_wins_over_foreign_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "install"
        _write_deployment_state(install_dir, repo_root=str(WIP_ROOT))
        monkeypatch.chdir(_foreign_git_repo(tmp_path))

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _roll(["--install-dir", str(install_dir)])

        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1
        assert mutate.call_args.kwargs["repo_root"] == WIP_ROOT

    def test_repo_root_flag_wins_over_foreign_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "install"
        _write_deployment_state(install_dir)  # no stamp
        monkeypatch.chdir(_foreign_git_repo(tmp_path))

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _roll(
                ["--install-dir", str(install_dir), "--repo-root", str(WIP_ROOT)]
            )

        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1

    def test_stale_stamp_falls_through_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "install"
        _write_deployment_state(
            install_dir, repo_root=str(tmp_path / "moved-away-checkout")
        )
        monkeypatch.chdir(WIP_ROOT)

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _roll(["--install-dir", str(install_dir)])

        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1

    def test_unstamped_state_from_wip_root_still_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-CASE-459 state files keep working from the WIP root —
        and the resolved root reaches the mutation lifecycle, so a
        successful mutation re-stamps (heals) the unstamped state."""
        install_dir = tmp_path / "install"
        _write_deployment_state(install_dir)
        monkeypatch.chdir(WIP_ROOT)

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _roll(["--install-dir", str(install_dir)])

        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1
        assert mutate.call_args.kwargs["repo_root"] == WIP_ROOT


class TestEnvelopeStamping:
    def test_persist_writes_stamp_when_given(self, tmp_path: Path) -> None:
        deployment = Deployment.model_validate(_DEPLOYMENT_DICT)
        _persist_deployment(deployment, tmp_path, repo_root=WIP_ROOT)

        payload = json.loads((tmp_path / "deployment.deployer-state").read_text())
        assert payload["repo_root"] == str(WIP_ROOT)
        assert _load_state_repo_root(tmp_path) == WIP_ROOT

    def test_persist_carries_existing_stamp_forward(self, tmp_path: Path) -> None:
        """Mutation-verb persists pass no root; the install-time stamp
        must survive the overwrite."""
        _write_deployment_state(tmp_path, repo_root=str(WIP_ROOT))
        deployment = Deployment.model_validate(_DEPLOYMENT_DICT)

        _persist_deployment(deployment, tmp_path)  # repo_root=None

        payload = json.loads((tmp_path / "deployment.deployer-state").read_text())
        assert payload["repo_root"] == str(WIP_ROOT)

    def test_persist_without_stamp_writes_none(self, tmp_path: Path) -> None:
        deployment = Deployment.model_validate(_DEPLOYMENT_DICT)
        _persist_deployment(deployment, tmp_path)

        payload = json.loads((tmp_path / "deployment.deployer-state").read_text())
        assert "repo_root" not in payload
        assert _load_state_repo_root(tmp_path) is None

    def test_load_stamp_tolerates_corrupt_state(self, tmp_path: Path) -> None:
        (tmp_path / "deployment.deployer-state").write_text("{not json")
        assert _load_state_repo_root(tmp_path) is None
