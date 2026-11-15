"""CASE-1312 — `add-app` can put a dev app on its registry image.

`wip-deploy add-app wip-kb --tag 20261113d --name tc01` on a dev install
aborted with advice this verb does not accept. The dev renderer refuses an app
with neither a local source nor a registry opt-in, and its message names
`install`'s flags (`--app-source NAME=PATH`, `--app-from-registry NAME`) and
tells the operator to re-install; `add-app` passed it through unchanged, and the
opt-in could only be set when the install was first assembled. So a dev install
had no route to a registry-image app short of re-specifying the whole install.

`add-app` now takes the opt-in itself — on/off, since the app is the command's
argument — and the dev checks that follow are worded in this verb's syntax and
run before anything is recorded:

  - an app with neither source nor opt-in is refused, naming the three routes
    as `add-app` accepts them;
  - the opt-in contradicts a source (`--app-source` this run, or one the install
    already binds) because the renderer tests source first and would ignore it;
  - `--tag` on a source-built app is refused: dev renders that image as
    `<image>:dev-<install>`, so the tag would be recorded and never pull
    (`app-deploy` refuses the same thing for the same reason).

`remove-app` drops the opt-in with the app. And a render refusal no longer
leaves newly generated secrets on disk — they are written after the render.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

import wip_deploy.cli as cli
from wip_deploy.cli import app
from wip_deploy.discovery import discover
from wip_deploy.spec.deployment import Deployment

runner = CliRunner()
WIP_ROOT = Path(__file__).resolve().parents[3]
STATE = "deployment.deployer-state"
APP = "wip-kb"


@pytest.fixture(autouse=True)
def _in_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manifests are discovered from the cwd when the state carries no root."""
    monkeypatch.chdir(WIP_ROOT)


def _write_state(
    install_dir: Path,
    *,
    target: str = "dev",
    apps: list[dict] | None = None,
    dev: dict | None = None,
    tag_overrides: dict[str, str] | None = None,
    secrets_location: str | None = None,
) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    platform: dict = {"dev": dev if dev is not None else {}}
    if target != "dev":
        platform["compose"] = {"data_dir": "/tmp/wip-test-data"}
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "tc01"},
            "spec": {
                "target": target,
                "apps": apps or [],
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "wip.localhost"},
                "images": {"tag_overrides": tag_overrides or {}},
                "platform": platform,
                "secrets": {
                    "backend": "file",
                    "location": secrets_location or "/tmp/wip-test-secrets",
                },
                "apply": {},
            },
        },
    }
    (install_dir / STATE).write_text(json.dumps(payload))
    return install_dir


def _add(install_dir: Path, *args: str):
    """`add-app` with the apply/persist step captured instead of run."""
    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = runner.invoke(
            app,
            ["add-app", APP, *args, "--name", "tc01", "--install-dir", str(install_dir)],
        )
    return result, mutate


def _reload(install_dir: Path) -> Deployment:
    return cli._load_deployment(install_dir)


# ────────────────────────────────────────────────────────────────────
# The opt-in the verb was missing
# ────────────────────────────────────────────────────────────────────


