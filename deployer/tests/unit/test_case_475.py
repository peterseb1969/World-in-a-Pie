"""Regression coverage for CASE-475 — reversible stop / start.

A zero-delete halt-and-resume pair for wip-deploy. Run-state only: no
render, no rebuild, no spec recompute (the same class as `up`/`restart`).
Before this, the only k8s teardown was `nuke` (deletes the whole
namespace, PVCs included) and `up` was compose-only — so there was no
guard-railed "shut it down, change nothing, bring it back".

This file owns the unit-level contract for the apply-layer functions:

- compose: `stop_install` / `start_install` shell `compose stop|start`
  against the rendered tree (NOT `down`/`up`).
- k8s: scale BOTH Deployments AND StatefulSets via the
  `app.kubernetes.io/part-of=wip` label — the StatefulSet half is the
  load-bearing detail (a bare `scale deploy --all` leaves the storage
  tier running).
- error paths: missing compose file; k8s without a namespace.

CLI wiring (target/namespace auto-detection from deployer-state) is the
same path `status` uses and is covered there (CASE-364); this file is the
narrow contract test for the apply layer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.apply import ApplyError, start_install, stop_install


def _write_compose(install_dir: Path, services: dict[str, dict]) -> None:
    """Write a minimal docker-compose.yaml with the given services."""
    import yaml

    install_dir.mkdir(parents=True, exist_ok=True)
    compose = {"services": services}
    (install_dir / "docker-compose.yaml").write_text(yaml.safe_dump(compose))


# ────────────────────────────────────────────────────────────────────
# compose: stop / start shell `compose stop|start`, keep containers
# ────────────────────────────────────────────────────────────────────


class TestComposeStopStart:
    def test_stop_missing_compose_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ApplyError, match="no docker-compose.yaml"):
            stop_install(install_dir=tmp_path, target="compose")

    def test_start_missing_compose_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ApplyError, match="no docker-compose.yaml"):
            start_install(install_dir=tmp_path, target="compose")

    def test_stop_runs_compose_stop_not_down(self, tmp_path: Path) -> None:
        """`stop` keeps the containers — it is `compose stop`, NOT `down`."""
        _write_compose(tmp_path, {"registry": {"image": "x"}})
        with (
            patch(
                "wip_deploy.apply.shutil.which",
                return_value="/usr/bin/podman-compose",
            ),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            stop_install(install_dir=tmp_path, target="compose")

        cmd = mock_run.call_args_list[0].args[0]
        assert "stop" in cmd
        assert "down" not in cmd
        assert "rm" not in cmd

    def test_start_runs_compose_start_not_up(self, tmp_path: Path) -> None:
        """`start` revives stopped containers — `compose start`, not `up`."""
        _write_compose(tmp_path, {"registry": {"image": "x"}})
        with (
            patch(
                "wip_deploy.apply.shutil.which",
                return_value="/usr/bin/podman-compose",
            ),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            start_install(install_dir=tmp_path, target="compose")

        cmd = mock_run.call_args_list[0].args[0]
        assert "start" in cmd
        assert "up" not in cmd
        assert "--build" not in cmd

    def test_compose_failure_surfaces_as_apply_error(
        self, tmp_path: Path
    ) -> None:
        import subprocess

        _write_compose(tmp_path, {"registry": {"image": "x"}})
        with (
            patch(
                "wip_deploy.apply.shutil.which",
                return_value="/usr/bin/podman-compose",
            ),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = subprocess.CalledProcessError(
                1, ["podman-compose", "stop"]
            )
            with pytest.raises(ApplyError, match="stop failed"):
                stop_install(install_dir=tmp_path, target="compose")


# ────────────────────────────────────────────────────────────────────
# k8s: scale BOTH Deployments AND StatefulSets, scoped by part-of=wip
# ────────────────────────────────────────────────────────────────────


class TestK8sStopStart:
    def test_stop_scales_both_workload_kinds_to_zero(self) -> None:
        """The load-bearing detail: stop must cover StatefulSets too,
        else the storage tier (mongo/postgres/minio/nats/dex) keeps
        running — the silent half-stop CASE-475 documents."""
        with patch("wip_deploy.apply._kubectl_run") as mock_kubectl:
            stop_install(
                install_dir=Path("/unused"),
                target="k8s",
                namespace="wip-kb",
            )

        args = mock_kubectl.call_args_list[0].args[0]
        assert "scale" in args
        assert "deployment,statefulset" in args
        assert "--selector=app.kubernetes.io/part-of=wip" in args
        assert "--replicas=0" in args
        assert "wip-kb" in args

    def test_start_scales_both_workload_kinds_to_one(self) -> None:
        with patch("wip_deploy.apply._kubectl_run") as mock_kubectl:
            start_install(
                install_dir=Path("/unused"),
                target="k8s",
                namespace="wip-kb",
            )

        args = mock_kubectl.call_args_list[0].args[0]
        assert "scale" in args
        assert "deployment,statefulset" in args
        assert "--selector=app.kubernetes.io/part-of=wip" in args
        assert "--replicas=1" in args

    def test_stop_k8s_without_namespace_raises(self) -> None:
        with pytest.raises(ApplyError, match="k8s stop requires a namespace"):
            stop_install(install_dir=Path("/unused"), target="k8s")

    def test_start_k8s_without_namespace_raises(self) -> None:
        with pytest.raises(ApplyError, match="k8s start requires a namespace"):
            start_install(install_dir=Path("/unused"), target="k8s")
