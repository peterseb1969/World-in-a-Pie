"""Shared fixtures for spec-layer tests."""

from __future__ import annotations

import pytest

from wip_deploy.spec import (
    AuthSpec,
    Component,
    ComponentMetadata,
    ComponentSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImageRef,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    Port,
    SecretsSpec,
)


@pytest.fixture
def minimal_compose_deployment() -> Deployment:
    """A valid minimal compose deployment. Tests that need to tweak a
    single field should copy this."""
    return Deployment(
        metadata=DeploymentMetadata(name="test"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/data")),
            secrets=SecretsSpec(backend="file", location="/tmp/secrets"),
        ),
    )


@pytest.fixture
def minimal_k8s_deployment() -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="test"),
        spec=DeploymentSpec(
            target="k8s",
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip-kubi.local"),
            platform=PlatformSpec(k8s=K8sPlatform()),
            secrets=SecretsSpec(backend="k8s-secret"),
        ),
    )


def make_component(
    name: str,
    category: str = "optional",
    **spec_kwargs: object,
) -> Component:
    """Component factory for tests."""
    spec_kwargs.setdefault("image", ImageRef(name=f"wip-{name}"))
    spec_kwargs.setdefault("ports", [Port(name="http", container_port=8000)])
    return Component(
        metadata=ComponentMetadata(
            name=name,
            category=category,  # type: ignore[arg-type]
            description=f"Test component {name}",
        ),
        spec=ComponentSpec(**spec_kwargs),  # type: ignore[arg-type]
    )
