"""Shared fixtures for config_gen tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.config_gen import SpecContext, make_spec_context
from wip_deploy.discovery import Discovery, discover
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    """All real manifests loaded once per session."""
    return discover(REPO_ROOT)


@pytest.fixture
def compose_deployment() -> Deployment:
    """Typical compose deployment — standard-preset-like (OIDC + gateway
    + mcp-server). No apps, no reporting/ingest/files."""
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/test", tag="test"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


@pytest.fixture
def k8s_deployment() -> Deployment:
    """Typical k8s deployment — mirrors compose_deployment at a different
    target. Uses 443/80 to match nginx-ingress's standard ports."""
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="k8s",
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip-kubi.local", https_port=443, http_port=80),
            images=ImagesSpec(registry="ghcr.io/test", tag="test"),
            platform=PlatformSpec(k8s=K8sPlatform()),
            secrets=SecretsSpec(backend="k8s-secret"),
        ),
    )


@pytest.fixture
def maximal_compose_deployment(real_discovery: Discovery) -> Deployment:
    """Compose deployment with every optional module + every app active."""
    optional = sorted(
        c.metadata.name
        for c in real_discovery.components
        if c.metadata.category == "optional"
    )
    app_names = sorted(a.metadata.name for a in real_discovery.apps)
    return Deployment(
        metadata=DeploymentMetadata(name="maximal"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": optional},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in app_names],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/test", tag="test"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


@pytest.fixture
def ctx_compose(compose_deployment: Deployment, real_discovery: Discovery) -> SpecContext:
    return make_spec_context(compose_deployment, real_discovery.components)


@pytest.fixture
def ctx_k8s(k8s_deployment: Deployment, real_discovery: Discovery) -> SpecContext:
    return make_spec_context(k8s_deployment, real_discovery.components)
