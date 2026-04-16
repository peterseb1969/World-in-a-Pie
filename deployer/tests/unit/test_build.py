"""Tests for build_deployment — preset + flags → Deployment."""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.build import BuildInputs, build_deployment


def _minimal_compose_inputs(**overrides: object) -> BuildInputs:
    base: dict[str, object] = {
        "name": "test",
        "preset": "standard",
        "target": "compose",
        "hostname": "wip.local",
        "tls": "internal",
        "compose_data_dir": Path("/tmp/wip-data"),
    }
    base.update(overrides)
    return BuildInputs(**base)  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────────
# Presets apply correctly
# ────────────────────────────────────────────────────────────────────


class TestPresetApplies:
    def test_standard_has_oidc_and_gateway(self) -> None:
        d = build_deployment(_minimal_compose_inputs(preset="standard"))
        assert d.spec.auth.mode == "oidc"
        assert d.spec.auth.gateway is True

    def test_headless_has_api_key_only_and_no_gateway(self) -> None:
        d = build_deployment(_minimal_compose_inputs(preset="headless"))
        assert d.spec.auth.mode == "api-key-only"
        assert d.spec.auth.gateway is False

    def test_full_enables_reporting_files_ingest(self) -> None:
        d = build_deployment(_minimal_compose_inputs(preset="full"))
        opt = set(d.spec.modules.optional)
        assert {"reporting-sync", "ingest-gateway", "minio"} <= opt

    def test_unknown_preset_raises_keyerror(self) -> None:
        with pytest.raises(KeyError, match="unknown preset"):
            build_deployment(_minimal_compose_inputs(preset="nosuch"))


# ────────────────────────────────────────────────────────────────────
# --add / --remove
# ────────────────────────────────────────────────────────────────────


class TestAddRemove:
    def test_add_appends_to_optional(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(
                preset="standard", add=["reporting-sync", "minio"]
            )
        )
        opt = set(d.spec.modules.optional)
        assert "reporting-sync" in opt
        assert "minio" in opt

    def test_remove_drops_from_optional(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(preset="full", remove=["ingest-gateway"])
        )
        opt = set(d.spec.modules.optional)
        assert "ingest-gateway" not in opt

    def test_add_is_idempotent(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(preset="standard", add=["console", "console"])
        )
        # console already in standard; add should not duplicate
        assert d.spec.modules.optional.count("console") == 1


# ────────────────────────────────────────────────────────────────────
# Target / platform
# ────────────────────────────────────────────────────────────────────


class TestPlatform:
    def test_compose_platform_populated(self) -> None:
        d = build_deployment(_minimal_compose_inputs())
        assert d.spec.platform.compose is not None
        assert d.spec.platform.compose.data_dir == Path("/tmp/wip-data")
        assert d.spec.platform.k8s is None

    def test_k8s_platform_populated(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(
                target="k8s",
                compose_data_dir=None,
                hostname="wip-kubi.local",
                secrets_backend="k8s-secret",
            )
        )
        assert d.spec.platform.k8s is not None
        assert d.spec.platform.k8s.storage_class == "rook-ceph-block"
        assert d.spec.platform.compose is None

    def test_dev_platform_populated(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(
                target="dev",
                compose_data_dir=None,
                dev_mode="simple",
            )
        )
        assert d.spec.platform.dev is not None
        assert d.spec.platform.dev.mode == "simple"

    def test_compose_without_data_dir_rejected(self) -> None:
        with pytest.raises(ValueError, match="data_dir"):
            build_deployment(
                _minimal_compose_inputs(compose_data_dir=None)
            )


# ────────────────────────────────────────────────────────────────────
# Secrets default by target
# ────────────────────────────────────────────────────────────────────


class TestSecrets:
    def test_compose_defaults_to_file_backend(self) -> None:
        d = build_deployment(_minimal_compose_inputs())
        assert d.spec.secrets.backend == "file"
        assert d.spec.secrets.location is not None
        assert "wip-deploy" in d.spec.secrets.location

    def test_k8s_defaults_to_k8s_secret_backend(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(target="k8s", compose_data_dir=None)
        )
        assert d.spec.secrets.backend == "k8s-secret"

    def test_explicit_sops_on_compose(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(
                secrets_backend="sops",
                secrets_location="/etc/wip/secrets.sops.yaml",
            )
        )
        assert d.spec.secrets.backend == "sops"
        assert d.spec.secrets.location == "/etc/wip/secrets.sops.yaml"


# ────────────────────────────────────────────────────────────────────
# Auth overrides
# ────────────────────────────────────────────────────────────────────


class TestAuthOverrides:
    def test_disable_gateway_via_flag(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(preset="standard", auth_gateway=False)
        )
        assert d.spec.auth.gateway is False
        assert d.spec.auth.mode == "oidc"  # unchanged

    def test_switch_to_api_key_only_clears_users(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(
                preset="standard",
                auth_mode="api-key-only",
                auth_gateway=False,
            )
        )
        assert d.spec.auth.mode == "api-key-only"
        assert d.spec.auth.gateway is False
        assert d.spec.auth.users == []


# ────────────────────────────────────────────────────────────────────
# Apps
# ────────────────────────────────────────────────────────────────────


class TestApps:
    def test_apps_cli_flag_populates_spec(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(apps=["dnd", "clintrial"])
        )
        names = [a.name for a in d.spec.apps]
        assert names == ["dnd", "clintrial"]


# ────────────────────────────────────────────────────────────────────
# Images
# ────────────────────────────────────────────────────────────────────


class TestImages:
    def test_registry_and_tag_propagate(self) -> None:
        d = build_deployment(
            _minimal_compose_inputs(
                registry="ghcr.io/peterseb1969",
                tag="v2.0.0",
            )
        )
        assert d.spec.images.registry == "ghcr.io/peterseb1969"
        assert d.spec.images.tag == "v2.0.0"

    def test_tag_defaults_to_latest(self) -> None:
        d = build_deployment(_minimal_compose_inputs())
        assert d.spec.images.tag == "latest"
