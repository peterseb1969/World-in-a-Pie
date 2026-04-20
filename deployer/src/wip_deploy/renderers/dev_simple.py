"""Dev renderer (simple mode) — compose-style output with source mounts
and live-reload where possible.

For developers iterating on component source locally. Produces the same
file set as the compose renderer but with three dev-friendly changes:

  1. `build:` contexts from each component's `build_context` — no pulling
     from a registry. Edits to a Dockerfile reflect in the next rebuild.
  2. Source volume mounts (`./components/<name>/src:/app/src:ro`) for
     every component with a build_context — edits to Python source show
     up inside the container without a rebuild.
  3. `--reload` appended to uvicorn commands — Python services
     hot-reload on source change.

Node/Go apps don't benefit from (2) and (3) — they need rebuilds. MVP
treats them the same: they get build contexts but no hot reload. Tilt
mode will handle incremental image builds properly; that's step 7 round 2.

Caddy, Dex, and the .env are rendered identically to compose — same
auth flow, same internal TLS. Production fidelity by default; if a
specific integration issue surfaces in dev it also surfaces in prod.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wip_deploy.config_gen import (
    SecretRef,
    generate_caddy_config,
    generate_dex_config,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.compose import (
    _caddy_service_block,
    _collect_volumes,
    _command_for,
    _container_name,
    _depends_on_block,
    _environment_block,
    _healthcheck_block,
    _image_ref,
    _render_dotenv,
)
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.compose_dex import render_dex_config
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────


def render_dev_simple(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    secrets: ResolvedSecrets,
    *,
    repo_root: Path,
) -> FileTree:
    """Render a dev-mode compose tree with build contexts and source mounts."""
    if deployment.spec.target != "dev":
        raise ValueError(
            f"render_dev_simple requires target=dev, got {deployment.spec.target!r}"
        )
    dev_plat = deployment.spec.platform.dev
    if dev_plat is None or dev_plat.mode != "simple":
        raise ValueError("render_dev_simple requires platform.dev.mode='simple'")

    ctx = make_spec_context(deployment, components)
    resolved_env = resolve_all_env(
        deployment, components, apps, ctx,
        collected_secrets=set(secrets.values.keys()),
    )

    tree = FileTree()

    tree.add(
        "docker-compose.yaml",
        _render_dev_compose_yaml(
            deployment, components, apps, resolved_env, repo_root,
            source_mount=dev_plat.source_mount,
        ),
    )

    tree.add(".env", _render_dotenv(secrets), mode=0o600)

    caddy_cfg = generate_caddy_config(deployment, components, apps)
    tree.add("config/caddy/Caddyfile", render_caddyfile(caddy_cfg))

    dex_cfg = generate_dex_config(deployment, components, apps)
    if dex_cfg is not None:
        tree.add("config/dex/config.yaml", render_dex_config(dex_cfg, secrets))

    return tree


# ────────────────────────────────────────────────────────────────────


def _render_dev_compose_yaml(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    resolved_env: dict[str, Any],  # dict[str, ResolvedEnv]
    repo_root: Path,
    *,
    source_mount: bool,
) -> str:
    """Same shape as compose's _render_compose_yaml but with build: blocks,
    source mounts, and uvicorn --reload."""
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}

    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    active_names: set[str] = set()
    healthcheck_owners: set[str] = set()
    for c in components:
        if is_component_active(c, deployment):
            active_names.add(c.metadata.name)
            if c.spec.healthcheck is not None:
                healthcheck_owners.add(c.metadata.name)
    for a in apps:
        if a.metadata.name in enabled_app_names:
            active_names.add(a.metadata.name)
            if a.spec.healthcheck is not None:
                healthcheck_owners.add(a.metadata.name)

    for c in components:
        if not is_component_active(c, deployment):
            continue
        services[c.metadata.name] = _dev_service_block(
            c, deployment, resolved_env[c.metadata.name], repo_root,
            healthcheck_owners=healthcheck_owners,
            active_names=active_names,
            source_mount=source_mount,
        )
        _collect_volumes(c, volumes)

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        services[a.metadata.name] = _dev_service_block(
            a, deployment, resolved_env[a.metadata.name], repo_root,
            healthcheck_owners=healthcheck_owners,
            active_names=active_names,
            source_mount=source_mount,
        )
        _collect_volumes(a, volumes)

    services["caddy"] = _caddy_service_block(deployment)

    top: dict[str, Any] = {
        "services": services,
        "networks": {"wip-network": {"name": "wip-network", "driver": "bridge"}},
    }
    if volumes:
        top["volumes"] = volumes

    return yaml.safe_dump(top, sort_keys=False, default_flow_style=False)


def _dev_service_block(
    owner: Component | App,
    deployment: Deployment,
    env: Any,  # ResolvedEnv
    repo_root: Path,
    *,
    healthcheck_owners: set[str],
    active_names: set[str],
    source_mount: bool,
) -> dict[str, Any]:
    """Same as compose's _service_block but with dev-mode overrides."""
    block: dict[str, Any] = {"container_name": _container_name(owner.metadata.name)}

    # Dev: prefer build: over image: when a build_context is declared.
    # The bare image ref stays as a fallback tag for infra components
    # (mongo, postgres) which have build_context=None.
    build_ctx = _resolve_build_context(owner, repo_root)
    if build_ctx is not None:
        block["build"] = {"context": str(build_ctx)}
        if owner.spec.image.build_args:
            block["build"]["args"] = dict(owner.spec.image.build_args)
        # In dev we still tag the image — keeps `podman images` tidy.
        block["image"] = f"{owner.spec.image.name}:dev"
    else:
        block["image"] = _image_ref(owner, deployment)

    environment = _environment_block(env)
    if environment:
        block["environment"] = environment

    if any(isinstance(v, SecretRef) for v in env.merged().values()):
        block["env_file"] = [".env"]

    # Dev: if the command is uvicorn-based, append --reload for hot reload.
    cmd = _command_for(owner)
    if cmd:
        if cmd[0] == "uvicorn" and "--reload" not in cmd:
            cmd = [*cmd, "--reload"]
        # See compose.py for the entrypoint/command split rationale:
        # compose's `command:` only overrides CMD, so full argv needs to
        # go to `entrypoint:` to replace the image's ENTRYPOINT.
        block["entrypoint"] = [cmd[0]]
        block["command"] = cmd[1:]

    # Dev: mount source directories for build-context components so edits
    # show up inside the container without a rebuild.
    volumes = _dev_volumes_for(owner, repo_root, enable_source_mount=source_mount)
    if volumes:
        block["volumes"] = volumes

    block["networks"] = ["wip-network"]
    block["restart"] = "unless-stopped"

    hc = _healthcheck_block(owner)
    if hc is not None:
        block["healthcheck"] = hc

    deps = _depends_on_block(
        owner, deployment,
        healthcheck_owners=healthcheck_owners,
        active_names=active_names,
    )
    if deps:
        block["depends_on"] = deps

    return block


