"""CASE-438: CLI image tag is authoritative; manifest pins are fallbacks.

Covers the shared resolver (`config_gen.images.image_ref`), the
`--image-tag NAME=TAG` CLI parsing, and the end-to-end spec plumbing
through show-spec. Precedence under test, for WIP-built (short-name)
images:

    tag_overrides[name] > spec.images.tag > manifest pin > "latest"

Fully-qualified images (mongo, postgres, …) skip the deployment-wide
tag — their pins are upstream version numbers — but still honor an
explicit tag_overrides entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wip_deploy.cli import _parse_image_tags, app
from wip_deploy.config_gen.images import image_ref
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

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()

runner = CliRunner()


def _deployment(images: ImagesSpec) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="api-key-only", gateway=False, users=[]),
            network=NetworkSpec(hostname="wip.local"),
            images=images,
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _component(name: str, image_name: str, pin: str | None) -> Component:
    return Component(
        metadata=ComponentMetadata(
            name=name, category="core", description="t"
        ),
        spec=ComponentSpec(image=ImageRef(name=image_name, tag=pin)),
    )


def _app(name: str, pin: str | None) -> App:
    return App(
        metadata=ComponentMetadata(
            name=name, category="optional", description="t"
        ),
        spec=ComponentSpec(image=ImageRef(name=name, tag=pin)),
        app_metadata=AppMetadata(
            display_name="T", route_prefix=f"/apps/{name}"
        ),
    )


# ────────────────────────────────────────────────────────────────────
# Resolver precedence — WIP-built (short-name) images
# ────────────────────────────────────────────────────────────────────


class TestShortNamePrecedence:
    def test_explicit_tag_overrides_manifest_pin(self) -> None:
        """The case's headline bug: --tag X must beat the pin."""
        d = _deployment(ImagesSpec(registry="ghcr.io/test", tag="X"))
        c = _component("registry", "registry", pin="20260601")
        assert image_ref(c, d) == "ghcr.io/test/registry:X"

    def test_no_tag_falls_back_to_manifest_pin(self) -> None:
        d = _deployment(ImagesSpec(registry="ghcr.io/test", tag=None))
        c = _component("registry", "registry", pin="20260601")
        assert image_ref(c, d) == "ghcr.io/test/registry:20260601"

    def test_no_tag_no_pin_falls_back_to_latest(self) -> None:
        d = _deployment(ImagesSpec(registry="ghcr.io/test", tag=None))
        c = _component("def-store", "def-store", pin=None)
        assert image_ref(c, d) == "ghcr.io/test/def-store:latest"

    def test_override_beats_explicit_tag_and_pin(self) -> None:
        d = _deployment(
            ImagesSpec(
                registry="ghcr.io/test",
                tag="X",
                tag_overrides={"registry": "hotfix-1"},
            )
        )
        c = _component("registry", "registry", pin="20260601")
        assert image_ref(c, d) == "ghcr.io/test/registry:hotfix-1"

    def test_override_only_touches_the_named_service(self) -> None:
        d = _deployment(
            ImagesSpec(
                registry="ghcr.io/test",
                tag="X",
                tag_overrides={"registry": "hotfix-1"},
            )
        )
        other = _component("def-store", "def-store", pin=None)
        assert image_ref(other, d) == "ghcr.io/test/def-store:X"

    def test_no_registry_renders_bare_name(self) -> None:
        d = _deployment(ImagesSpec(registry=None, tag="X"))
        c = _component("registry", "registry", pin="20260601")
        assert image_ref(c, d) == "registry:X"

    def test_apps_resolve_like_components(self) -> None:
        d = _deployment(ImagesSpec(registry="ghcr.io/test", tag="X"))
        a = _app("wip-kb", pin="20260601")
        assert image_ref(a, d) == "ghcr.io/test/wip-kb:X"


# ────────────────────────────────────────────────────────────────────
# Resolver precedence — fully-qualified (infra) images
# ────────────────────────────────────────────────────────────────────


class TestFullyQualifiedPrecedence:
    def test_global_tag_does_not_touch_infra(self) -> None:
        """`--tag 20260610a` must not render mongo:20260610a."""
        d = _deployment(ImagesSpec(registry="ghcr.io/test", tag="20260610a"))
        c = _component("mongodb", "docker.io/library/mongo", pin="7")
        assert image_ref(c, d) == "docker.io/library/mongo:7"

    def test_override_beats_infra_pin(self) -> None:
        d = _deployment(
            ImagesSpec(
                registry="ghcr.io/test",
                tag="20260610a",
                tag_overrides={"mongodb": "8"},
            )
        )
        c = _component("mongodb", "docker.io/library/mongo", pin="7")
        assert image_ref(c, d) == "docker.io/library/mongo:8"

    def test_infra_without_pin_defaults_to_latest(self) -> None:
        d = _deployment(ImagesSpec(registry="ghcr.io/test", tag="X"))
        c = _component("minio", "docker.io/minio/minio", pin=None)
        assert image_ref(c, d) == "docker.io/minio/minio:latest"


# ────────────────────────────────────────────────────────────────────
# CLI parsing — --image-tag NAME=TAG
# ────────────────────────────────────────────────────────────────────


class TestParseImageTags:
    def test_parses_multiple_entries(self) -> None:
        assert _parse_image_tags(
            ["registry=hotfix-1", "wip-kb=20260611"]
        ) == {"registry": "hotfix-1", "wip-kb": "20260611"}

    def test_empty_list_gives_empty_dict(self) -> None:
        assert _parse_image_tags([]) == {}

    def test_missing_equals_raises(self) -> None:
        with pytest.raises(ValueError, match="missing '='"):
            _parse_image_tags(["registry-hotfix"])

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty NAME and TAG"):
            _parse_image_tags(["=hotfix"])

    def test_empty_tag_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty NAME and TAG"):
            _parse_image_tags(["registry="])


# ────────────────────────────────────────────────────────────────────
# End-to-end CLI plumbing — show-spec
# ────────────────────────────────────────────────────────────────────


class TestShowSpecPlumbing:
    def test_image_tag_lands_in_spec(self) -> None:
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "compose",
                "--hostname", "wip.local",
                "--tag", "20260610a",
                "--image-tag", "registry=hotfix-1",
                "--format", "json",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.output)
        assert parsed["spec"]["images"]["tag"] == "20260610a"
        assert parsed["spec"]["images"]["tag_overrides"] == {
            "registry": "hotfix-1"
        }

    def test_no_tag_leaves_spec_tag_null(self) -> None:
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "compose",
                "--hostname", "wip.local",
                "--format", "json",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        parsed = json.loads(r.output)
        assert parsed["spec"]["images"]["tag"] is None

    def test_malformed_image_tag_exits_2(self) -> None:
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "compose",
                "--hostname", "wip.local",
                "--image-tag", "registry",
                "--repo-root", str(REPO_ROOT),
            ],
        )
        assert r.exit_code == 2, r.output
