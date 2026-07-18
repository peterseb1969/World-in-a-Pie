"""Regression coverage for CASE-355 — dev target loud-fail on missing app source.

Three surfaces:

  1. Spec — `DevPlatform.apps_from_registry` accepts a list of names,
     defaults to empty.
  2. CLI — `--app-from-registry NAME` parses, plumbs through
     `_assemble` → `BuildInputs` → `DevPlatform`, and implicitly
     enables the named app (parallel to `--app-source`'s
     CASE-313-era behaviour).
  3. End-to-end via the dev renderer — already covered in
     `tests/unit/renderers/test_dev_simple.py`:
       - `test_app_in_apps_from_registry_uses_registry_image` —
         opt-in path produces an `image:` block.
       - `test_app_without_source_and_not_in_apps_from_registry_raises`
         — bare enabled-app trips the ValueError.

This file owns the spec + CLI contract; the renderer-level cases
live in the existing test_dev_simple.py because they need the
discovery fixtures already wired there.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import app
from wip_deploy.spec.deployment import DevPlatform

runner = CliRunner()


# ────────────────────────────────────────────────────────────────────
# Spec — DevPlatform.apps_from_registry
# ────────────────────────────────────────────────────────────────────


class TestDevPlatformAppsFromRegistry:
    def test_defaults_to_empty_list(self) -> None:
        dp = DevPlatform()
        assert dp.apps_from_registry == []

    def test_accepts_list_of_names(self) -> None:
        dp = DevPlatform(apps_from_registry=["react-console", "clintrial"])
        assert dp.apps_from_registry == ["react-console", "clintrial"]

    def test_persists_through_model_dump(self) -> None:
        """Round-trips through model_dump for deployment-state persistence."""
        dp = DevPlatform(apps_from_registry=["kb"])
        dumped = dp.model_dump()
        assert dumped["apps_from_registry"] == ["kb"]
        rehydrated = DevPlatform.model_validate(dumped)
        assert rehydrated.apps_from_registry == ["kb"]


# ────────────────────────────────────────────────────────────────────
# BuildInputs → DevPlatform plumbing
# ────────────────────────────────────────────────────────────────────


class TestBuildInputsPlumbing:
    def test_apps_from_registry_threaded_into_dev_platform(
        self, tmp_path: Path
    ) -> None:
        """`BuildInputs.apps_from_registry` lands on
        `Deployment.spec.platform.dev.apps_from_registry` after
        `build_deployment`."""
        inputs = BuildInputs(
            name="test",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=["react-console"],
            apps_from_registry=["react-console"],
        )
        deployment = build_deployment(inputs)
        assert deployment.spec.platform.dev is not None
        assert deployment.spec.platform.dev.apps_from_registry == ["react-console"]

    def test_empty_apps_from_registry_omitted_from_dev_block(self) -> None:
        """When the operator doesn't pass --app-from-registry, the
        deployment ends up with an empty list (not absent). Same shape
        as `app_sources`. Keeps the persisted state predictable."""
        inputs = BuildInputs(
            name="test",
            preset="standard",
            target="dev",
            hostname="localhost",
            apps=[],
        )
        deployment = build_deployment(inputs)
        assert deployment.spec.platform.dev is not None
        assert deployment.spec.platform.dev.apps_from_registry == []

    def test_compose_target_ignores_apps_from_registry(
        self, tmp_path: Path
    ) -> None:
        """`--app-from-registry` is dev-only. Setting it on a compose
        install doesn't error — it's just inert because compose
        already pulls from the registry by default."""
        inputs = BuildInputs(
            name="test",
            preset="standard",
            target="compose",
            hostname="wip.local",
            compose_data_dir=tmp_path / "data",
            apps=["react-console"],
            apps_from_registry=["react-console"],  # ignored
        )
        deployment = build_deployment(inputs)
        # No dev platform on a compose install.
        assert deployment.spec.platform.dev is None
        assert deployment.spec.platform.compose is not None


# ────────────────────────────────────────────────────────────────────
# CLI — --app-from-registry plumbing + implicit-enable
# ────────────────────────────────────────────────────────────────────


class TestCLIAppFromRegistry:
    def test_show_spec_plumbs_apps_from_registry(self) -> None:
        """`wip-deploy show-spec --app-from-registry react-console`
        produces a Deployment whose DevPlatform contains react-console
        in apps_from_registry."""
        result = runner.invoke(
            app,
            [
                "show-spec",
                "--target", "dev",
                "--hostname", "localhost",
                "--app-from-registry", "react-console",
                "--name", "test-show-spec",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        # show-spec dumps JSON; just look for the literal in the dump
        # — full Pydantic deserialization is overkill for plumbing.
        assert '"apps_from_registry"' in result.output
        assert '"react-console"' in result.output

    def test_show_spec_implicitly_enables_apps_from_registry_name(self) -> None:
        """Parallel to --app-source's CASE-313-era implicit-enable:
        passing --app-from-registry NAME without a separate --app NAME
        should still result in NAME being in spec.apps. Opting an app
        into the fallback only makes sense for an app that's actually
        enabled."""
        result = runner.invoke(
            app,
            [
                "show-spec",
                "--target", "dev",
                "--hostname", "localhost",
                "--app-from-registry", "react-console",
                "--name", "test-implicit-enable",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        # The app should appear in spec.apps with enabled=true.
        assert '"name": "react-console"' in result.output
        assert '"enabled": true' in result.output