def _resolve_build_context(
    owner: Component | App, repo_root: Path
) -> Path | None:
    """Where is this component's Dockerfile?

    Component manifests declare `build_context: .` (relative to the
    manifest directory). We resolve that to an absolute path: for
    components, `repo_root/components/<name>/`; for apps, we skip
    because the app images are built externally in the MVP.
    """
    ref = owner.spec.image
    if ref.build_context is None:
        return None
    if isinstance(owner, App):
        # Apps are Node/mixed — no hot reload, and their repos are external.
        # Still rebuild from source if a build_context is set (future).
        return None
    return (repo_root / "components" / owner.metadata.name).resolve()


def _dev_volumes_for(
    owner: Component | App, repo_root: Path, *, enable_source_mount: bool
) -> list[str]:
    """Compose volumes list with dev-mode source mounts added.

    Storage volumes come from the manifest (same as prod). Config-file
    mounts (Dex) come from the compose renderer's logic — mirrored here
    so dev doesn't need a full compose re-implementation.
    """
    volumes: list[str] = []

    # Named storage volumes — same as prod.
    for storage in owner.spec.storage:
        volume_name = f"wip-{owner.metadata.name}-{storage.name}"
        volumes.append(f"{volume_name}:{storage.mount_path}")

    # Dex config bind mount — same as prod.
    if owner.metadata.name == "dex":
        volumes.append("./config/dex/config.yaml:/etc/dex/config.yaml:ro")

    # Dev-specific: source mount for Python services.
    if enable_source_mount and not isinstance(owner, App):
        ref = owner.spec.image
        if ref.build_context is not None:
            source = (repo_root / "components" / owner.metadata.name / "src").resolve()
            if source.exists():
                volumes.append(f"{source}:/app/src:ro")

    return volumes


# Re-export for potential external use; the name aligns with the module's
# `render_*` convention.
__all__ = ["render_dev_simple"]