class TestAddAppFromRegistry:
    def test_the_opt_in_is_recorded_and_the_app_added(
        self, tmp_path: Path
    ) -> None:
        install = _write_state(tmp_path / "install")
        result, mutate = _add(install, "--app-from-registry")

        assert result.exit_code == 0, result.output
        deployment = mutate.call_args.args[0]
        ref = next(a for a in deployment.spec.apps if a.name == APP)
        assert ref.enabled
        assert deployment.spec.platform.dev.apps_from_registry == [APP]

    def test_the_tag_the_case_started_with_now_reaches_the_image(
        self, tmp_path: Path
    ) -> None:
        """The reported command, with the opt-in: the tag lands on an image the
        renderer actually resolves (dev_simple takes `image_ref`, which honours
        `tag_overrides`, for a registry-sourced app)."""
        install = _write_state(tmp_path / "install")
        result, mutate = _add(
            install, "--app-from-registry", "--tag", "20261113d"
        )

        assert result.exit_code == 0, result.output
        deployment = mutate.call_args.args[0]
        assert deployment.spec.images.tag_overrides[APP] == "20261113d"
        assert deployment.spec.platform.dev.apps_from_registry == [APP]

    def test_an_existing_opt_in_is_not_duplicated(self, tmp_path: Path) -> None:
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": APP, "enabled": True}],
            dev={"apps_from_registry": [APP, "song"]},
        )
        result, mutate = _add(install, "--app-from-registry")

        assert result.exit_code == 0, result.output
        assert mutate.call_args.args[0].spec.platform.dev.apps_from_registry == [
            APP,
            "song",
        ]

    def test_the_opt_in_on_an_enabled_app_is_a_real_mutation(
        self, tmp_path: Path
    ) -> None:
        """Not the friendly no-op: without the apply the opt-in would never be
        written, and the install would keep refusing the app at its next render."""
        install = _write_state(tmp_path / "install", apps=[{"name": APP, "enabled": True}])
        result, mutate = _add(install, "--app-from-registry")

        assert result.exit_code == 0, result.output
        assert "no change" not in result.output
        assert mutate.call_count == 1

    def test_an_app_opted_into_the_registry_still_takes_a_tag(
        self, tmp_path: Path
    ) -> None:
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": APP, "enabled": True}],
            dev={"apps_from_registry": [APP]},
        )
        result, mutate = _add(install, "--tag", "20261113d")

        assert result.exit_code == 0, result.output
        assert mutate.call_args.args[0].spec.images.tag_overrides[APP] == "20261113d"

    def test_the_opt_in_is_refused_off_the_dev_target(self, tmp_path: Path) -> None:
        install = _write_state(tmp_path / "install", target="compose")
        result, mutate = _add(install, "--app-from-registry")

        assert result.exit_code == 2
        assert "only valid for target=dev" in result.output
        mutate.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# The advice names what this verb accepts
# ────────────────────────────────────────────────────────────────────


class TestRefusalNamesWhatThisVerbAccepts:
    def test_an_app_with_no_source_offers_the_three_routes_in_add_app_form(
        self, tmp_path: Path
    ) -> None:
        install = _write_state(tmp_path / "install")
        result, mutate = _add(install)

        assert result.exit_code == 2, result.output
        assert "--app-source <path-to-checkout>" in result.output
        assert "--app-from-registry" in result.output
        assert f"remove-app {APP}" in result.output
        # `install`'s NAME=PATH form and its "re-install" are not this verb's.
        assert f"--app-source {APP}=" not in result.output
        assert "re-install" not in result.output
        mutate.assert_not_called()

    def test_the_case_s_command_is_refused_and_points_at_the_opt_in(
        self, tmp_path: Path
    ) -> None:
        """`add-app wip-kb --tag 20261113d` on dev: the app has no source, and
        a tag alone never gets one."""
        install = _write_state(tmp_path / "install")
        result, mutate = _add(install, "--tag", "20261113d")

        assert result.exit_code == 2, result.output
        assert "--app-from-registry" in result.output
        mutate.assert_not_called()

    def test_a_refusal_leaves_the_saved_spec_as_it_was(self, tmp_path: Path) -> None:
        install = _write_state(tmp_path / "install")
        runner.invoke(
            app,
            ["add-app", APP, "--tag", "20261113d", "--install-dir", str(install)],
        )

        spec = _reload(install).spec
        assert [a.name for a in spec.apps] == []
        assert spec.platform.dev.apps_from_registry == []
        assert APP not in spec.images.tag_overrides


# ────────────────────────────────────────────────────────────────────
# Contradictions between the flags and the install
# ────────────────────────────────────────────────────────────────────


