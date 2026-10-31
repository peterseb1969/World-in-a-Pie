"""`wip-deploy images` — which image each service runs, why, and what a tag
change would move.

The resolver reports the precedence step that decided each tag
(pin > base > manifest > latest; upstream for fully-qualified infra
images). The plan previews a roll on a copy of the spec and singles out
apps that would move only because the deployment-wide tag moved: apps are
built in their own repos, so the new base tag rarely names an image that
exists for them. On the dev target, source-built services render as
`<name>:dev` and no tag reaches them.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wip_deploy.cli import app
from wip_deploy.config_gen.images import image_ref, resolve_tag
from wip_deploy.image_plan import (
    apps_carried_by_base_tag,
    format_image_json,
    format_image_table,
    plan_images,
)
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.spec.app import App, AppMetadata
from wip_deploy.spec.component import (
    Component,
    ComponentMetadata,
    ComponentSpec,
    ImageRef,
)
from wip_deploy.spec.deployment import AppRef, DevPlatform

runner = CliRunner()


def _deployment(
    images: ImagesSpec,
    *,
    apps: list[str] = (),
    target: str = "compose",
    app_sources: dict[str, Path] | None = None,
) -> Deployment:
    platform = (
        PlatformSpec(dev=DevPlatform(app_sources=app_sources or {}))
        if target == "dev"
        else PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d"))
    )
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target=target,
            auth=AuthSpec(mode="api-key-only", gateway=False, users=[]),
            network=NetworkSpec(hostname="wip.local"),
            images=images,
            apps=[AppRef(name=a) for a in apps],
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _component(
    name: str, image_name: str | None = None, pin: str | None = None,
    build_context: Path | None = None,
) -> Component:
    return Component(
        metadata=ComponentMetadata(name=name, category="core", description="t"),
        spec=ComponentSpec(
            image=ImageRef(name=image_name or name, tag=pin, build_context=build_context)
        ),
    )


def _app(name: str, pin: str | None = None) -> App:
    return App(
        metadata=ComponentMetadata(name=name, category="optional", description="t"),
        spec=ComponentSpec(image=ImageRef(name=name, tag=pin)),
        app_metadata=AppMetadata(display_name="T", route_prefix=f"/apps/{name}"),
    )


def _with_base_tag(d: Deployment, tag: str) -> Deployment:
    after = d.model_copy(deep=True)
    after.spec.images.tag = tag
    return after


class TestResolveTag:
    def test_each_precedence_step_is_named(self) -> None:
        d = _deployment(ImagesSpec(tag="B", tag_overrides={"pinned": "P"}))
        assert resolve_tag(_component("pinned"), d) == ("P", "pin")
        assert resolve_tag(_component("plain", pin="M"), d) == ("B", "base")

        no_base = _deployment(ImagesSpec())
        assert resolve_tag(_component("plain", pin="M"), no_base) == ("M", "manifest")
        assert resolve_tag(_component("plain"), no_base) == ("latest", "latest")

    def test_fully_qualified_images_ignore_the_base_tag(self) -> None:
        d = _deployment(ImagesSpec(tag="B", tag_overrides={"pg": "17"}))
        mongo = _component("mongodb", "docker.io/library/mongo", pin="7")
        assert resolve_tag(mongo, d) == ("7", "upstream")
        assert resolve_tag(_component("x", "docker.io/library/x"), d) == ("latest", "latest")
        # An explicit pin still reaches an infra image.
        assert resolve_tag(_component("pg", "docker.io/library/postgres", "16"), d) == (
            "17", "pin",
        )

    def test_image_ref_is_unchanged_by_the_refactor(self) -> None:
        d = _deployment(ImagesSpec(registry="ghcr.io/t", tag="B"))
        assert image_ref(_component("registry", pin="M"), d) == "ghcr.io/t/registry:B"
        mongo = _component("mongodb", "docker.io/library/mongo", pin="7")
        assert image_ref(mongo, d) == "docker.io/library/mongo:7"


class TestPlan:
    def _setup(self) -> tuple[Deployment, list[Component], list[App]]:
        d = _deployment(
            ImagesSpec(registry="ghcr.io/t", tag="OLD", tag_overrides={"kb": "kb-1"}),
            apps=["rc", "kb"],
        )
        components = [
            _component("registry"),
            _component("mongodb", "docker.io/library/mongo", pin="7"),
        ]
        apps = [_app("rc", pin="rc-manifest"), _app("kb"), _app("not-enabled")]
        return d, components, apps

    def test_rows_without_preview(self) -> None:
        d, components, apps = self._setup()
        rows = {r.name: r for r in plan_images(d, components, apps)}
        assert set(rows) == {"registry", "mongodb", "rc", "kb"}
        assert rows["rc"].image == "ghcr.io/t/rc:OLD"
        assert rows["rc"].source == "base"
        assert rows["kb"].source == "pin"
        assert not any(r.moves for r in rows.values())

    def test_base_tag_preview_moves_unpinned_and_flags_apps(self) -> None:
        d, components, apps = self._setup()
        rows = plan_images(d, components, apps, after=_with_base_tag(d, "NEW"))
        by_name = {r.name: r for r in rows}
        assert by_name["registry"].moves
        assert by_name["rc"].new_image == "ghcr.io/t/rc:NEW"
        assert not by_name["kb"].moves
        assert not by_name["mongodb"].moves
        assert [r.name for r in apps_carried_by_base_tag(rows)] == ["rc"]

        table = format_image_table(rows, preview=True)
        assert "⚠ app moved by base tag" in table
        assert "stays (pinned)" in table
        assert "stays (upstream pin)" in table

    def test_json_carries_moves(self) -> None:
        d, components, apps = self._setup()
        rows = plan_images(d, components, apps, after=_with_base_tag(d, "NEW"))
        payload = {r["name"]: r for r in json.loads(format_image_json(rows))}
        assert payload["rc"]["moves"] is True
        assert payload["kb"]["moves"] is False

    def test_dev_source_builds_ignore_tags(self, tmp_path: Path) -> None:
        d = _deployment(
            ImagesSpec(tag="OLD"), apps=["rc", "kb"], target="dev",
            app_sources={"rc": tmp_path},
        )
        components = [_component("registry", build_context=Path("."))]
        apps = [_app("rc"), _app("kb")]
        rows = {
            r.name: r
            for r in plan_images(d, components, apps, after=_with_base_tag(d, "NEW"))
        }
        assert (rows["registry"].image, rows["registry"].source) == ("registry:dev", "source")
        assert (rows["rc"].image, rows["rc"].source) == ("rc:dev", "source")
        assert not rows["registry"].moves and not rows["rc"].moves
        # An app without a local source pulls its image, so the tag reaches it.
        assert rows["kb"].moves


def _write_state(install_dir: Path, *, apps: list[str], overrides: dict) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "test-install"},
            "spec": {
                "target": "compose",
                "apps": [{"name": a, "enabled": True} for a in apps],
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "localhost"},
                "images": {
                    "registry": "ghcr.io/t", "tag": "OLD", "tag_overrides": overrides,
                },
                "platform": {"compose": {"data_dir": "/tmp/wip-test-data"}},
                "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
                "apply": {},
            },
        },
    }
    state = install_dir / "deployment.deployer-state"
    state.write_text(json.dumps(payload, indent=2))
    return state


class TestImagesCommand:
    def test_lists_images_and_changes_nothing(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, apps=["react-console"], overrides={})
        before = state.read_text()
        result = runner.invoke(app, ["images", "--install-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "ghcr.io/t/registry:OLD" in result.output
        assert "ghcr.io/t/react-console:OLD" in result.output
        assert "AFTER CHANGE" not in result.output
        assert state.read_text() == before

    def test_tag_preview_warns_with_the_pins_that_keep_apps(
        self, tmp_path: Path
    ) -> None:
        state = _write_state(
            tmp_path, apps=["react-console", "wip-kb"], overrides={"wip-kb": "kb-1"}
        )
        before = state.read_text()
        result = runner.invoke(
            app, ["images", "--install-dir", str(tmp_path), "--tag", "NEW"]
        )
        assert result.exit_code == 0, result.output
        assert "→ ghcr.io/t/registry:NEW" in result.output
        assert "--image-tag react-console=OLD" in result.output
        assert "wip-kb=" not in result.output
        assert state.read_text() == before

    def test_image_tag_preview(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=[], overrides={})
        result = runner.invoke(
            app,
            ["images", "--install-dir", str(tmp_path), "--image-tag", "registry=FIX"],
        )
        assert result.exit_code == 0, result.output
        assert "→ ghcr.io/t/registry:FIX" in result.output

    def test_bad_format_exits_2(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=[], overrides={})
        result = runner.invoke(
            app, ["images", "--install-dir", str(tmp_path), "--format", "xml"]
        )
        assert result.exit_code == 2


class TestRedeployKeepsApps:
    """`redeploy --tag` pins apps the new base tag alone would move."""

    def _redeploy(self, install_dir: Path, *args: str):  # type: ignore[no-untyped-def]
        from unittest.mock import patch

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = runner.invoke(
                app, ["redeploy", "--install-dir", str(install_dir), *args]
            )
        return result, mutate

    def test_unpinned_app_is_pinned_to_its_current_tag(self, tmp_path: Path) -> None:
        _write_state(
            tmp_path, apps=["react-console", "wip-kb"], overrides={"wip-kb": "kb-1"}
        )
        result, mutate = self._redeploy(tmp_path, "--tag", "NEW")
        assert result.exit_code == 0, result.output
        deployment = mutate.call_args.args[0]
        assert deployment.spec.images.tag == "NEW"
        assert deployment.spec.images.tag_overrides == {
            "wip-kb": "kb-1",
            "react-console": "OLD",
        }
        assert "app 'react-console' stays on 'OLD'" in result.output
        assert "react-console=OLD" in mutate.call_args.args[4]

    def test_explicit_image_tag_moves_the_app(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=["react-console"], overrides={})
        result, mutate = self._redeploy(
            tmp_path, "--tag", "NEW", "--image-tag", "react-console=rc-2"
        )
        assert result.exit_code == 0, result.output
        overrides = mutate.call_args.args[0].spec.images.tag_overrides
        assert overrides == {"react-console": "rc-2"}
        assert "stays on" not in result.output

    def test_unchanged_tag_pins_nothing(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=["react-console"], overrides={})
        result, mutate = self._redeploy(tmp_path, "--tag", "OLD")
        assert result.exit_code == 0, result.output
        assert mutate.call_args.args[0].spec.images.tag_overrides == {}

    def test_image_tag_alone_pins_nothing_else(self, tmp_path: Path) -> None:
        _write_state(tmp_path, apps=["react-console"], overrides={})
        result, mutate = self._redeploy(tmp_path, "--image-tag", "registry=FIX")
        assert result.exit_code == 0, result.output
        assert mutate.call_args.args[0].spec.images.tag_overrides == {"registry": "FIX"}
