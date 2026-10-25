"""Pydantic models for the wip-deploy declarative spec.

The Deployment spec describes user intent (target, modules, apps, auth,
network). The Component manifest describes per-service deployment shape
(image, ports, env, routes, healthcheck). Apps are Components with extra
metadata (route prefix, display name).

All models forbid unknown fields — typos in manifest YAML surface as
validation errors at load time rather than silent misbehavior downstream.
"""

from wip_deploy.spec.app import App, AppMetadata
from wip_deploy.spec.component import (
    ActivationSpec,
    Component,
    ComponentMetadata,
    ComponentSpec,
    EnvSource,
    EnvSpec,
    EnvVar,
    HealthcheckSpec,
    ImageRef,
    MongoClientSpec,
    NatsClientSpec,
    ObservabilitySpec,
    OidcClientSpec,
    Port,
    PostInstallHook,
    ResourceSpec,
    Route,
    StorageSpec,
)
from wip_deploy.spec.deployment import (
    ApplySpec,
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    DexConnector,
    DexUser,
    GroupMapping,
    ImagesSpec,
    K8sPlatform,
    ModulesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

# Grouped by model family for readability rather than alpha-sorted; RUF022
# would flatten the grouping (same as config_gen/__init__.py).
__all__ = [  # noqa: RUF022
    # Deployment
    "Deployment",
    "DeploymentMetadata",
    "DeploymentSpec",
    "ModulesSpec",
    "AppRef",
    "AuthSpec",
    "DexConnector",
    "DexUser",
    "GroupMapping",
    "NetworkSpec",
    "ImagesSpec",
    "PlatformSpec",
    "ComposePlatform",
    "K8sPlatform",
    "DevPlatform",
    "SecretsSpec",
    "ApplySpec",
    # Component
    "Component",
    "ComponentMetadata",
    "ComponentSpec",
    "ActivationSpec",
    "ImageRef",
    "Port",
    "EnvSpec",
    "EnvVar",
    "EnvSource",
    "Route",
    "StorageSpec",
    "HealthcheckSpec",
    "ResourceSpec",
    "OidcClientSpec",
    "NatsClientSpec",
    "MongoClientSpec",
    "PostInstallHook",
    "ObservabilitySpec",
    # App
    "App",
    "AppMetadata",
]