class TestContradictionsAreRefused:
    def test_source_and_opt_in_cannot_be_asked_for_in_one_command(
        self, tmp_path: Path
    ) -> None:
        install = _write_state(tmp_path / "install")
        result, mutate = _add(
            install, "--app-source", str(tmp_path), "--app-from-registry"
        )

        assert result.exit_code == 2
        assert "one or the other" in result.output
        mutate.assert_not_called()

    def test_the_opt_in_is_refused_when_the_install_binds_a_source(
        self, tmp_path: Path
    ) -> None:
        """dev_simple tests `app_sources` before the opt-in, so recording both
        would claim a registry image that never runs."""
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": APP, "enabled": True}],
            dev={"app_sources": {APP: "/home/op/checkouts/wip-kb"}},
        )
        result, mutate = _add(install, "--app-from-registry")

        assert result.exit_code == 2
        assert "/home/op/checkouts/wip-kb" in result.output
        assert f"remove-app {APP}" in result.output
        mutate.assert_not_called()

    def test_a_tag_on_a_locally_built_app_is_refused(self, tmp_path: Path) -> None:
        install = _write_state(tmp_path / "install")
        result, mutate = _add(
            install, "--app-source", str(tmp_path), "--tag", "20261113d"
        )

        assert result.exit_code == 2
        assert "built from source" in result.output
        assert "dev-tc01" in result.output
        mutate.assert_not_called()

    def test_a_tag_is_refused_when_the_opt_in_and_a_source_both_exist(
        self, tmp_path: Path
    ) -> None:
        """The opt-in in the spec does not make the tag real while a source is
        bound — the same finding `app-deploy` holds."""
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": APP, "enabled": True}],
            dev={
                "apps_from_registry": [APP],
                "app_sources": {APP: "/home/op/checkouts/wip-kb"},
            },
        )
        result, mutate = _add(install, "--tag", "20261113d")

        assert result.exit_code == 2
        assert "built from source" in result.output
        mutate.assert_not_called()

    def test_a_compose_install_keeps_its_tag_behaviour(self, tmp_path: Path) -> None:
        install = _write_state(tmp_path / "install", target="compose")
        result, mutate = _add(install, "--tag", "20261113d")

        assert result.exit_code == 0, result.output
        assert mutate.call_args.args[0].spec.images.tag_overrides[APP] == "20261113d"


# ────────────────────────────────────────────────────────────────────
# remove-app drops the opt-in with the app
# ────────────────────────────────────────────────────────────────────


class TestRemoveAppDropsTheOptIn:
    def _remove(self, install_dir: Path):
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app,
                ["remove-app", APP, "--name", "tc01", "--install-dir", str(install_dir)],
            )
        return result, mutate

    def test_both_dev_bindings_go_with_the_app(self, tmp_path: Path) -> None:
        install = _write_state(
            tmp_path / "install",
            apps=[{"name": APP, "enabled": True}, {"name": "song", "enabled": True}],
            dev={
                "app_sources": {APP: "/home/op/kb", "song": "/home/op/song"},
                "apps_from_registry": [APP, "song"],
            },
        )
        result, mutate = self._remove(install)

        assert result.exit_code == 0, result.output
        dev = mutate.call_args.args[0].spec.platform.dev
        assert APP not in dev.app_sources
        assert APP not in dev.apps_from_registry
        # Another app's bindings are untouched.
        assert dev.app_sources["song"] == Path("/home/op/song")
        assert dev.apps_from_registry == ["song"]


# ────────────────────────────────────────────────────────────────────
# A refused render leaves no secrets behind
# ────────────────────────────────────────────────────────────────────


class TestSecretsWaitForTheRender:
    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, render_effect):
        store = tmp_path / "secrets"
        deployment = _reload(_write_state(tmp_path, secrets_location=str(store)))
        discovery = discover(WIP_ROOT)
        for fn in (
            "_validate_or_exit",
            "_select_cluster_or_exit",
            "_refuse_runtime_reservation_collisions",
            "_refuse_attachment_collisions",
            "changed_outside_scope",
        ):
            monkeypatch.setattr(cli, fn, lambda *a, **k: None)
        monkeypatch.setattr(cli, "_render_tree", render_effect)
        monkeypatch.setattr(
            cli,
            "apply_compose",
            lambda **kw: cli.ApplyResult(
                install_dir=tmp_path, services_up=0, healthy=True
            ),
        )
        try:
            cli._apply_and_persist_mutation(
                deployment, discovery.components, discovery.apps, tmp_path, "Added app"
            )
        except typer.Exit as e:
            return e.exit_code, store
        return 0, store

    def test_a_render_refusal_generates_nothing_on_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_a, **_k):
            raise ValueError("app has no source for --target dev")

        exit_code, store = self._run(tmp_path, monkeypatch, refuse)

        assert exit_code == 2
        written = sorted(p.name for p in store.iterdir()) if store.exists() else []
        assert written == [], f"a refused run left {written} in the store"

    def test_the_values_are_written_once_the_render_succeeded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def ok(*_a, **_k):
            return cli.FileTree()

        exit_code, store = self._run(tmp_path, monkeypatch, ok)

        assert exit_code == 0
        written = sorted(p.name for p in store.iterdir())
        assert written, "the install's secrets must reach the store"
        assert "Generated new secrets: " + ", ".join(written) in capsys.readouterr().out
