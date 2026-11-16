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
  4. The verbs that apply a *saved* spec (CASE-1386) — `add-app`,
     `redeploy` and the rest refuse an enabled app the state left
     unbound in their own words, since the renderer's advice names
     `install`'s flags.

This file owns the spec + CLI contract; the renderer-level cases
live in the existing test_dev_simple.py because they need the
discovery fixtures already wired there.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import wip_deploy.cli as cli
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import app
from wip_deploy.spec.deployment import DevPlatform

runner = CliRunner()
WIP_ROOT = Path(__file__).resolve().parents[3]
STATE = "deployment.deployer-state"


@pytest.fixture(autouse=True)
def _in_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manifests are discovered from the cwd when the state carries no root."""
    monkeypatch.chdir(WIP_ROOT)


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
            hostname="wip.localhost",
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
            hostname="wip.localhost",
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
                "--hostname", "wip.localhost",
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
                "--hostname", "wip.localhost",
                "--app-from-registry", "react-console",
                "--name", "test-implicit-enable",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        # The app should appear in spec.apps with enabled=true.
        assert '"name": "react-console"' in result.output
        assert '"enabled": true' in result.output


# ────────────────────────────────────────────────────────────────────
# CASE-1386 — verbs applying a saved spec advise their own routes
# ────────────────────────────────────────────────────────────────────

UNBOUND = "song"
"""An app the saved spec enables with neither a checkout nor the opt-in — a
state no verb produces (`remove-app` drops both bindings with the app, and
`install` refuses it before it persists), so it comes from a spec written
before the loud-fail or hand-edited afterwards."""


def _write_state(
    install_dir: Path,
    *,
    target: str = "dev",
    apps: list[dict],
    dev: dict | None = None,
    name: str = "tc01",
    registry: str | None = None,
    secrets_location: str | None = None,
) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    platform: dict = {"dev": dev or {}}
    if target != "dev":
        platform = {"compose": {"data_dir": str(install_dir / "data")}}
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": name},
            "spec": {
                "target": target,
                "apps": apps,
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "wip.localhost"},
                "images": {"tag_overrides": {}, **({"registry": registry} if registry else {})},
                "platform": platform,
                "secrets": {
                    "backend": "file",
                    "location": secrets_location or str(install_dir / "secrets"),
                },
                "apply": {},
            },
        },
    }
    (install_dir / STATE).write_text(json.dumps(payload))
    return install_dir


def _stub_the_apply(monkeypatch: pytest.MonkeyPatch, install_dir: Path) -> SimpleNamespace:
    """Everything `_apply_and_persist_mutation` does past its front-door checks,
    stubbed: the verb runs for real up to the refusal, and the recorder shows
    whether the render and the save were reached."""
    steps = SimpleNamespace(rendered=[], persisted=[], removed=[])
    for name in (
        "_select_cluster_or_exit",
        "_refuse_runtime_reservation_collisions",
        "_refuse_attachment_collisions",
        "_persist_generated_secrets",
        "changed_outside_scope",
        "_after_app_removal",
    ):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ensure_secrets_via_spec", lambda *a, **k: {})
    monkeypatch.setattr(
        cli, "_render_tree", lambda *a, **k: steps.rendered.append(1) or cli.FileTree()
    )
    monkeypatch.setattr(
        cli,
        "apply_compose",
        lambda **kw: cli.ApplyResult(install_dir=install_dir, services_up=0, healthy=True),
    )
    monkeypatch.setattr(
        cli, "_persist_deployment", lambda deployment, *_a, **_k: steps.persisted.append(deployment)
    )
    return steps


def _routes(out: str, app_name: str, install_name: str) -> list[str]:
    return [
        f"app {app_name!r} is enabled in this install but has no source for --target dev",
        f"add-app {app_name} --app-source <path-to-checkout> --name {install_name}",
        f"add-app {app_name} --app-from-registry --name {install_name}",
        f"remove-app {app_name} --name {install_name}",
    ]


class TestVerbsOnAnUnboundSavedApp:
    def test_add_app_for_another_app_is_refused_naming_the_routes_this_verb_accepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install = _write_state(tmp_path / "install", apps=[{"name": UNBOUND, "enabled": True}])
        steps = _stub_the_apply(monkeypatch, install)
        checkout = tmp_path / "wip-kb"
        checkout.mkdir()

        result = runner.invoke(
            app,
            ["add-app", "wip-kb", "--app-source", str(checkout),
             "--name", "tc01", "--install-dir", str(install)],
        )

        assert result.exit_code == 2, result.output
        for line in _routes(result.output, UNBOUND, "tc01"):
            assert line in result.output, result.output
        # `install`'s NAME=PATH form, its re-install, and the echo of the
        # renderer's error under this verb's label are none of them routes here.
        assert f"{UNBOUND}=" not in result.output
        assert "re-install" not in result.output
        assert "aborted" not in result.output
        # The refusal comes before the render, and records nothing.
        assert steps.rendered == []
        assert steps.persisted == []

    def test_redeploy_is_refused_the_same_way(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": UNBOUND, "enabled": True}, {"name": "wip-kb", "enabled": True}],
            dev={"app_sources": {"wip-kb": str(tmp_path / "kb")}},
        )
        steps = _stub_the_apply(monkeypatch, install)

        result = runner.invoke(
            app, ["redeploy", "--name", "tc01", "--install-dir", str(install)]
        )

        assert result.exit_code == 2, result.output
        for line in _routes(result.output, UNBOUND, "tc01"):
            assert line in result.output, result.output
        assert f"{UNBOUND}=" not in result.output
        assert steps.rendered == [] and steps.persisted == []

    def test_only_the_unbound_apps_are_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install = _write_state(
            tmp_path / "install",
            apps=[
                {"name": UNBOUND, "enabled": True},
                {"name": "wip-kb", "enabled": True},
                {"name": "dev-kb", "enabled": False},
            ],
            dev={"app_sources": {"wip-kb": str(tmp_path / "kb")}},
        )
        _stub_the_apply(monkeypatch, install)

        result = runner.invoke(
            app, ["redeploy", "--name", "tc01", "--install-dir", str(install)]
        )

        assert result.exit_code == 2, result.output
        assert UNBOUND in result.output
        assert "wip-kb" not in result.output
        assert "dev-kb" not in result.output

    def test_taking_the_app_out_of_the_install_is_the_way_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`remove-app song` mutates the spec before the check, so the lighter
        route it offers itself still works — the run renders and saves."""
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": UNBOUND, "enabled": True}, {"name": "wip-kb", "enabled": True}],
            dev={"app_sources": {"wip-kb": str(tmp_path / "kb")}},
        )
        steps = _stub_the_apply(monkeypatch, install)

        result = runner.invoke(
            app, ["remove-app", UNBOUND, "--name", "tc01", "--install-dir", str(install)]
        )

        assert result.exit_code == 0, result.output
        assert steps.rendered, "the render was never reached"
        [persisted] = steps.persisted
        assert [a.name for a in persisted.spec.apps] == ["wip-kb"]

    def test_an_install_whose_apps_are_all_bound_applies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": UNBOUND, "enabled": True}],
            dev={"app_sources": {UNBOUND: str(tmp_path / "song")}},
        )
        steps = _stub_the_apply(monkeypatch, install)

        result = runner.invoke(
            app, ["redeploy", "--name", "tc01", "--install-dir", str(install)]
        )

        assert result.exit_code == 0, result.output
        assert steps.rendered and steps.persisted

    def test_a_compose_install_keeps_pullable_apps_without_a_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The binding is a dev-target rule: compose pulls the registry image."""
        install = _write_state(
            tmp_path / "install", target="compose", registry="ghcr.io/example",
            apps=[{"name": UNBOUND, "enabled": True}],
        )
        steps = _stub_the_apply(monkeypatch, install)

        result = runner.invoke(
            app, ["redeploy", "--name", "tc01", "--install-dir", str(install)]
        )

        assert result.exit_code == 0, result.output
        assert steps.rendered and steps.persisted


class TestASpecFromFlagsKeepsTheRenderersAdvice:
    def test_render_names_installs_flags(self, tmp_path: Path) -> None:
        """`render`/`install` build the spec from flags, so `--app-source
        NAME=PATH` and the re-install the renderer names are exactly what
        their operator can run: that path keeps the renderer's own refusal."""
        result = runner.invoke(
            app,
            ["render", "--target", "dev", "--hostname", "wip.localhost",
             "--app", UNBOUND, "--name", "tc-render",
             "--output-dir", str(tmp_path / "preview")],
        )

        assert result.exit_code == 2, result.output
        assert f"--app-source {UNBOUND}=<path-to-checkout>" in result.output
        assert f"--app-from-registry {UNBOUND}" in result.output
        assert "disable the app and re-install" in result.output
