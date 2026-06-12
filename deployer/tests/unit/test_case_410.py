"""Regression coverage for CASE-410 — `app-deploy` tag-roll verb.

The APP-YAC self-deploy last mile: the app repo's CI builds + pushes a
sha-tagged image; `wip-deploy app-deploy <name> --tag <tag>` points an
existing install at it via `spec.images.tag_overrides` (CASE-438
precedence) and applies scoped to that one app (CASE-443).

Contract under test:

  - Missing install state → clean error (shared loader path).
  - App not enabled (absent or disabled) → exit 2 with the enabled list.
  - Empty/whitespace --tag → exit 2.
  - Same-tag re-roll → friendly no-op, no render/apply lifecycle.
  - Happy path → tag_overrides mutated on the deployment passed to the
    mutation lifecycle, services_scope=[app].
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()


def _write_deployment_state(
    install_dir: Path,
    apps: list[dict],
    tag_overrides: dict[str, str] | None = None,
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "test-install"},
            "spec": {
                "target": "compose",
                "apps": apps,
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "localhost"},
                "images": {"tag_overrides": tag_overrides or {}},
                "platform": {"compose": {"data_dir": "/tmp/wip-test-data"}},
                "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
                "apply": {},
            },
        },
    }
    (install_dir / "deployment.deployer-state").write_text(json.dumps(payload, indent=2))


class TestAppDeployErrors:
    def test_missing_install_state(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["app-deploy", "wip-kb", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_app_not_in_install(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path, apps=[{"name": "react-console", "enabled": True}]
        )
        result = runner.invoke(
            app,
            ["app-deploy", "wip-kb", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "not enabled" in result.output
        assert "react-console" in result.output

    def test_app_disabled_rejected(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path, apps=[{"name": "wip-kb", "enabled": False}]
        )
        result = runner.invoke(
            app,
            ["app-deploy", "wip-kb", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "not enabled" in result.output

    def test_empty_tag_rejected(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path, apps=[{"name": "wip-kb", "enabled": True}]
        )
        result = runner.invoke(
            app,
            ["app-deploy", "wip-kb", "--tag", "  ", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "non-empty" in result.output


class TestAppDeployNoop:
    def test_same_tag_is_noop(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path,
            apps=[{"name": "wip-kb", "enabled": True}],
            tag_overrides={"wip-kb": "sha-abc"},
        )
        # No mutation-lifecycle patch — the no-op must short-circuit
        # before the render+apply path (which would crash here).
        result = runner.invoke(
            app,
            ["app-deploy", "wip-kb", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "no change" in result.output


class TestAppDeployHappyPath:
    def test_mutates_override_and_scopes_apply(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path,
            apps=[{"name": "wip-kb", "enabled": True}],
            tag_overrides={"wip-kb": "sha-old"},
        )
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                [
                    "app-deploy", "wip-kb",
                    "--tag", "sha-new",
                    "--install-dir", str(tmp_path),
                ],
            )
        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1
        deployment = mutate.call_args.args[0]
        assert deployment.spec.images.tag_overrides["wip-kb"] == "sha-new"
        assert mutate.call_args.kwargs["services_scope"] == ["wip-kb"]
