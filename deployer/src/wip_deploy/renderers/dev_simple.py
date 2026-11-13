"""Dev renderer (simple mode) — compose-style output with source mounts
and live-reload where possible.

For developers iterating on component source locally. Produces the same
file set as the compose renderer but with three dev-friendly changes:

  1. `build:` contexts materialized into the render tree at
     build-contexts/<name>/ for each component that declares one (the
     others, dex, mongodb, nats, postgres, garage and the router, still
     pull their images). For the
     Python services that use `wip_auth` the context is patched to
     COPY + pip install `libs/wip-auth` (mirrors `build-release.sh`
     for production). `document-store` additionally gets `libs/wip-archive`
     baked in.
  2. Source volume mounts (`./components/<name>/src:/app/src:ro`) for
     every component with a build_context — edits to Python source show
     up inside the container without a rebuild.
  3. uvicorn commands run through a dev launcher with `--reload` —
     Python services hot-reload on source change, and a worker that dies
     is respawned instead of leaving the container up and unresponsive.
     A mounted service whose command is not uvicorn (mcp-server, app-edge)
     has no watcher: an edit takes effect on a restart, and the render
     notes each one (`FileTree.notes`).

Apps get no build context from the render: an app builds from its own
`--app-source` checkout (its Dockerfile.dev when it has one), pulls its
registry image when named in `--app-from-registry`, and is refused otherwise.

Caddy, Dex, and the .env are rendered identically to compose — same
auth flow, same internal TLS. Production fidelity by default; if a
specific integration issue surfaces in dev it also surfaces in prod.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wip_deploy.config_gen import (
    generate_caddy_config,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.config_gen.api_keys import (
    API_KEYS_CONTAINER_DIR,
    API_KEYS_RENDER_DIR,
    API_KEYS_RENDER_PATH,
    FEDERATED_GROUPS_RENDER_PATH,
    KEY_FILE_CONTAINER_DIR,
    KEY_FILE_RENDER_DIR,
    LEGACY_API_KEYS_RENDER_PATH,
    declares_api_keys_file,
    generate_api_keys_json,
    mounts_auth_config_dir,
)
from wip_deploy.config_gen.attach_policy import ATTACH_POLICY_RENDER_PATH, attach_policy_json
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY, config_hash
from wip_deploy.config_gen.garage import garage_component, render_garage_toml
from wip_deploy.config_gen.images import image_ref as _image_ref
from wip_deploy.config_gen.mongodb import (
    ENTRYPOINT_CONTAINER_PATH as MONGO_ENTRYPOINT_CONTAINER_PATH,
)
from wip_deploy.config_gen.mongodb import (
    ENTRYPOINT_RENDER_PATH as MONGO_ENTRYPOINT_RENDER_PATH,
)
from wip_deploy.config_gen.mongodb import (
    MONGODB_COMPONENT,
    mongodb_config_hash,
    render_mongodb_entrypoint,
)
from wip_deploy.config_gen.namespaces import (
    RESERVATIONS_RENDER_PATH,
    namespace_reservations_json,
)
from wip_deploy.config_gen.nats import (
    NATS_COMPONENT,
    NATS_CONFIG_CONTAINER_PATH,
    NATS_CONFIG_RENDER_PATH,
    nats_config_hash,
    render_nats_conf,
)
from wip_deploy.config_gen.router import generate_router_config
from wip_deploy.config_gen.spec_context import federated_groups_policy
from wip_deploy.naming import (
    compose_project_name,
    container_name,
    dev_image_tag,
    network_block,
    service_networks,
)
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.compose import (
    _caddy_service_block,
    _collect_volumes,
    _command_for,
    _environment_block,
    _healthcheck_block,
    _render_dotenv,
)
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.compose_dex import (
    DEX_COMPONENT,
    dex_config_hash,
    render_dex_conf,
)
from wip_deploy.renderers.router_caddy import render_router_caddyfile
from wip_deploy.renderers.upstreams import check_caddy_upstreams
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
    "ingest-gateway", "mcp-server", "auth-gateway",
})

# Services that additionally import from libs/wip-archive (the archive
# format + remap library the backup/restore engine consumes in-process).
_ARCHIVE_SERVICES: frozenset[str] = frozenset({"document-store"})

# uvicorn services run under a dev launcher: uvicorn's own CLI and hot
# reload, plus a respawn when the worker dies. Plain `--reload` restarts the
# worker only on a file change, so a worker that is OOM-killed or crashes
# leaves the supervisor holding the socket — every request hangs and the
# container never exits for its restart policy to act. The launcher is
# rendered into the install dir and mounted read-only, like the other
# rendered support files.
_UVICORN_LAUNCHER_SOURCE = (
    Path(__file__).resolve().parent.parent / "dev_runtime" / "uvicorn_respawn.py"
)
UVICORN_LAUNCHER_RENDER_PATH = "config/dev/uvicorn_respawn.py"
UVICORN_LAUNCHER_CONTAINER_PATH = "/etc/wip-dev/uvicorn_respawn.py"

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


def _patch_dockerfile_for_dev(content: str, *, bake_archive: bool) -> str:
    """Insert wip-auth (and optionally wip-archive) installs into a
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
    if bake_archive:
        insert.extend([
            "",
            "# Install wip-archive library (dev-bake)",
            "COPY wip-archive /tmp/wip-archive",
            "RUN pip install --no-cache-dir /tmp/wip-archive && rm -rf /tmp/wip-archive",
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
    """Copy a component's build inputs + wip-auth (+ wip-archive) into
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
    if name in _ARCHIVE_SERVICES:
        _copy_tree_into(repo_root / "libs" / "wip-archive", tree, f"{prefix}/wip-archive")

    if name in _AUTH_SERVICES:
        dockerfile_path = Path(f"{prefix}/Dockerfile")
        if dockerfile_path in tree.files:
            patched = _patch_dockerfile_for_dev(
                tree.files[dockerfile_path].content,
                bake_archive=(name in _ARCHIVE_SERVICES),
            )
            tree.add(str(dockerfile_path), patched)

    return f"./{prefix}"


def refresh_dev_build_contexts(
    install_dir: Path,
    repo_root: Path,
    components: list[Component],
    services: list[str],
) -> list[str]:
    """Re-copy the named services' build contexts from the repo into an
    existing dev install, exactly as a render would write them.

    A dev install builds each component's image from the copy the render
    made under build-contexts/<name>/ (the component plus the libraries it
    bakes in), not from the repo itself. Without a refresh, a rebuild after
    a Dockerfile or requirements edit builds the old copy and the new image
    has none of the edit. Only the named services' contexts are rewritten;
    each is replaced whole, so a file deleted in the repo does not linger.

    A service is skipped when it is not a discovered component with a
    build context, or when the install holds no build-contexts/<name>/ for
    it (it is not built from a copied context here, e.g. an app built from
    its own --app-source checkout, which a rebuild already reads live).
    Returns the names refreshed.
    """
    import shutil

    by_name = {c.metadata.name: c for c in components}
    refreshed: list[str] = []
    for name in services:
        component = by_name.get(name)
        ctx_dir = install_dir / "build-contexts" / name
        if component is None or not ctx_dir.is_dir():
            continue
        tree = FileTree()
        if _materialize_dev_build_context(component, repo_root, tree) is None:
            continue
        shutil.rmtree(ctx_dir)
        tree.write(install_dir)
        refreshed.append(name)
    return refreshed


# ────────────────────────────────────────────────────────────────────


def _mounted_source(owner: Component | App, repo_root: Path) -> Path | None:
    """The component source the dev render bind-mounts over /app/src, or
    None: a component with a build context and a `src` directory."""
    if isinstance(owner, App) or owner.spec.image.build_context is None:
        return None
    source = (repo_root / "components" / owner.metadata.name / "src").resolve()
    return source if source.exists() else None


def _hot_reloads(owner: Component | App) -> bool:
    """Whether the dev render runs the owner under the reloading launcher:
    only a uvicorn command has a watcher to restart it."""
    cmd = _command_for(owner)
    return bool(cmd and cmd[0] == "uvicorn")


def _dev_render_notes(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    repo_root: Path,
    *,
    source_mount: bool,
    app_sources: dict[str, Path],
) -> list[str]:
    """What the dev render tells the operator: each mounted component that
    does not reload (an edit needs a restart), and each --app-source app
    built from its production Dockerfile for want of a Dockerfile.dev."""
    notes: list[str] = []
    install = deployment.metadata.name
    if source_mount:
        for c in components:
            if (
                is_component_active(c, deployment)
                and _mounted_source(c, repo_root) is not None
                and not _hot_reloads(c)
            ):
                notes.append(
                    f"{c.metadata.name}: its source is mounted but it does not "
                    f"hot-reload; after an edit, `podman restart {install}_wip-{c.metadata.name}`."
                )
    enabled = {r.name for r in deployment.spec.apps if r.enabled}
    for a in apps:
        path = app_sources.get(a.metadata.name)
        if a.metadata.name in enabled and path is not None and not (path / "Dockerfile.dev").is_file():
            notes.append(
                f"{a.metadata.name}: {path} has no Dockerfile.dev, so the dev build uses its "
                "production Dockerfile, with NODE_ENV=development and the checkout mounted "
                "over /app; an app that serves its built assets only in production may not "
                "serve them. Add a Dockerfile.dev for a dev server."
            )
    return notes


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

    ctx = make_spec_context(deployment, components, apps)
    resolved_env = resolve_all_env(
        deployment, components, apps, ctx,
        collected_secrets=set(secrets.values.keys()),
    )

    tree = FileTree()
    dex_conf = render_dex_conf(deployment, components, apps, secrets)

    # Materialize a self-contained build context per Python service so
    # wip-auth (and wip-archive for document-store) get baked in the
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
            app_sources=dev_plat.app_sources,
            dex_conf=dex_conf,
            secrets=secrets,
        ),
    )

    tree.add(".env", _render_dotenv(secrets), mode=0o600)

    caddy_cfg = generate_caddy_config(deployment, components, apps)
    tree.add("config/caddy/Caddyfile", render_caddyfile(caddy_cfg))

    tree.add(UVICORN_LAUNCHER_RENDER_PATH, _UVICORN_LAUNCHER_SOURCE.read_text())
    tree.notes.extend(_dev_render_notes(
        deployment, components, apps, repo_root,
        source_mount=dev_plat.source_mount, app_sources=dev_plat.app_sources,
    ))

    # Config-file API keys (the spec's plus the canary) — same render + posture as the
    # compose target (0644 inside the 0700 install directory, mounted
    # where the manifest declares the env).
    tree.add(API_KEYS_RENDER_PATH, generate_api_keys_json(deployment, secrets, apps), atomic=True)
    tree.obsolete.add(Path(LEGACY_API_KEYS_RENDER_PATH))
    tree.add(FEDERATED_GROUPS_RENDER_PATH, federated_groups_policy(deployment), atomic=True)
    tree.add(RESERVATIONS_RENDER_PATH, namespace_reservations_json(deployment, apps), atomic=True)
    tree.add(ATTACH_POLICY_RENDER_PATH, attach_policy_json(deployment, components, apps), atomic=True)

    if dex_conf is not None:
        tree.add("config/dex/config.yaml", dex_conf)

    # Garage config — same posture as the compose renderer: the file
    # carries the rpc secret and admin token, so 0600.
    if garage_component(deployment, components) is not None:
        tree.add(
            "config/garage/garage.toml", render_garage_toml(secrets), mode=0o600,
        )

    # MongoDB start script — same as the compose renderer (no secrets).
    mongo_entrypoint = render_mongodb_entrypoint(deployment, components, apps)
    if mongo_entrypoint is not None:
        tree.add(MONGO_ENTRYPOINT_RENDER_PATH, mongo_entrypoint)

    # NATS config — same as the compose renderer (no secrets: 0644).
    nats_conf = render_nats_conf(deployment, components, apps)
    if nats_conf is not None:
        tree.add(NATS_CONFIG_RENDER_PATH, nats_conf)

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

    check_caddy_upstreams(tree)
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
    app_sources: dict[str, Path],
    dex_conf: str | None,
    secrets: ResolvedSecrets,
) -> str:
    """Same shape as compose's _render_compose_yaml but with build: blocks,
    source mounts, and uvicorn --reload.

    Every service carries compose's config-hash label too: a secret's value
    is not in the definition (env references `${VAR}`), so the hash is what
    tells a scoped apply that a service reading a rotated secret must be
    recreated (apply.changed_outside_scope)."""
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}

    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    for c in components:
        if not is_component_active(c, deployment):
            continue
        services[c.metadata.name] = _dev_service_block(
            c, deployment, resolved_env[c.metadata.name], repo_root,
            source_mount=source_mount,
            build_context_paths=build_context_paths,
            app_sources=app_sources,
        )
        _collect_volumes(c, volumes)
        if c.metadata.name == NATS_COMPONENT:
            # The bind-mounted config must recreate NATS when it changes;
            # `compose up -d` only recreates on a definition change.
            nats_conf = render_nats_conf(deployment, components, apps)
            if nats_conf is not None:
                services[c.metadata.name]["labels"] = {
                    CONFIG_HASH_KEY: nats_config_hash(nats_conf),
                }
        if c.metadata.name == MONGODB_COMPONENT:
            # Same for MongoDB's start script (its user set).
            script = render_mongodb_entrypoint(deployment, components, apps)
            if script is not None:
                services[c.metadata.name]["labels"] = {
                    CONFIG_HASH_KEY: mongodb_config_hash(script),
                }
        if c.metadata.name == DEX_COMPONENT and dex_conf is not None:
            # And Dex's config.yaml (its user and client set).
            services[c.metadata.name]["labels"] = {
                CONFIG_HASH_KEY: dex_config_hash(dex_conf),
            }
        if "labels" not in services[c.metadata.name]:
            _stamp_config_hash(services[c.metadata.name], config_hash(
                c, deployment, resolved_env[c.metadata.name], secrets,
            ))

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        services[a.metadata.name] = _dev_service_block(
            a, deployment, resolved_env[a.metadata.name], repo_root,
            source_mount=source_mount,
            build_context_paths=build_context_paths,
            app_sources=app_sources,
        )
        _stamp_config_hash(services[a.metadata.name], config_hash(
            a, deployment, resolved_env[a.metadata.name], secrets,
        ))
        _collect_volumes(a, volumes)
        # CASE-55: declare named volume for node_modules shadow so it
        # persists across --force-recreate and is shared with `run`
        # commands (needed to populate the volume via a one-off
        # `npm ci` without it being discarded).
        if a.metadata.name in app_sources:
            volumes[f"{a.metadata.name}-node-modules"] = {}

    services["caddy"] = _caddy_service_block(deployment)

    top: dict[str, Any] = {
        "name": compose_project_name(deployment.metadata.name),
        "services": services,
        "networks": network_block(deployment.metadata.name),
    }
    if volumes:
        top["volumes"] = volumes

    return yaml.safe_dump(top, sort_keys=False, default_flow_style=False)


def _stamp_config_hash(block: dict[str, Any], workload_hash: str | None) -> None:
    if workload_hash is not None:
        block["labels"] = {CONFIG_HASH_KEY: workload_hash}


def _dev_service_block(
    owner: Component | App,
    deployment: Deployment,
    env: Any,  # ResolvedEnv
    repo_root: Path,
    *,
    source_mount: bool,
    build_context_paths: dict[str, str],
    app_sources: dict[str, Path],
) -> dict[str, Any]:
    """Same as compose's _service_block but with dev-mode overrides."""
    block: dict[str, Any] = {
        "container_name": container_name(deployment.metadata.name, owner.metadata.name)
    }

    # Dev: prefer build: over image: when a build_context is declared.
    # For Python components we use the materialized per-component build
    # context under ./build-contexts/<name>/ (wip-auth baked in).
    # For apps with --app-source overrides we use the user-provided
    # local path (the app's own repo). Other apps fall back to the
    # registry image just like compose target.
    name = owner.metadata.name
    dev_plat = deployment.spec.platform.dev
    apps_from_registry: set[str] = (
        set(dev_plat.apps_from_registry) if dev_plat is not None else set()
    )
    if name in build_context_paths:
        block["build"] = {"context": build_context_paths[name]}
        if owner.spec.image.build_args:
            block["build"]["args"] = dict(owner.spec.image.build_args)
        block["image"] = f"{owner.spec.image.name}:{dev_image_tag(deployment.metadata.name)}"
    elif isinstance(owner, App) and name in app_sources:
        # CASE-55: app source override. Build the app's image locally
        # from the user's checkout. Prefer Dockerfile.dev if the app
        # provides one — that's where the app defines its own dev
        # command (e.g., `npm run dev` for React/Vite with HMR),
        # keeping renderer-side knowledge minimal.
        app_path = app_sources[name]
        build_cfg: dict[str, Any] = {"context": str(app_path)}
        if (app_path / "Dockerfile.dev").is_file():
            build_cfg["dockerfile"] = "Dockerfile.dev"
        if owner.spec.image.build_args:
            build_cfg["args"] = dict(owner.spec.image.build_args)
        block["build"] = build_cfg
        block["image"] = f"{owner.spec.image.name}:{dev_image_tag(deployment.metadata.name)}"
    else:
        build_ctx = _resolve_build_context(owner, repo_root)
        if build_ctx is not None:
            block["build"] = {"context": str(build_ctx)}
            if owner.spec.image.build_args:
                block["build"]["args"] = dict(owner.spec.image.build_args)
            block["image"] = f"{owner.spec.image.name}:{dev_image_tag(deployment.metadata.name)}"
        elif isinstance(owner, App) and name not in apps_from_registry:
            # CASE-355: dev mode means dev. An enabled app with no local
            # source (no --app-source, no build_context) silently fell
            # back to the registry image pre-CASE-355 — leading to
            # stale-image installs the operator only noticed when a
            # recent fix was absent. Refuse loudly. Operators who really
            # want the registry image in dev opt in via
            # --app-from-registry NAME.
            raise ValueError(
                f"app {name!r} is enabled but has no source for --target dev. "
                f"Either: (a) pass --app-source {name}=<path-to-checkout> to "
                f"bind-mount the app's source, (b) pass "
                f"--app-from-registry {name} to explicitly opt into the "
                f"registry image, or (c) disable the app and re-install."
            )
        else:
            block["image"] = _image_ref(owner, deployment)

    environment = _environment_block(env)
    # CASE-55: apps with --app-source run in dev mode (npm run dev), so
    # devDependencies (vite, concurrently, etc.) must be installed. The
    # manifest's NODE_ENV=production literal is correct for prod images
    # but wrong here — it makes `npm ci` skip devDeps, breaking the dev
    # command. Override to development whenever --app-source is active.
    #
    # The Vite dev server answers only the hosts it allows: localhost and IP
    # addresses unless the app's config lists more. Behind the router every
    # request carries the install's hostname, so an install reached by name
    # (wip-pi.internal) got "Blocked request. This host is not allowed" from
    # every app. Vite appends __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS to the
    # allowed hosts itself (Vite >= 5.4.12 / 6.0.9), so no app config changes.
    if isinstance(owner, App) and owner.metadata.name in app_sources:
        environment["NODE_ENV"] = "development"
        # Vite takes this as ONE host: every request carries the app's own
        # origin's host.
        allowed_host = f"{owner.app_metadata.label}.{deployment.spec.network.hostname}"
        environment.setdefault("__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS", allowed_host)
    # CASE-301: extend PYTHONPATH so the bind-mounted wip-auth source
    # shadows the build-baked site-packages copy. Without this, edits
    # to libs/wip-auth/ would only land via a full `wip-deploy install`
    # (build-context refresh + image rebuild) rather than via a simple
    # restart. The build-time pip install stays as a fallback — if the
    # bind-mount is somehow missing, the site-packages copy still lets
    # the container boot.
    # The same applies to wip-archive for the services that use it: the
    # bind-mount has to come first, or the copy baked at image build wins and
    # library edits stay invisible until someone rebuilds.
    python_path = [
        path
        for path, applies in (
            ("/app/libs/wip-auth/src", name in _AUTH_SERVICES),
            ("/app/libs/wip-archive/src", name in _ARCHIVE_SERVICES),
        )
        if applies
    ]
    if python_path:
        environment["PYTHONPATH"] = ":".join([*python_path, "/app/src"])
    if environment:
        block["environment"] = environment

    # No `env_file:` — see compose.py: secrets reach the container only
    # through the `${VAR}` entries its manifest declares.

    # Dev: if the command is uvicorn-based, run it through the respawning
    # launcher (see _UVICORN_LAUNCHER_SOURCE), append --reload for hot reload,
    # AND force the reload watcher to poll (CASE-523). uvicorn --reload's watcher
    # (watchfiles) uses native inotify by default, which does NOT propagate across
    # the macOS podman virtiofs/9p bind mount — so --reload is a silent no-op there
    # (edits land on disk but the running process never reloads, manufacturing
    # "the fix isn't deployed" misdiagnoses). WATCHFILES_FORCE_POLLING is the
    # backend analogue of the apps' CHOKIDAR_USEPOLLING.
    cmd = _command_for(owner)
    uses_launcher = _hot_reloads(owner)
    if cmd:
        if uses_launcher:
            if "--reload" not in cmd:
                cmd = [*cmd, "--reload"]
            cmd = ["python", UVICORN_LAUNCHER_CONTAINER_PATH, *cmd[1:]]
            block.setdefault("environment", {})["WATCHFILES_FORCE_POLLING"] = "1"
        # See compose.py for the entrypoint/command split rationale:
        # compose's `command:` only overrides CMD, so full argv needs to
        # go to `entrypoint:` to replace the image's ENTRYPOINT.
        block["entrypoint"] = [cmd[0]]
        block["command"] = cmd[1:]

    # Dev: mount source directories for build-context components so edits
    # show up inside the container without a rebuild.
    volumes = _dev_volumes_for(
        owner, repo_root,
        enable_source_mount=source_mount,
        app_sources=app_sources,
    )
    if mounts_auth_config_dir(owner):
        volumes.append(f"./{API_KEYS_RENDER_DIR}:{API_KEYS_CONTAINER_DIR}:ro")
    # The key file's own directory, for the manifests that read it (the
    # Registry): no other container holds the plaintext.
    if declares_api_keys_file(owner):
        volumes.append(f"./{KEY_FILE_RENDER_DIR}:{KEY_FILE_CONTAINER_DIR}:ro")
    if uses_launcher:
        volumes.append(
            f"./{UVICORN_LAUNCHER_RENDER_PATH}:{UVICORN_LAUNCHER_CONTAINER_PATH}:ro"
        )
    if volumes:
        block["volumes"] = volumes

    block["networks"] = service_networks(owner.metadata.name)
    block["restart"] = "unless-stopped"

    hc = _healthcheck_block(owner)
    if hc is not None:
        block["healthcheck"] = hc

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
    owner: Component | App,
    repo_root: Path,
    *,
    enable_source_mount: bool,
    app_sources: dict[str, Path],
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
    elif owner.metadata.name == "garage":
        volumes.append("./config/garage/garage.toml:/etc/garage.toml:ro")
    elif owner.metadata.name == NATS_COMPONENT:
        volumes.append(f"./{NATS_CONFIG_RENDER_PATH}:{NATS_CONFIG_CONTAINER_PATH}:ro")
    elif owner.metadata.name == MONGODB_COMPONENT:
        volumes.append(f"./{MONGO_ENTRYPOINT_RENDER_PATH}:{MONGO_ENTRYPOINT_CONTAINER_PATH}:ro")

    # Router config bind mount — same as prod. Without this, wip-router
    # starts with the stock caddy default Caddyfile and all /api/* routing
    # breaks (502 Bad Gateway from SSR-proxied app calls).
    if owner.metadata.name == "router":
        volumes.append("./config/router/Caddyfile:/etc/caddy/Caddyfile:ro")

    # Dev-specific: source mount for Python services.
    if enable_source_mount and not isinstance(owner, App):
        source = _mounted_source(owner, repo_root)
        if source is not None:
            volumes.append(f"{source}:/app/src:ro")
        # CASE-301: also bind-mount libs/wip-auth/src for components that
        # use wip_auth, so library edits propagate via a `restart` instead
        # of forcing a full `wip-deploy install` to refresh the
        # build-context. The PYTHONPATH override above (set in
        # _dev_service_block) makes Python find this bind-mount before
        # falling through to the site-packages copy installed at build
        # time.
        if owner.metadata.name in _AUTH_SERVICES:
            wip_auth_src = (repo_root / "libs" / "wip-auth" / "src").resolve()
            if wip_auth_src.is_dir():
                volumes.append(f"{wip_auth_src}:/app/libs/wip-auth/src:ro")
        # Same treatment for wip-archive, for the same reason. Without it,
        # wip-auth edits propagated on a restart while archive-lib edits silently
        # did not — the service kept running the copy pip-installed at image
        # build, and the only symptom was behaviour that did not match the
        # source in front of you. Found the hard way: a restore mode calling a
        # library function added the same day failed with AttributeError
        # against a container whose code was days old.
        if owner.metadata.name in _ARCHIVE_SERVICES:
            archive_src = (repo_root / "libs" / "wip-archive" / "src").resolve()
            if archive_src.is_dir():
                volumes.append(f"{archive_src}:/app/libs/wip-archive/src:ro")

    # CASE-55: app source-mount for --app-source overrides. Mount the
    # entire app directory into /app (including package.json + src/ +
    # node_modules if present). The container's dev command (defined by
    # the app's Dockerfile.dev via `npm run dev` or equivalent) reads
    # from /app and watches for changes. `rw` because dev servers often
    # write build artifacts; read-only is too strict for most Node dev
    # loops.
    #
    # Node_modules shadow: a NAMED volume `<name>-node-modules` at
    # /app/node_modules shadows the host's node_modules directory.
    # Without this, macOS darwin-arm64 binaries (esbuild native module,
    # etc.) leak into the linux-arm64 container and fail to execute;
    # worse, the container's first-run `npm install` would overwrite
    # the host's node_modules with linux builds, breaking host-side
    # `npm run dev`.
    #
    # Named (not anonymous): podman-compose's `run --rm` scopes
    # anonymous volumes to the run-container, so a one-off install
    # can't populate the service container's node_modules. A named
    # volume is shared across all service invocations (up, run, exec)
    # and persists across --force-recreate — so `podman-compose run
    # --rm react-console npm ci` populates it once, then every
    # subsequent start sees a populated node_modules.
    if isinstance(owner, App) and owner.metadata.name in app_sources:
        app_path = app_sources[owner.metadata.name]
        volumes.append(f"{app_path}:/app:rw")
        volumes.append(f"{owner.metadata.name}-node-modules:/app/node_modules")

    return volumes


# Re-export for potential external use; the name aligns with the module's
# `render_*` convention.
__all__ = ["render_dev_simple"]
