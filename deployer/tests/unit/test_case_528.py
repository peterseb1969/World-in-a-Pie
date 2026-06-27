"""Regression coverage for CASE-528 — the `redeploy` verb.

The missing middle between `rebuild` (recreate, no re-render) and
`install` (full re-render from CLI flags). `redeploy` reuses the
install's persisted `deployment.deployer-state` spec, re-renders it (so
deployer renderer/spec changes like CASE-523's WATCHFILES_FORCE_POLLING
take effect), and `compose up -d` recreates only the services whose
rendered config actually changed.

Contract under test:

  - Missing install state -> clean error (shared loader path).
  - No service args -> full re-up, services_scope=None.
  - Service subset -> services_scope=[services] forwarded to the apply.
  - Unknown service name (validated against the rendered compose) -> exit 2.
  - Service subset on a k8s install -> exit 2 (kubectl apply is incremental).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()


def _write_deployment_state(install_dir: Path, *, target: str = "compose") -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    platform = (
        {"k8s": {}} if target == "k8s" else {"compose": {"data_dir": "/tmp/wip-test-data"}}
    )
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "test-install"},
            "spec": {
                "target": target,
                "apps": [],
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "localhost"},
                "images": {"tag_overrides": {}},
                "platform": platform,
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


class TestRedeployErrors:
    def test_missing_install_state(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path)])
        assert result.exit_code != 0

    def test_unknown_service_rejected(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path)
        _write_compose(tmp_path, ["registry", "def-store"])
        result = runner.invoke(
            app,
            ["redeploy", "registry", "bogus", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "unknown service" in result.output
        assert "bogus" in result.output

    def test_service_subset_rejected_on_k8s(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, target="k8s")
        result = runner.invoke(
            app,
            ["redeploy", "registry", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "compose/dev only" in result.output


class TestRedeployHappyPath:
    def test_no_args_full_reup(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path)
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1
        # Whole-install re-up: compose recreates only the changed services.
        assert mutate.call_args.kwargs["services_scope"] is None
        deployment = mutate.call_args.args[0]
        assert deployment.spec.target == "compose"

    def test_service_subset_scopes_apply(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path)
        _write_compose(tmp_path, ["registry", "def-store", "document-store"])
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                [
                    "redeploy", "registry", "def-store",
                    "--install-dir", str(tmp_path),
                ],
            )
        assert result.exit_code == 0, result.output
        assert mutate.call_args.kwargs["services_scope"] == ["registry", "def-store"]
