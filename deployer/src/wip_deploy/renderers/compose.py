"""Compose renderer — produces `docker-compose.yaml` + `.env` + `config/`.

Turns the declarative spec + component manifests + resolved env +
secrets into a compose file that `podman-compose up -d` (or
`docker compose up -d`) can directly consume.

Output tree:
    docker-compose.yaml
    .env
    config/caddy/Caddyfile     (when auth.gateway=True or OIDC active)
    config/dex/config.yaml     (when Dex active)

Design decisions:
  - Literals go inline in compose `environment:`; secret refs go via
    `${VAR}` interpolation from `.env`. Both .env and compose are
    written to the install dir with 0600/0644 respectively.
  - Each component with storage gets a named volume
    `wip-<component>-<storage>-data`.
  - Config files bound read-only from the install dir.
  - Explicit `command:` on WIP services (uvicorn) since we're running
    published images directly, not the service's own Dockerfile CMD.
"""

from __future__ import annotations

from typing import Any

import yaml

from wip_deploy.config_gen import (
    ResolvedEnv,
    SecretRef,
    generate_caddy_config,
    generate_dex_config,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.config_gen.env import (
    Literal,
)
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.compose_dex import render_dex_config
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────
# Main entry point
# ────────────────────────────────────────────────────────────────────


def render_compose(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    secrets: ResolvedSecrets,
) -> FileTree:
    """Render the complete compose deployment to an in-memory FileTree."""
    if deployment.spec.target != "compose":
        raise ValueError(
            f"render_compose requires target=compose, got {deployment.spec.target!r}"
        )

    ctx = make_spec_context(deployment, components)
    resolved_env = resolve_all_env(deployment, components, apps, ctx)

    tree = FileTree()

    # docker-compose.yaml
    tree.add(
        "docker-compose.yaml",
        _render_compose_yaml(deployment, components, apps, resolved_env),
    )

    # .env (secrets + any interpolation the compose file refers to)
    tree.add(
        ".env",
        _render_dotenv(secrets),
        mode=0o600,
    )

    # Caddyfile — always emitted for compose (Caddy is the ingress)
    caddy_cfg = generate_caddy_config(deployment, components, apps)
    tree.add("config/caddy/Caddyfile", render_caddyfile(caddy_cfg))

    # Dex config — only when Dex is active
    dex_cfg = generate_dex_config(deployment, components, apps)
    if dex_cfg is not None:
        tree.add(
            "config/dex/config.yaml",
            render_dex_config(dex_cfg, secrets),
        )

    return tree


# ────────────────────────────────────────────────────────────────────
# docker-compose.yaml
# ────────────────────────────────────────────────────────────────────


def _render_compose_yaml(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    resolved_env: dict[str, ResolvedEnv],
) -> str:
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}

    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    for c in components:
        if not is_component_active(c, deployment):
            continue
        services[c.metadata.name] = _service_block(
            c, deployment, resolved_env[c.metadata.name], is_app=False
        )
        _collect_volumes(c, volumes)

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        services[a.metadata.name] = _service_block(
            a, deployment, resolved_env[a.metadata.name], is_app=True
        )
        _collect_volumes(a, volumes)

    # Always emit the caddy service when OIDC is on or apps/components
    # have routes — which is basically every deployment.
    services["caddy"] = _caddy_service_block(deployment)

    top: dict[str, Any] = {
        "services": services,
        "networks": {"wip-network": {"name": "wip-network", "driver": "bridge"}},
    }
    if volumes:
        top["volumes"] = volumes

    return yaml.safe_dump(top, sort_keys=False, default_flow_style=False)


