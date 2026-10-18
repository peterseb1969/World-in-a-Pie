"""`add-app --tag` sets the app's image-tag override in the same mutation.

App images are built on their own cadence, so the install's base tag
routinely does not exist for an app being added; without a tag option the
add rendered the app at `spec.images.tag`, the pod sat in
ImagePullBackOff for the whole health wait, and a second verb had to pin
the real tag afterwards. Now the override lands in the SAME mutation that
enables the app — one render, one apply. `remove-app` drops the override
with the AppRef, so a removed app cannot leave dead state that warns on
every later render or silently re-pin a returning app to a stale tag.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()


def _write_state(
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
    (install_dir / "deployment.deployer-state").write_text(json.dumps(payload))


def _invoke_add(install_dir: Path, *args: str):
    """Run add-app with the apply/persist step captured instead of run."""
    with patch("wip_deploy.cli._apply_and_persist_mutation") as apply_mut:
        result = runner.invoke(
            app,
            ["add-app", *args, "--install-dir", str(install_dir)],
        )
    return result, apply_mut


class TestAddAppTag:
    def test_tag_lands_in_the_same_mutation(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=[])
        result, apply_mut = _invoke_add(tmp_path, "react-console", "--tag", "20261018b")
        assert result.exit_code == 0, result.output
        assert apply_mut.call_count == 1
        deployment = apply_mut.call_args.args[0]
        assert deployment.spec.images.tag_overrides["react-console"] == "20261018b"
        ref = next(a for a in deployment.spec.apps if a.name == "react-console")
        assert ref.enabled

    def test_empty_tag_rejected(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=[])
        result, apply_mut = _invoke_add(tmp_path, "react-console", "--tag", "  ")
        assert result.exit_code == 2
        assert "non-empty image tag" in result.output
        assert apply_mut.call_count == 0

    def test_tag_on_enabled_app_is_a_real_mutation(self, tmp_path: Path) -> None:
        # Without --tag this is the friendly no-op; with it, the verb
        # behaves like app-deploy (update the override, apply scoped).
        _write_state(
            tmp_path,
            apps=[{"name": "react-console", "enabled": True}],
            tag_overrides={"react-console": "20261012a"},
        )
        result, apply_mut = _invoke_add(tmp_path, "react-console", "--tag", "20261018b")
        assert result.exit_code == 0, result.output
        assert apply_mut.call_count == 1
        deployment = apply_mut.call_args.args[0]
        assert deployment.spec.images.tag_overrides["react-console"] == "20261018b"

    def test_without_tag_enabled_app_stays_a_noop(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=[{"name": "react-console", "enabled": True}])
        result, apply_mut = _invoke_add(tmp_path, "react-console")
        assert result.exit_code == 0
        assert "already enabled" in result.output
        assert apply_mut.call_count == 0


class TestRemoveAppDropsOverride:
    def test_override_removed_with_the_app(self, tmp_path: Path) -> None:
        _write_state(
            tmp_path,
            apps=[{"name": "react-console", "enabled": True}],
            tag_overrides={"react-console": "20261018b", "wip-kb": "20261014c"},
        )
        with (
            patch("wip_deploy.cli._apply_and_persist_mutation") as apply_mut,
            patch("wip_deploy.apply.stop_and_remove_container"),
        ):
            result = runner.invoke(
                app,
                ["remove-app", "react-console", "--install-dir", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output
        deployment = apply_mut.call_args.args[0]
        # The removed app's override is gone; others are untouched.
        assert "react-console" not in deployment.spec.images.tag_overrides
        assert deployment.spec.images.tag_overrides["wip-kb"] == "20261014c"
