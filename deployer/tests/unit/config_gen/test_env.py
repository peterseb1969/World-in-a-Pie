"""Tests for env resolution.

Cross-target tests are the load-bearing ones: same spec, different
targets, different resolved URLs / hostnames.
"""

from __future__ import annotations

import pytest

from wip_deploy.config_gen import (
    Literal,
    SecretRef,
    SpecContext,
    make_spec_context,
    resolve_all_env,
    resolve_component_env,
    resolve_env_source,
)
from wip_deploy.discovery import Discovery
from wip_deploy.spec import Deployment, EnvSource, ImageRef
from wip_deploy.spec.component import Component, ComponentMetadata, ComponentSpec

# ────────────────────────────────────────────────────────────────────
# Per-EnvSource resolution
# ────────────────────────────────────────────────────────────────────


class TestResolveEnvSource:
    def test_literal(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(literal="hello"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("hello")

    def test_from_spec(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_spec="auth.issuer_url_public"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("https://wip.local:8443/dex")

    def test_from_secret_stays_a_ref(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_secret="api-key"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == SecretRef("api-key")


class TestFromComponentCompose:
    def test_mongodb_url_uses_mongodb_scheme(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component="mongodb"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("mongodb://wip-mongodb:27017/")

    def test_postgres_url_uses_postgresql_scheme(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component="postgres"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("postgresql://wip-postgres:5432/")

    def test_nats_url(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component="nats"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("nats://wip-nats:4222")

    def test_http_service_defaults_to_http_scheme(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component="registry"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("http://wip-registry:8001")


class TestFromComponentK8s:
    def test_mongodb_uses_cluster_dns(
        self,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
        ctx_k8s: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component="mongodb"),
            deployment=k8s_deployment,
            ctx=ctx_k8s,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("mongodb://wip-mongodb.wip.svc.cluster.local:27017/")

    def test_http_service_uses_cluster_dns(
        self,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
        ctx_k8s: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component="registry"),
            deployment=k8s_deployment,
            ctx=ctx_k8s,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("http://wip-registry.wip.svc.cluster.local:8001")


class TestFromComponentHostAndPort:
    def test_from_component_host_compose(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component_host="postgres"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("wip-postgres")

    def test_from_component_host_k8s(
        self,
        k8s_deployment: Deployment,
        real_discovery: Discovery,
        ctx_k8s: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component_host="postgres"),
            deployment=k8s_deployment,
            ctx=ctx_k8s,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("wip-postgres.wip.svc.cluster.local")

    def test_from_component_port(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        v = resolve_env_source(
            EnvSource(from_component_port="postgres"),
            deployment=compose_deployment,
            ctx=ctx_compose,
            components_by_name=by_name,
            namespace="wip",
        )
        assert v == Literal("5432")


class TestEnvResolutionErrors:
    def test_from_component_unknown_raises(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        by_name = {c.metadata.name: c for c in real_discovery.components}
        with pytest.raises(KeyError, match="imaginary"):
            resolve_env_source(
                EnvSource(from_component="imaginary"),
                deployment=compose_deployment,
                ctx=ctx_compose,
                components_by_name=by_name,
                namespace="wip",
            )

    def test_from_component_with_no_ports_raises(
        self,
        compose_deployment: Deployment,
        ctx_compose: SpecContext,
    ) -> None:
        # Synthetic component with zero ports
        portless = Component(
            metadata=ComponentMetadata(
                name="portless",
                category="infrastructure",
                description="no ports",
            ),
            spec=ComponentSpec(image=ImageRef(name="wip-portless")),
        )
        with pytest.raises(ValueError, match="has no ports"):
            resolve_env_source(
                EnvSource(from_component="portless"),
                deployment=compose_deployment,
                ctx=ctx_compose,
                components_by_name={"portless": portless},
                namespace="wip",
            )


# ────────────────────────────────────────────────────────────────────
# Full-component resolution
# ────────────────────────────────────────────────────────────────────


class TestResolveComponentEnv:
    def test_real_document_store_resolves_cleanly(
        self,
        compose_deployment: Deployment,
        real_discovery: Discovery,
        ctx_compose: SpecContext,
    ) -> None:
        doc_store = next(
            c for c in real_discovery.components if c.metadata.name == "document-store"
        )
        env = resolve_component_env(
            doc_store,
            compose_deployment,
            ctx_compose,
            real_discovery.components,
            real_discovery.apps,
        )
        # Some required values
        assert env.required["MONGO_URI"] == Literal("mongodb://wip-mongodb:27017/")
        assert env.required["REGISTRY_URL"] == Literal("http://wip-registry:8001")
        assert env.required["TEMPLATE_STORE_URL"] == Literal(
            "http://wip-template-store:8003"
        )
        assert env.required["MASTER_API_KEY"] == SecretRef("api-key")

    def test_real_reporting_sync_postgres_host_port(
        self,
        real_discovery: Discovery,
        maximal_compose_deployment: Deployment,
    ) -> None:
        # Use maximal so reporting-sync is active and its deps exist
        ctx = make_spec_context(
            maximal_compose_deployment, real_discovery.components
        )
        rs = next(
            c for c in real_discovery.components if c.metadata.name == "reporting-sync"
        )
        env = resolve_component_env(
            rs,
            maximal_compose_deployment,
            ctx,
            real_discovery.components,
            real_discovery.apps,
        )
        assert env.required["POSTGRES_HOST"] == Literal("wip-postgres")
        assert env.required["POSTGRES_PORT"] == Literal("5432")
        assert env.required["POSTGRES_PASSWORD"] == SecretRef("postgres-password")
        # Full URL for NATS
        assert env.required["NATS_URL"] == Literal("nats://wip-nats:4222")


class TestResolveAllEnv:
    def test_all_components_resolve(
        self,
        maximal_compose_deployment: Deployment,
        real_discovery: Discovery,
    ) -> None:
        """End-to-end: every real component+app's env resolves without error."""
        ctx = make_spec_context(
            maximal_compose_deployment, real_discovery.components
        )
        resolved = resolve_all_env(
            maximal_compose_deployment,
            real_discovery.components,
            real_discovery.apps,
            ctx,
        )
        # Every component and app represented
        for c in real_discovery.components:
            assert c.metadata.name in resolved
        for a in real_discovery.apps:
            assert a.metadata.name in resolved
