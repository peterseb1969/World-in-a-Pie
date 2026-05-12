"""Regression coverage for CASE-331 — wip-deploy post-reboot recovery.

Three fixes landed under CASE-331; this file owns the unit-level
contract for each:

- Fix A: ``up_compose_install`` — bring an existing install back to
  running without spec recomputation. Tests exercise error paths
  (missing dir / missing compose) and the happy-path command shape
  (``compose up -d`` against the rendered tree, no ``--build``).
- Fix B: ``_diff_spec_for_drops`` — drop-detection over Deployment
  state. Tests cover the four shapes: no drops, app drops, module
  drops, both.
- Fix C: ``split_services_and_apps`` — partition the status table
  into Services + Apps buckets by container-name convention.

End-to-end CLI smoke is covered by the live-install verification
documented in the CASE-331 implementation note; this file is the
narrow contract test for the helper layer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.apply import ApplyError, up_compose_install
from wip_deploy.spec.deployment import (
    ApplySpec,
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    ModulesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.status import ServiceStatus, split_services_and_apps

# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────


def _write_compose(install_dir: Path, services: dict[str, dict]) -> None:
    """Write a minimal docker-compose.yaml with the given services."""
    import yaml

    install_dir.mkdir(parents=True, exist_ok=True)
    compose = {"services": services}
    (install_dir / "docker-compose.yaml").write_text(yaml.safe_dump(compose))


def _make_deployment(
    apps: list[tuple[str, bool]],
    modules: list[str],
) -> Deployment:
    """Construct a minimal Deployment for spec-diff tests.

    ``apps`` is a list of (name, enabled) tuples. ``modules`` is the
    ``modules.optional`` list. Everything else gets sensible defaults
    so the Pydantic validators are satisfied.
    """
    return Deployment(
        metadata=DeploymentMetadata(name="test"),
        spec=DeploymentSpec(
            target="compose",
            apps=[AppRef(name=n, enabled=e) for n, e in apps],
            modules=ModulesSpec(optional=modules),
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="localhost"),
            images=ImagesSpec(),
            platform=PlatformSpec(
                compose=ComposePlatform(data_dir=Path("/tmp/wip-test-data")),
            ),
            secrets=SecretsSpec(backend="file", location="/tmp/wip-test-secrets"),
            apply=ApplySpec(),
        ),
    )


# ────────────────────────────────────────────────────────────────────
# Fix A — up_compose_install
# ────────────────────────────────────────────────────────────────────


class TestUpCompose:
    def test_missing_install_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ApplyError, match="no docker-compose.yaml"):
            up_compose_install(install_dir=tmp_path / "missing", wait=False)

    def test_missing_compose_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ApplyError, match="no docker-compose.yaml"):
            up_compose_install(install_dir=tmp_path, wait=False)

    def test_runs_compose_up_dash_d_no_build(self, tmp_path: Path) -> None:
        """The whole point of `up`: bring containers back without rebuilding."""
        _write_compose(
            tmp_path,
            {"registry": {"image": "x"}, "def-store": {"image": "y"}},
        )
        with (
            patch(
                "wip_deploy.apply.shutil.which",
                return_value="/usr/bin/podman-compose",
            ),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            up_compose_install(install_dir=tmp_path, wait=False)

        cmd = mock_run.call_args_list[0].args[0]
        assert "up" in cmd
        assert "-d" in cmd
        # The "no recompute" contract — must NOT rebuild or recreate.
        assert "--build" not in cmd
        assert "--force-recreate" not in cmd

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
                1, ["podman-compose", "up"]
            )
            with pytest.raises(ApplyError, match="up failed"):
                up_compose_install(install_dir=tmp_path, wait=False)


# ────────────────────────────────────────────────────────────────────
# Fix B — _diff_spec_for_drops
# ────────────────────────────────────────────────────────────────────


class TestDiffSpecForDrops:
    """Drop detection over Deployment state.

    Imported from cli.py because the helper lives there (it's tightly
    coupled to the install verb's flow). The test treats it as a pure
    function over two Deployments.
    """

    def _diff(self, prev: Deployment, curr: Deployment) -> tuple[list[str], list[str]]:
        from wip_deploy.cli import _diff_spec_for_drops
        return _diff_spec_for_drops(prev, curr)

    def test_no_changes_means_no_drops(self) -> None:
        prev = _make_deployment(
            apps=[("react-console", True)], modules=["reporting-sync"]
        )
        curr = _make_deployment(
            apps=[("react-console", True)], modules=["reporting-sync"]
        )
        dropped_apps, dropped_modules = self._diff(prev, curr)
        assert dropped_apps == []
        assert dropped_modules == []

    def test_dropped_app_surfaces(self) -> None:
        prev = _make_deployment(
            apps=[("react-console", True)], modules=["reporting-sync"]
        )
        curr = _make_deployment(apps=[], modules=["reporting-sync"])
        dropped_apps, dropped_modules = self._diff(prev, curr)
        assert dropped_apps == ["react-console"]
        assert dropped_modules == []

    def test_disabled_app_counts_as_dropped(self) -> None:
        """An app flipping from enabled → disabled is a drop too."""
        prev = _make_deployment(apps=[("react-console", True)], modules=[])
        curr = _make_deployment(apps=[("react-console", False)], modules=[])
        dropped_apps, _ = self._diff(prev, curr)
        assert dropped_apps == ["react-console"]

    def test_dropped_module_surfaces(self) -> None:
        prev = _make_deployment(
            apps=[], modules=["reporting-sync", "minio", "mcp-server"]
        )
        curr = _make_deployment(apps=[], modules=["reporting-sync"])
        dropped_apps, dropped_modules = self._diff(prev, curr)
        assert dropped_apps == []
        assert dropped_modules == ["mcp-server", "minio"]

    def test_added_items_are_not_drops(self) -> None:
        """Net-new state on the current side doesn't count as a drop."""
        prev = _make_deployment(apps=[], modules=[])
        curr = _make_deployment(
            apps=[("react-console", True)], modules=["minio"]
        )
        dropped_apps, dropped_modules = self._diff(prev, curr)
        assert dropped_apps == []
        assert dropped_modules == []

    def test_both_apps_and_modules_dropped(self) -> None:
        prev = _make_deployment(
            apps=[("a", True), ("b", True)], modules=["x", "y"]
        )
        curr = _make_deployment(apps=[("a", True)], modules=["x"])
        dropped_apps, dropped_modules = self._diff(prev, curr)
        assert dropped_apps == ["b"]
        assert dropped_modules == ["y"]


# ────────────────────────────────────────────────────────────────────
# Fix C — split_services_and_apps
# ────────────────────────────────────────────────────────────────────


class TestSplitServicesAndApps:
    def test_empty_apps_set_returns_everything_as_services(self) -> None:
        rows = [
            ServiceStatus(name="wip-registry", state="running", health="healthy"),
            ServiceStatus(name="wip-def-store", state="running", health="healthy"),
        ]
        services, apps = split_services_and_apps(rows, set())
        assert services == rows
        assert apps == []

    def test_app_container_goes_to_apps_bucket(self) -> None:
        rows = [
            ServiceStatus(name="wip-registry", state="running", health="healthy"),
            ServiceStatus(name="wip-react-console", state="running", health="healthy"),
            ServiceStatus(name="wip-def-store", state="running", health="healthy"),
        ]
        services, apps = split_services_and_apps(rows, {"react-console"})
        assert [r.name for r in services] == ["wip-registry", "wip-def-store"]
        assert [r.name for r in apps] == ["wip-react-console"]

    def test_declared_but_missing_app_returns_empty_apps_bucket(self) -> None:
        """Caller can detect the gap by seeing app_names non-empty but apps[]."""
        rows = [
            ServiceStatus(name="wip-registry", state="running", health="healthy"),
        ]
        services, apps = split_services_and_apps(rows, {"react-console"})
        assert apps == []
        assert services == rows

    def test_multiple_apps_partitioned_correctly(self) -> None:
        rows = [
            ServiceStatus(name="wip-app-a", state="running", health="healthy"),
            ServiceStatus(name="wip-app-b", state="exited", health=""),
            ServiceStatus(name="wip-registry", state="running", health="healthy"),
        ]
        services, apps = split_services_and_apps(rows, {"app-a", "app-b"})
        assert {r.name for r in apps} == {"wip-app-a", "wip-app-b"}
        assert [r.name for r in services] == ["wip-registry"]
