"""Construct a validated Deployment from (preset + CLI flags).

The CLI takes preset and flag values, this module turns them into a typed
`Deployment`. Kept separate from `cli.py` so it's unit-testable without a
CliRunner.

Merge semantics:
  - Start from the preset (partial DeploymentSpec dict)
  - Overlay CLI-provided values on top
  - Apply `add` / `remove` to `modules.optional`
  - Populate `target`, `network`, `platform`, `secrets` from flags
  - Fill Pydantic defaults for anything else
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wip_deploy.presets import get_preset
from wip_deploy.spec import (
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
)


@dataclass
class BuildInputs:
    """All user-supplied values needed to construct a Deployment.

    Populated by the CLI layer; consumed by `build_deployment`.
    """

    # Identity
    name: str = "default"
    preset: str = "standard"

    # Target + network
    target: str = "compose"
    hostname: str = "wip.local"
    tls: str = "internal"
    # None → target-aware defaults: 443 for k8s (nginx-ingress LoadBalancer),
    # 8443 for compose/dev (Caddy as non-root). Explicit ports pass through.
    https_port: int | None = None
    http_port: int | None = None

    # Compose platform
    compose_data_dir: Path | None = None
    compose_platform_variant: str = "default"

    # K8s platform
    k8s_namespace: str = "wip"
    k8s_storage_class: str = "rook-ceph-block"
    k8s_ingress_class: str = "nginx"
    k8s_tls_secret_name: str = "wip-tls"

    # Dev platform
    dev_mode: str = "tilt"

    # Images
    registry: str | None = None
    tag: str = "latest"

    # Modules / apps
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)

    # Auth overrides
    auth_mode: str | None = None
    auth_gateway: bool | None = None

    # Secrets
    secrets_backend: str | None = None
    secrets_location: str | None = None


def build_deployment(inputs: BuildInputs) -> Deployment:
    """Construct a Deployment. Raises pydantic.ValidationError on bad
    combinations; raises KeyError on unknown preset name."""
    preset = get_preset(inputs.preset)

    spec_dict: dict[str, Any] = {}

    # Deep-copy relevant preset sections.
    spec_dict["modules"] = {
        "optional": list(preset.get("modules", {}).get("optional", []))
    }
    spec_dict["auth"] = dict(preset.get("auth", {}))
    spec_dict["apps"] = list(preset.get("apps", []))
    spec_dict["images"] = dict(preset.get("images", {}))

    # --add / --remove → modules.optional
    optional: list[str] = spec_dict["modules"]["optional"]
    for m in inputs.add:
        if m not in optional:
            optional.append(m)
    for m in inputs.remove:
        if m in optional:
            optional.remove(m)

    # --app → spec.apps (replaces preset's app list if any provided)
    if inputs.apps:
        spec_dict["apps"] = [{"name": a} for a in inputs.apps]

    # Auth CLI overrides
    if inputs.auth_mode is not None:
        spec_dict["auth"]["mode"] = inputs.auth_mode
    if inputs.auth_gateway is not None:
        spec_dict["auth"]["gateway"] = inputs.auth_gateway
        # Switching off OIDC + gateway? Drop users too, to pass validation.
        if inputs.auth_mode == "api-key-only":
            spec_dict["auth"]["users"] = []

    # Target + platform
    spec_dict["target"] = inputs.target
    spec_dict["platform"] = _build_platform(inputs)

    # Network. Target-aware port defaults when not explicitly set:
    # k8s uses the LB's 443/80; compose/dev uses 8443/8080 so Caddy
    # doesn't need privileged ports.
    default_https = 443 if inputs.target == "k8s" else 8443
    default_http = 80 if inputs.target == "k8s" else 8080
    spec_dict["network"] = {
        "hostname": inputs.hostname,
        "tls": inputs.tls,
        "https_port": inputs.https_port if inputs.https_port is not None else default_https,
        "http_port": inputs.http_port if inputs.http_port is not None else default_http,
    }

    # Images
    if inputs.registry is not None:
        spec_dict["images"]["registry"] = inputs.registry
    if inputs.tag:
        spec_dict["images"]["tag"] = inputs.tag

    # Secrets — target-derived default
    spec_dict["secrets"] = _build_secrets(inputs)

    return Deployment(
        metadata=DeploymentMetadata(name=inputs.name),
        spec=DeploymentSpec.model_validate(spec_dict),
    )


def _build_platform(inputs: BuildInputs) -> dict[str, Any]:
    block: dict[str, Any] = {}
    if inputs.target == "compose":
        if inputs.compose_data_dir is None:
            raise ValueError("target=compose requires a data_dir")
        block["compose"] = {
            "data_dir": str(inputs.compose_data_dir),
            "platform_variant": inputs.compose_platform_variant,
        }
    elif inputs.target == "k8s":
        block["k8s"] = {
            "namespace": inputs.k8s_namespace,
            "storage_class": inputs.k8s_storage_class,
            "ingress_class": inputs.k8s_ingress_class,
            "tls_secret_name": inputs.k8s_tls_secret_name,
        }
    elif inputs.target == "dev":
        block["dev"] = {"mode": inputs.dev_mode}
    else:
        raise ValueError(f"unknown target {inputs.target!r}")
    return block


def _build_secrets(inputs: BuildInputs) -> dict[str, Any]:
    backend = inputs.secrets_backend
    if backend is None:
        backend = "k8s-secret" if inputs.target == "k8s" else "file"

    block: dict[str, Any] = {"backend": backend}

    if inputs.secrets_location is not None:
        block["location"] = inputs.secrets_location
    elif backend in ("file", "sops"):
        block["location"] = str(
            Path.home() / ".wip-deploy" / inputs.name / "secrets"
        )

    return block
