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
    # Security posture (dev|prod) — arms the services' WIP_VARIANT-keyed
    # production-safety guards. Explicit operator intent only; never
    # derived from target.
    variant: str = "dev"
    hostname: str = "wip.local"
    tls: str = "internal"
    # None → target-aware defaults: 443 for k8s (nginx-ingress LoadBalancer),
    # 8443 for compose/dev (Caddy as non-root). Explicit ports pass through.
    https_port: int | None = None

    # Spec-declared API keys (parsed dicts, one per --api-key JSON
    # value). Persist in the install's state like everything else, so
    # redeploy/rebuild keep them and the rendered key file survives.
    api_keys: list[dict[str, Any]] = field(default_factory=list)

    # Compose platform
    compose_data_dir: Path | None = None
    compose_platform_variant: str = "default"

    # K8s platform
    k8s_namespace: str = "wip"
    k8s_storage_class: str = "rook-ceph-block"
    k8s_ingress_class: str = "nginx"
    k8s_ingress_controller: str = "nginx"
    k8s_ingress_namespace: str | None = None
    # "pods=CIDR" / "services=CIDR" / "nodes=CIDR" (--cluster-cidr, repeatable)
    k8s_cluster_cidrs: list[str] | None = None
    k8s_tls_secret_name: str = "wip-tls"

    # Dev platform
    dev_mode: str = "simple"
    # CASE-55: map of app_name → local build-context path for hot-reload dev.
    # Populated from `--app-source NAME=PATH` (repeatable). Applies only to
    # target=dev; ignored otherwise.
    app_sources: dict[str, Path] = field(default_factory=dict)
    # CASE-355: explicit opt-in list of apps that should use the
    # registry image in dev mode despite no local source. Populated from
    # `--app-from-registry NAME` (repeatable). Applies only to
    # target=dev; ignored otherwise.
    apps_from_registry: list[str] = field(default_factory=list)
    # --privileged-app: apps this install lets hold the master key. The
    # manifest's privileged_key asks for it; only the operator grants it.
    privileged_apps: list[str] = field(default_factory=list)

    # Images. tag=None → no deployment-wide tag: manifest pins apply
    # (then "latest"). When set, the tag is authoritative over manifest
    # pins for WIP-built images (CASE-438). tag_overrides carries
    # per-service `--image-tag NAME=TAG` entries — highest precedence.
    registry: str | None = None
    tag: str | None = None
    tag_overrides: dict[str, str] = field(default_factory=dict)

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
    if inputs.privileged_apps:
        enabled = {
            (a["name"] if isinstance(a, dict) else a) for a in spec_dict["apps"]
        }
        stray = sorted(set(inputs.privileged_apps) - enabled)
        if stray:
            raise ValueError(
                f"--privileged-app {', '.join(stray)} names an app this install "
                "does not enable; add it with --app as well"
            )
        spec_dict["apps"] = [
            {**(a if isinstance(a, dict) else {"name": a}),
             "privileged": (a["name"] if isinstance(a, dict) else a) in inputs.privileged_apps}
            for a in spec_dict["apps"]
        ]

    # Auth CLI overrides
    if inputs.auth_mode is not None:
        spec_dict["auth"]["mode"] = inputs.auth_mode
    if inputs.auth_gateway is not None:
        spec_dict["auth"]["gateway"] = inputs.auth_gateway
        # Switching off OIDC + gateway? Drop users too, to pass validation.
        if inputs.auth_mode == "api-key-only":
            spec_dict["auth"]["users"] = []
    elif inputs.auth_mode == "api-key-only" and spec_dict["auth"].get("gateway"):
        # The mode needs this half of the pair whether the operator named it or
        # not: api-key-only runs no Dex (config_gen/dex.py skips the component
        # outright), and the gateway exists to authenticate Dex sessions, so a
        # gateway preset left `gateway: True` and the validator refused the
        # spec. Same shape the core and headless presets ship. An explicit --auth-gateway is not overridden —
        # a deliberate contradiction still reaches the validator, which now names
        # both flags.
        spec_dict["auth"]["gateway"] = False
        spec_dict["auth"]["users"] = []

    # Spec-declared API keys — each entry a dict (parsed from the CLI's
    # --api-key JSON); AuthSpec validates shape, name uniqueness, and
    # grants ⊆ namespaces.
    if inputs.api_keys:
        spec_dict["auth"]["api_keys"] = list(inputs.api_keys)

    # Target + platform
    spec_dict["target"] = inputs.target
    spec_dict["variant"] = inputs.variant
    spec_dict["platform"] = _build_platform(inputs)

    # Network. Target-aware port defaults when not explicitly set:
    # k8s uses the LB's 443/80; compose/dev uses 8443/8080 so Caddy
    # doesn't need privileged ports.
    default_https = 443 if inputs.target == "k8s" else 8443
    # Target-aware TLS: for k8s, auto-upgrade tls=internal (the default)
    # to tls=self-signed so the install pre-flight generates a cert + Secret.
    # On compose/dev, tls=internal means Caddy-managed self-signed and
    # already works. CASE-247.
    tls = inputs.tls
    if inputs.target == "k8s" and tls == "internal":
        tls = "self-signed"
    spec_dict["network"] = {
        "hostname": inputs.hostname,
        "tls": tls,
        "https_port": inputs.https_port if inputs.https_port is not None else default_https,
    }
    # Images
    if inputs.registry is not None:
        spec_dict["images"]["registry"] = inputs.registry
    if inputs.tag is not None:
        spec_dict["images"]["tag"] = inputs.tag
    if inputs.tag_overrides:
        spec_dict["images"]["tag_overrides"] = dict(inputs.tag_overrides)

    # Secrets — target-derived default
    spec_dict["secrets"] = _build_secrets(inputs)

    return Deployment(
        metadata=DeploymentMetadata(name=inputs.name),
        spec=DeploymentSpec.model_validate(spec_dict),
    )


