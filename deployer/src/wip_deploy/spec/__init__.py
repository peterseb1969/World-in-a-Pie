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
    DexUser,
    ImagesSpec,
    K8sPlatform,
    ModulesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

__all__ = [
    # Deployment
    "Deployment",
    "DeploymentMetadata",
    "DeploymentSpec",
    "ModulesSpec",
    "AppRef",
    "AuthSpec",
    "DexUser",
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
    "PostInstallHook",
    "ObservabilitySpec",
    # App
    "App",
    "AppMetadata",
]
