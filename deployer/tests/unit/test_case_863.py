"""Regression coverage for CASE-863 — `redeploy --tag` / `--image-tag`.

Rolling an existing install to a new image tag is a spec mutation on the
same load → mutate → `_apply_and_persist_mutation` skeleton app-deploy
uses: the flags update the SAVED spec (`spec.images.tag` /
`tag_overrides`) before the re-render, so the deployer-state and the
running install stay in agreement — no more hand-editing the state file.

Contract under test:

  - `--tag` lands in the loaded spec that gets persisted + applied.
  - An unchanged `--tag` prints the no-change marker and still redeploys.
  - `--image-tag NAME=TAG` upserts; sibling overrides are kept.
  - Empty `--tag` and malformed `--image-tag` exit 2 before any apply.
  - An `--image-tag` key matching no enabled component/app warns
    (CASE-556 text) but does not fail the redeploy.
  - A tag change combined with a service subset warns that out-of-scope
    services keep their old images until the next unscoped redeploy.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()


def _write_deployment_state(
    install_dir: Path, *, tag: str | None = None, overrides: dict | None = None
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    images: dict = {"tag_overrides": overrides or {}}
    if tag is not None:
        images["tag"] = tag
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "test-install"},
            "spec": {
                "target": "compose",
                "apps": [],
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "localhost"},
                "images": images,
                "platform": {"compose": {"data_dir": "/tmp/wip-test-data"}},
                "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
                "apply": {},
            },
        },
    }
    (install_dir / "deployment.deployer-state").write_text(json.dumps(payload, indent=2))


def _write_compose(install_dir: Path, services: list[str]) -> None:
    body = "services:\n" + "".join(
        f"  {s}:\n    image: scratch\n" for s in services
    )
    (install_dir / "docker-compose.yaml").write_text(body)


class TestRedeployTagRoll:
    def test_tag_lands_in_the_persisted_spec(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, tag="20261012a")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                ["redeploy", "--install-dir", str(tmp_path), "--tag", "20261013a"],
            )
        assert result.exit_code == 0, result.output
        deployment = mutate.call_args.args[0]
        assert deployment.spec.images.tag == "20261013a"
        # The label (the persisted mutation description) records the roll.
        assert "tag=20261013a" in mutate.call_args.args[4]

    def test_unchanged_tag_is_a_noop_mutation_but_still_redeploys(
        self, tmp_path: Path
    ) -> None:
        _write_deployment_state(tmp_path, tag="20261013a")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                ["redeploy", "--install-dir", str(tmp_path), "--tag", "20261013a"],
            )
        assert result.exit_code == 0, result.output
        assert "no change" in result.output
        # The verb's base contract — re-render from the saved spec — holds.
        assert mutate.call_count == 1
        assert "rolled" not in mutate.call_args.args[4]

    def test_image_tag_upserts_and_keeps_siblings(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path, tag="20261013a", overrides={"wip-kb": "20261012c"}
        )
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                [
                    "redeploy", "--install-dir", str(tmp_path),
                    "--image-tag", "registry=20261014-fix",
                ],
            )
        assert result.exit_code == 0, result.output
        deployment = mutate.call_args.args[0]
        assert deployment.spec.images.tag_overrides == {
            "wip-kb": "20261012c",
            "registry": "20261014-fix",
        }

    def test_empty_tag_exits_2(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path)
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app, ["redeploy", "--install-dir", str(tmp_path), "--tag", "  "],
            )
        assert result.exit_code == 2
        assert mutate.call_count == 0

    def test_malformed_image_tag_exits_2(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path)
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                ["redeploy", "--install-dir", str(tmp_path), "--image-tag", "noequals"],
            )
        assert result.exit_code == 2
        assert mutate.call_count == 0

    def test_unmatched_override_warns_but_proceeds(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path)
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                [
                    "redeploy", "--install-dir", str(tmp_path),
                    "--image-tag", "bogus-svc=20261013a",
                ],
            )
        assert result.exit_code == 0, result.output
        assert "matches no enabled component/app" in result.output
        assert mutate.call_count == 1

    def test_tag_change_with_service_scope_warns(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, tag="20261012a")
        _write_compose(tmp_path, ["registry", "def-store"])
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                [
                    "redeploy", "registry",
                    "--install-dir", str(tmp_path), "--tag", "20261013a",
                ],
            )
        assert result.exit_code == 0, result.output
        assert "tag change with a service subset" in result.output
        assert mutate.call_args.kwargs["services_scope"] == ["registry"]