def _service_block(
    owner: Component | App,
    deployment: Deployment,
    env: ResolvedEnv,
    *,
    is_app: bool,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "container_name": _container_name(owner.metadata.name),
        "image": _image_ref(owner, deployment),
    }

    # Build context, if applicable (local-build mode: no registry set).
    build = _build_block(owner, deployment)
    if build is not None:
        block["build"] = build

    if owner.spec.ports:
        # Infrastructure and internal services expose no host ports; only
        # Caddy maps to the host (handled separately in _caddy_service_block).
        pass

    environment = _environment_block(env)
    if environment:
        block["environment"] = environment

    # .env interpolation wants this for every service that uses ${VAR}.
    if any(isinstance(v, SecretRef) for v in env.merged().values()):
        block["env_file"] = [".env"]

    command = _command_for(owner)
    if command is not None:
        block["command"] = command

    volumes = _volumes_for(owner)
    if volumes:
        block["volumes"] = volumes

    block["networks"] = ["wip-network"]
    block["restart"] = "unless-stopped"

    hc = _healthcheck_block(owner)
    if hc is not None:
        block["healthcheck"] = hc

    deps = _depends_on_block(owner, deployment)
    if deps:
        block["depends_on"] = deps

    return block


def _caddy_service_block(deployment: Deployment) -> dict[str, Any]:
    """Caddy is implicit — every compose deployment gets it as the
    single host-exposed ingress."""
    net = deployment.spec.network
    return {
        "container_name": "wip-caddy",
        "image": "docker.io/library/caddy:2",
        "ports": [f"{net.https_port}:{net.https_port}"],
        "volumes": [
            "./config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
            "wip-caddy-data:/data",
            "wip-caddy-config:/config",
        ],
        "networks": ["wip-network"],
        "restart": "unless-stopped",
    }


# ────────────────────────────────────────────────────────────────────
# Per-field builders
# ────────────────────────────────────────────────────────────────────


def _container_name(component_name: str) -> str:
    return f"wip-{component_name}"


def _image_ref(owner: Component | App, deployment: Deployment) -> str:
    """Resolve the full image reference.

    - Fully qualified (contains `/`): `{name}:{tag}` — tag from the
      ImageRef (required for infra pinning).
    - Short name + deployment registry set: `{registry}/{name}:{tag}`.
      Tag falls back to `spec.images.tag`.
    - Short name + no registry: bare `{name}:{tag}` (assumed local
      build will produce this image).
    """
    ref = owner.spec.image
    spec_images = deployment.spec.images

    if "/" in ref.name:
        tag = ref.tag or "latest"
        return f"{ref.name}:{tag}"

    tag = ref.tag or spec_images.tag
    if spec_images.registry:
        return f"{spec_images.registry}/{ref.name}:{tag}"
    return f"{ref.name}:{tag}"


def _build_block(
    owner: Component | App, deployment: Deployment
) -> dict[str, Any] | None:
    """Emit a `build:` block when local build is intended.

    In v2 initial, compose local-build is deliberately unsupported. The
    recommended flows are:

      - Registry-pull: pass `--registry <host>` pointing at a host with
        pre-built images; `spec.images.registry` is set and the bare
        image name is prefixed.
      - Dev loop: use the dev renderer (Tilt, step 7) which handles
        source mounts + incremental build natively.

    When neither is in play (no registry, no dev), we emit no build:
    block. Users must `podman build` / `podman load` the images before
    `install` runs. This keeps the compose renderer small; complex
    build-context resolution lives only in the dev renderer.
    """
    return None


def _environment_block(env: ResolvedEnv) -> dict[str, str]:
    """Flatten resolved env to a name → string mapping.

    Literals go inline. SecretRef values reference `${VAR}` which is
    supplied by the .env file written alongside.
    """
    out: dict[str, str] = {}
    for name, value in env.merged().items():
        if isinstance(value, Literal):
            out[name] = value.value
        elif isinstance(value, SecretRef):
            out[name] = f"${{{_secret_to_env_var(value.name)}}}"
        else:  # pragma: no cover — exhaustive via types
            raise AssertionError(f"unknown EnvValue: {value!r}")
    return out


def _command_for(owner: Component | App) -> list[str] | None:
    """Explicit command for WIP services (the published image's CMD
    isn't always what we want, and being explicit makes the compose
    file self-documenting)."""
    name = owner.metadata.name

    # Infra components use the image's default command.
    if name in ("mongodb", "postgres", "nats"):
        return None

    # Dex needs an explicit command.
    if name == "dex":
        return ["dex", "serve", "/etc/dex/config.yaml"]

    # MinIO: command is set on its own manifest or here.
    if name == "minio":
        return ["server", "/data", "--console-address", ":9001"]

    # WIP Python services: run uvicorn with their main app. Module name
    # is the service's short name with hyphens → underscores.
    http_port = next(
        (p for p in owner.spec.ports if p.name == "http"), None
    )
    if http_port is None:
        # e.g., auth-gateway (port name "http") — covered above; or nats
        # with no http port — no command needed.
        return None

    module = name.replace("-", "_")
    # wip-console: just run nginx (default CMD). auth-gateway: default CMD.
    if name in ("console", "auth-gateway"):
        return None

    return [
        "uvicorn",
        f"{module}.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(http_port.container_port),
    ]