def parse_cluster_cidrs(items: list[str]) -> dict[str, list[str]]:
    """`--cluster-cidr kind=CIDR` items as K8sClusterRanges fields."""
    ranges: dict[str, list[str]] = {}
    for item in items:
        kind, sep, cidr = item.partition("=")
        if not sep or kind not in ("pods", "services", "nodes") or not cidr:
            raise ValueError(
                f"--cluster-cidr {item!r}: expected pods=CIDR, services=CIDR or nodes=CIDR"
            )
        ranges.setdefault(kind, []).append(cidr)
    return ranges


def _build_platform(inputs: BuildInputs) -> dict[str, Any]:
    block: dict[str, Any] = {}
    if inputs.k8s_cluster_cidrs and inputs.target != "k8s":
        raise ValueError(
            f"--cluster-cidr is for the k8s target only (this is {inputs.target!r}): "
            "the cluster ranges bound the app edge's NetworkPolicy"
        )
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
            "ingress_controller": inputs.k8s_ingress_controller,
            "tls_secret_name": inputs.k8s_tls_secret_name,
        }
        if inputs.k8s_ingress_namespace:
            block["k8s"]["ingress_namespace"] = inputs.k8s_ingress_namespace
        if inputs.k8s_cluster_cidrs:
            block["k8s"]["cluster_ranges"] = parse_cluster_cidrs(inputs.k8s_cluster_cidrs)
    elif inputs.target == "dev":
        dev_block: dict[str, Any] = {"mode": inputs.dev_mode}
        if inputs.app_sources:
            dev_block["app_sources"] = dict(inputs.app_sources)
        if inputs.apps_from_registry:
            dev_block["apps_from_registry"] = list(inputs.apps_from_registry)
        block["dev"] = dev_block
    else:
        raise ValueError(f"unknown target {inputs.target!r}")
    return block


def _build_secrets(inputs: BuildInputs) -> dict[str, Any]:
    backend = inputs.secrets_backend
    if backend is None:
        # CASE-363: default to the file backend for every target, including
        # k8s. The native k8s-secret backend isn't implemented (install
        # rejects any non-file backend), and the k8s renderer already bakes
        # file-backend values into an in-cluster Secret (renderers/k8s.py
        # _render_secrets) — so file is the correct default for k8s, not a
        # stopgap. Previously k8s defaulted to "k8s-secret", which made every
        # fresh `install --target k8s` fail on its own default. Flip back to a
        # target-conditional default if/when the k8s-secret backend lands.
        backend = "file"

    block: dict[str, Any] = {"backend": backend}

    if inputs.secrets_location is not None:
        # Make the store path absolute where it enters the spec, so every
        # consumer — the file backend writing it, install's and render's CA probe,
        # and the compose bind-mount — resolves the same directory. A relative
        # value would otherwise be read against whatever cwd each site happens to
        # have, and compose treats a short-syntax mount source with no leading
        # `/`, `./` or `~` as a *named volume*: the CA bind-mount would silently
        # become an empty volume instead (CASE-1228 #3 finding 2).
        #
        # `absolute()`, not `resolve()`: resolve() follows symlinks, which on
        # macOS rewrites `/var/folders/…` to `/private/var/folders/…` and churns
        # stored paths against every later comparison. `~` still needs expanding.
        block["location"] = str(Path(inputs.secrets_location).expanduser().absolute())
    elif backend in ("file", "sops"):
        block["location"] = str(
            Path.home() / ".wip-deploy" / inputs.name / "secrets"
        )

    return block
