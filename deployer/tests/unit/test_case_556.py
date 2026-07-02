"""CASE-556: `--image-tag NAME=TAG` with a key matching no enabled
component/app was silently discarded — image_ref() looks overrides up
per rendered owner, so an unmatched key is never consulted and the
service renders at the base `--tag`, deploying a different version than
intended.

Covers `_warn_unmatched_tag_overrides` (called from `_assemble` after
discovery): matched keys stay silent; unmatched keys warn on stderr
(CASE-366 channel discipline) listing the valid key set; a key that
matches a *declared but not enabled* owner gets the "forgot --app/--add?"
hint — the exact operator mistake from the case report.
"""

from __future__ import annotations

import pytest

from wip_deploy.cli import _warn_unmatched_tag_overrides
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
from wip_deploy.spec.deployment import AppRef


def _deployment(
    tag_overrides: dict[str, str],
    *,
    apps: list[AppRef] | None = None,
    optional: list[str] | None = None,
) -> Deployment:
    spec: dict = {
        "target": "compose",
        "auth": AuthSpec(mode="api-key-only", gateway=False, users=[]),
        "network": NetworkSpec(hostname="wip.local"),
        "images": ImagesSpec(tag="base", tag_overrides=tag_overrides),
        "platform": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "secrets": SecretsSpec(backend="file", location="/tmp/s"),
        "apps": apps or [],
    }
    if optional is not None:
        spec["modules"] = {"optional": optional}
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec.model_validate(spec),
    )


def _component(name: str, category: str = "core") -> Component:
    return Component(
        metadata=ComponentMetadata(name=name, category=category, description="t"),
        spec=ComponentSpec(image=ImageRef(name=name, tag=None)),
    )


def _app(name: str) -> App:
    return App(
        metadata=ComponentMetadata(name=name, category="optional", description="t"),
        spec=ComponentSpec(image=ImageRef(name=name, tag=None)),
        app_metadata=AppMetadata(display_name="T", route_prefix=f"/apps/{name}"),
    )


class TestNoWarning:
    def test_no_overrides_is_silent(self, capsys: pytest.CaptureFixture) -> None:
        d = _deployment({})
        _warn_unmatched_tag_overrides(d, [_component("registry")], [])
        assert capsys.readouterr().err == ""

    def test_override_matching_active_component_is_silent(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        d = _deployment({"registry": "hotfix-1"})
        _warn_unmatched_tag_overrides(d, [_component("registry")], [])
        assert capsys.readouterr().err == ""

    def test_override_matching_enabled_app_is_silent(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        d = _deployment({"wip-kb": "20260629a"}, apps=[AppRef(name="wip-kb")])
        _warn_unmatched_tag_overrides(d, [], [_app("wip-kb")])
        assert capsys.readouterr().err == ""


class TestUnmatchedWarns:
    def test_unknown_key_warns_and_lists_valid_keys(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """The comment's scenario: `kb` instead of `wip-kb` — a typo'd key."""
        d = _deployment({"kb": "20260629a"}, apps=[AppRef(name="wip-kb")])
        _warn_unmatched_tag_overrides(
            d, [_component("registry")], [_app("wip-kb")]
        )
        err = capsys.readouterr().err
        assert "--image-tag 'kb' matches no enabled component/app" in err
        assert "override ignored" in err
        assert "valid --image-tag keys" in err
        assert "wip-kb" in err and "registry" in err
        # a pure typo gets no forgot---app hint
        assert "forgot --app" not in err

    def test_declared_but_not_enabled_gets_forgot_app_hint(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """The case's headline scenario: --image-tag wip-kb=… without --app
        wip-kb. The app manifest exists (discovered) but isn't enabled."""
        d = _deployment({"wip-kb": "20260629a"}, apps=[])
        _warn_unmatched_tag_overrides(
            d, [_component("registry")], [_app("wip-kb")]
        )
        err = capsys.readouterr().err
        assert "--image-tag 'wip-kb' matches no enabled component/app" in err
        assert "forgot --app/--add?" in err

    def test_optional_component_not_added_gets_hint(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        d = _deployment({"mcp-server": "x"}, optional=[])
        _warn_unmatched_tag_overrides(
            d, [_component("mcp-server", category="optional")], []
        )
        err = capsys.readouterr().err
        assert "matches no enabled component/app" in err
        assert "forgot --app/--add?" in err

    def test_matched_and_unmatched_mix_warns_only_unmatched(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        d = _deployment({"registry": "a", "nope": "b"})
        _warn_unmatched_tag_overrides(d, [_component("registry")], [])
        err = capsys.readouterr().err
        assert "'nope'" in err
        assert "'registry'" not in err
