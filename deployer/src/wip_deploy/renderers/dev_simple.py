"""Dev renderer (simple mode) — compose-style output with source mounts
and live-reload where possible.

For developers iterating on component source locally. Produces the same
file set as the compose renderer but with three dev-friendly changes:

  1. `build:` contexts materialized into the render tree at
     build-contexts/<name>/ — no pulling from a registry. For the 5
     Python services that use `wip_auth` the context is patched to
     COPY + pip install `libs/wip-auth` (mirrors `build-release.sh`
     for production). `document-store` additionally gets `WIP-Toolkit`
     baked in.
  2. Source volume mounts (`./components/<name>/src:/app/src:ro`) for
     every component with a build_context — edits to Python source show
     up inside the container without a rebuild.
  3. `--reload` appended to uvicorn commands — Python services
     hot-reload on source change.

Node/Go apps don't benefit from (2) and (3) — they need rebuilds. MVP
treats them the same: they get build contexts but no hot reload. Tilt
mode reserved for future incremental-build orchestration; not
implemented today.

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
from wip_deploy.config_gen.router import generate_router_config
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.compose_dex import render_dex_config
from wip_deploy.renderers.router_caddy import render_router_caddyfile
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────
# Build-context baking — match build-release.sh
# ────────────────────────────────────────────────────────────────────

# Services that import `wip_auth`. Their dev images need it baked in
# (the Dockerfiles don't install it by default — production images are
# patched by build-release.sh; dev gets the same treatment here).
_AUTH_SERVICES: frozenset[str] = frozenset({
    "registry", "def-store", "template-store", "document-store", "reporting-sync",
})

# Services that additionally import from WIP-Toolkit.
_TOOLKIT_SERVICES: frozenset[str] = frozenset({"document-store"})

# Directories/files to skip when copying a component tree into the
# render output. Caches and test fixtures aren't needed in the build
# context and can contain binaries that would break FileTree's
# text-only storage.
_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", "venv", "node_modules", ".git", "tests", "dist", "build",
    ".egg-info",
})
_SKIP_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo", ".pyd"})


def _copy_tree_into(src: Path, tree: FileTree, prefix: str) -> None:
    """Walk `src` and add every text file to `tree` under `prefix/...`.

    Binary files are silently skipped (FileTree stores text only; our
    Python services don't need binary assets in the build context).
    """
    if not src.is_dir():
        return
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(src).parts
        if any(part in _SKIP_DIR_NAMES for part in parts):
            continue
        if path.suffix in _SKIP_SUFFIXES:
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            continue
        rel = "/".join(parts)
        tree.add(f"{prefix}/{rel}", content)


def _patch_dockerfile_for_dev(content: str, *, bake_toolkit: bool) -> str:
    """Insert wip-auth (and optionally wip-toolkit) installs into a
    component's Dockerfile, right after the requirements install.

    Mirrors the awk patch in `scripts/build-release.sh`. If the trigger
    line isn't present (unusual — every Python component has it), the
    Dockerfile is returned unchanged.
    """
    trigger = "RUN pip install --no-cache-dir -r requirements-docker.txt"
    insert = [
        "",
        "# Install wip-auth library (dev-bake)",
        "COPY wip-auth /tmp/wip-auth",
        "RUN pip install --no-cache-dir /tmp/wip-auth && rm -rf /tmp/wip-auth",
    ]
    if bake_toolkit:
        insert.extend([
            "",
            "# Install wip-toolkit library (dev-bake)",
            "COPY wip-toolkit /tmp/wip-toolkit",
            "RUN pip install --no-cache-dir /tmp/wip-toolkit && rm -rf /tmp/wip-toolkit",
        ])
    out_lines: list[str] = []
    patched = False
    for line in content.splitlines():
        out_lines.append(line)
        if not patched and line.strip() == trigger:
            out_lines.extend(insert)
            patched = True
    return "\n".join(out_lines) + "\n"


def _materialize_dev_build_context(
    component: Component,
    repo_root: Path,
    tree: FileTree,
) -> str | None:
    """Copy a component's build inputs + wip-auth (+ wip-toolkit) into
    the render tree under build-contexts/<name>/.

    Returns the relative path the compose `build.context:` field should
    point at, or None for components that have no build_context (use
    the upstream image directly).
    """
    if component.spec.image.build_context is None:
        return None

    name = component.metadata.name
    ctx_root = repo_root / "components" / name
    if not ctx_root.is_dir():
        return None

    prefix = f"build-contexts/{name}"
    _copy_tree_into(ctx_root, tree, prefix)

    if name in _AUTH_SERVICES:
        _copy_tree_into(repo_root / "libs" / "wip-auth", tree, f"{prefix}/wip-auth")
    if name in _TOOLKIT_SERVICES:
        _copy_tree_into(repo_root / "WIP-Toolkit", tree, f"{prefix}/wip-toolkit")

    if name in _AUTH_SERVICES:
        dockerfile_path = Path(f"{prefix}/Dockerfile")
        if dockerfile_path in tree.files:
            patched = _patch_dockerfile_for_dev(
                tree.files[dockerfile_path].content,
                bake_toolkit=(name in _TOOLKIT_SERVICES),
            )
            tree.add(str(dockerfile_path), patched)

    return f"./{prefix}"


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

    # Materialize a self-contained build context per Python service so
    # wip-auth (and WIP-Toolkit for document-store) get baked in the
    # image. Without this, services crash at import with
    # `ModuleNotFoundError: No module named 'wip_auth'`.
    build_context_paths: dict[str, str] = {}
    for c in components:
        if not is_component_active(c, deployment):
            continue
        ctx_path = _materialize_dev_build_context(c, repo_root, tree)
        if ctx_path is not None:
            build_context_paths[c.metadata.name] = ctx_path

    tree.add(
        "docker-compose.yaml",
        _render_dev_compose_yaml(
            deployment, components, apps, resolved_env, repo_root,
            source_mount=dev_plat.source_mount,
            build_context_paths=build_context_paths,
        ),
    )

    tree.add(".env", _render_dotenv(secrets), mode=0o600)

    caddy_cfg = generate_caddy_config(deployment, components, apps)
    tree.add("config/caddy/Caddyfile", render_caddyfile(caddy_cfg))

    dex_cfg = generate_dex_config(deployment, components, apps)
    if dex_cfg is not None:
        tree.add("config/dex/config.yaml", render_dex_config(dex_cfg, secrets))

    # wip-router Caddyfile — same as compose renderer. Without this,
    # the wip-router container boots with the stock caddy image's
    # default Caddyfile (listening on :80, serving static files) and
    # every SSR-proxied API call through wip-router:8080 returns 502.
    router_active = any(
        c.metadata.name == "router" and is_component_active(c, deployment)
        for c in components
    )
    if router_active:
        router_cfg = generate_router_config(deployment, components, apps)
        tree.add(
            "config/router/Caddyfile",
            render_router_caddyfile(router_cfg),
        )

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
    build_context_paths: dict[str, str],
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
            build_context_paths=build_context_paths,
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
            build_context_paths=build_context_paths,
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
    build_context_paths: dict[str, str],
) -> dict[str, Any]:
    """Same as compose's _service_block but with dev-mode overrides."""
    block: dict[str, Any] = {"container_name": _container_name(owner.metadata.name)}

    # Dev: prefer build: over image: when a build_context is declared.
    # For Python components we use the materialized per-component build
    # context under ./build-contexts/<name>/ (wip-auth baked in).
    # For apps / other build-context components fall back to the
    # component's own directory.
    name = owner.metadata.name
    if name in build_context_paths:
        block["build"] = {"context": build_context_paths[name]}
        if owner.spec.image.build_args:
            block["build"]["args"] = dict(owner.spec.image.build_args)
        block["image"] = f"{owner.spec.image.name}:dev"
    else:
        build_ctx = _resolve_build_context(owner, repo_root)
        if build_ctx is not None:
            block["build"] = {"context": str(build_ctx)}
            if owner.spec.image.build_args:
                block["build"]["args"] = dict(owner.spec.image.build_args)
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

    # Router config bind mount — same as prod. Without this, wip-router
    # starts with the stock caddy default Caddyfile and all /api/* routing
    # breaks (502 Bad Gateway from SSR-proxied app calls).
    if owner.metadata.name == "router":
        volumes.append("./config/router/Caddyfile:/etc/caddy/Caddyfile:ro")

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
