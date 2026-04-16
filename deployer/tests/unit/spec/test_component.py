"""Tests for Component + nested models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from wip_deploy.spec import (
    EnvSource,
    HealthcheckSpec,
    ImageRef,
    OidcClientSpec,
    Port,
    Route,
    StorageSpec,
)
from wip_deploy.spec.component import Component, ComponentSpec

from .conftest import make_component

# ────────────────────────────────────────────────────────────────────
# EnvSource — exactly-one constraint
# ────────────────────────────────────────────────────────────────────


class TestEnvSource:
    def test_literal_alone_is_valid(self) -> None:
        EnvSource(literal="hello")

    def test_from_spec_alone_is_valid(self) -> None:
        EnvSource(from_spec="auth.issuer_url")

    def test_from_secret_alone_is_valid(self) -> None:
        EnvSource(from_secret="api-key")

    def test_from_component_alone_is_valid(self) -> None:
        EnvSource(from_component="mongodb")

    def test_no_source_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="exactly one"):
            EnvSource()

    def test_two_sources_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="exactly one"):
            EnvSource(literal="x", from_component="mongodb")

    def test_all_four_sources_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="exactly one"):
            EnvSource(
                literal="x",
                from_spec="y",
                from_secret="z",
                from_component="w",
            )


# ────────────────────────────────────────────────────────────────────
# Port
# ────────────────────────────────────────────────────────────────────


class TestPort:
    def test_valid_port(self) -> None:
        p = Port(name="http", container_port=8004)
        assert p.protocol == "TCP"

    def test_port_out_of_range_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Port(name="http", container_port=70000)

    def test_port_zero_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Port(name="http", container_port=0)


# ────────────────────────────────────────────────────────────────────
# Route
# ────────────────────────────────────────────────────────────────────


class TestRoute:
    def test_valid_route(self) -> None:
        r = Route(path="/api/document-store")
        assert r.auth_required is True
        assert r.streaming is False

    def test_path_must_start_with_slash(self) -> None:
        with pytest.raises(PydanticValidationError):
            Route(path="api/document-store")

    def test_streaming_route(self) -> None:
        r = Route(path="/api/document-store", streaming=True)
        assert r.streaming is True


# ────────────────────────────────────────────────────────────────────
# Storage
# ────────────────────────────────────────────────────────────────────


class TestStorage:
    def test_defaults(self) -> None:
        s = StorageSpec(name="data", mount_path="/data")
        assert s.size == "10Gi"
        assert s.access_mode == "ReadWriteOnce"

    def test_mount_path_must_start_with_slash(self) -> None:
        with pytest.raises(PydanticValidationError):
            StorageSpec(name="data", mount_path="data")


# ────────────────────────────────────────────────────────────────────
# Healthcheck
# ────────────────────────────────────────────────────────────────────


class TestHealthcheck:
    def test_defaults(self) -> None:
        h = HealthcheckSpec(endpoint="/health")
        assert h.interval_seconds == 10
        assert h.retries == 3

    def test_endpoint_must_start_with_slash(self) -> None:
        with pytest.raises(PydanticValidationError):
            HealthcheckSpec(endpoint="health")


# ────────────────────────────────────────────────────────────────────
# OIDC client
# ────────────────────────────────────────────────────────────────────


class TestOidcClient:
    def test_defaults(self) -> None:
        o = OidcClientSpec(client_id="foo")
        assert o.redirect_paths == ["/auth/callback"]

    def test_empty_client_id_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            OidcClientSpec(client_id="")


# ────────────────────────────────────────────────────────────────────
# ImageRef
# ────────────────────────────────────────────────────────────────────


class TestImageRef:
    def test_build_from_source(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        i = ImageRef(name="wip-foo", build_context=tmp_path)
        assert i.build_context == tmp_path

    def test_pre_built_image(self) -> None:
        i = ImageRef(name="wip-foo")
        assert i.build_context is None

    def test_name_must_be_lowercase_dns(self) -> None:
        with pytest.raises(PydanticValidationError):
            ImageRef(name="WIP-Foo")


# ────────────────────────────────────────────────────────────────────
# Component-level uniqueness
# ────────────────────────────────────────────────────────────────────


class TestComponentSpecUniqueness:
    def test_duplicate_port_names_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="duplicate port names"):
            ComponentSpec(
                image=ImageRef(name="wip-foo"),
                ports=[
                    Port(name="http", container_port=80),
                    Port(name="http", container_port=81),
                ],
            )

    def test_duplicate_storage_names_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="duplicate storage names"):
            ComponentSpec(
                image=ImageRef(name="wip-foo"),
                storage=[
                    StorageSpec(name="data", mount_path="/data"),
                    StorageSpec(name="data", mount_path="/data2"),
                ],
            )

    def test_duplicate_route_paths_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="duplicate route paths"):
            ComponentSpec(
                image=ImageRef(name="wip-foo"),
                routes=[
                    Route(path="/api/x"),
                    Route(path="/api/x"),
                ],
            )


# ────────────────────────────────────────────────────────────────────
# Full Component
# ────────────────────────────────────────────────────────────────────


class TestComponent:
    def test_factory_minimal_component(self) -> None:
        c = make_component("foo")
        assert c.api_version == "wip.dev/v1"
        assert c.kind == "Component"
        assert c.metadata.category == "optional"

    def test_name_must_be_lowercase_dns(self) -> None:
        with pytest.raises(PydanticValidationError):
            make_component("Foo")

    def test_realistic_document_store_like_manifest(self) -> None:
        """Smoke test: a manifest resembling what document-store would have."""
        c = Component(
            metadata={
                "name": "document-store",
                "category": "core",
                "description": "Document storage with template-based validation",
            },  # type: ignore[arg-type]
            spec={
                "image": {"name": "wip-document-store", "build_context": "."},
                "ports": [{"name": "http", "container_port": 8004}],
                "env": {
                    "required": [
                        {
                            "name": "MONGO_URI",
                            "source": {"from_component": "mongodb"},
                        },
                        {
                            "name": "WIP_API_KEY",
                            "source": {"from_secret": "api-key"},
                        },
                    ],
                },
                "routes": [
                    {
                        "path": "/api/document-store",
                        "auth_required": True,
                        "streaming": True,
                    },
                ],
                "depends_on": ["mongodb", "registry", "template-store"],
                "healthcheck": {"endpoint": "/health"},
            },  # type: ignore[arg-type]
        )
        assert c.metadata.name == "document-store"
        assert c.spec.routes[0].streaming is True