def _volumes_for(owner: Component | App) -> list[str]:
    volumes: list[str] = []

    # Named volumes from storage declarations.
    for storage in owner.spec.storage:
        volume_name = f"wip-{owner.metadata.name}-{storage.name}"
        volumes.append(f"{volume_name}:{storage.mount_path}")

    # Config-file bind mounts for known components.
    if owner.metadata.name == "dex":
        volumes.append("./config/dex/config.yaml:/etc/dex/config.yaml:ro")

    return volumes


def _collect_volumes(owner: Component | App, volumes: dict[str, Any]) -> None:
    """Register top-level named volumes for this component's storage."""
    for storage in owner.spec.storage:
        volumes[f"wip-{owner.metadata.name}-{storage.name}"] = {}

    # Caddy's data/config volumes (always present).
    volumes["wip-caddy-data"] = {}
    volumes["wip-caddy-config"] = {}


def _healthcheck_block(owner: Component | App) -> dict[str, Any] | None:
    hc = owner.spec.healthcheck
    if hc is None:
        return None

    if hc.command is not None:
        test = ["CMD", *hc.command]
    else:
        # HTTP check. Pick the port: explicit > "http" > first.
        port = _resolve_check_port(owner, hc.port)
        assert hc.endpoint is not None  # enforced by HealthcheckSpec validator
        url = f"http://localhost:{port}{hc.endpoint}"
        test = ["CMD", "curl", "-f", url]

    return {
        "test": test,
        "interval": f"{hc.interval_seconds}s",
        "timeout": f"{hc.timeout_seconds}s",
        "retries": hc.retries,
        "start_period": f"{hc.start_period_seconds}s",
    }


def _resolve_check_port(owner: Component | App, explicit: str | None) -> int:
    by_name = {p.name: p for p in owner.spec.ports}
    if explicit is not None:
        if explicit not in by_name:
            raise ValueError(
                f"{owner.metadata.name}: healthcheck.port={explicit!r} "
                f"not declared in ports"
            )
        return by_name[explicit].container_port
    if "http" in by_name:
        return by_name["http"].container_port
    if owner.spec.ports:
        return owner.spec.ports[0].container_port
    raise ValueError(
        f"{owner.metadata.name} has an HTTP healthcheck but no ports declared"
    )


def _depends_on_block(
    owner: Component | App, deployment: Deployment
) -> dict[str, Any]:
    """Emit depends_on with service_healthy conditions for every active
    dependency."""
    out: dict[str, Any] = {}
    for dep_name in owner.spec.depends_on:
        # We can only wait on deps that themselves declare a healthcheck.
        # For the rest we fall back to service_started (starts-but-may-
        # not-be-ready). This is target-equivalent to what v1 did.
        out[dep_name] = {"condition": "service_healthy"}
    return out


# ────────────────────────────────────────────────────────────────────
# .env
# ────────────────────────────────────────────────────────────────────


def _render_dotenv(secrets: ResolvedSecrets) -> str:
    """Emit a key=value file. Each secret appears as
    `<UPPER_SNAKE_CASE>=<value>` so compose's `${VAR}` interpolation
    finds it."""
    lines = [
        "# Generated by wip-deploy — do not edit. Regenerate via `wip-deploy install`.",
        "# Secrets have mode 0600. Keep this file out of version control.",
        "",
    ]
    for name in sorted(secrets.values.keys()):
        lines.append(f"{_secret_to_env_var(name)}={secrets.values[name]}")
    return "\n".join(lines) + "\n"


def _secret_to_env_var(name: str) -> str:
    """Translate a secret name into a shell env var name."""
    return name.upper().replace("-", "_")
