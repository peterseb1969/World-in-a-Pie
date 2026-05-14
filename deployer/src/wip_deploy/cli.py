"""wip-deploy CLI entrypoint.

Step 3 scope: `validate` and `show-spec` verbs. Renderers + `install` /
`upgrade` / `render` come in later steps.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer
import yaml

from wip_deploy import __version__
from wip_deploy.apply import ApplyError, apply_compose, apply_k8s
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import discover, find_repo_root
from wip_deploy.export_ca import (
    TRUST_INSTRUCTIONS,
    ExportCAError,
    export_caddy_internal_ca,
)
from wip_deploy.nuke import NukeError, nuke_install_dir, nuke_purge_all
from wip_deploy.presets import PRESETS
from wip_deploy.renderers import (
    FileTree,
    render_compose,
    render_dev_simple,
    render_k8s,
)
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component
from wip_deploy.spec.deployment import AppRef
from wip_deploy.spec.validators import validate_all

app = typer.Typer(
    help="WIP declarative deployer (v2).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
    epilog=(
        "Try 'wip-deploy examples' for common workflows. "
        "Run 'wip-deploy COMMAND --help' for command-specific options."
    ),
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wip-deploy {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            help="Show version and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    pass


# ────────────────────────────────────────────────────────────────────
# Shared option factories (keep verb signatures tight)
# ────────────────────────────────────────────────────────────────────


# Typer Option factories — no defaults inside Option (Annotated-style usage).
# Defaults live in the function signature. Each returns a fresh Option so it
# can be reused across commands without click's "Name defined twice" error.


def _preset_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--preset", "-p",
        help=f"Preset name. One of: {', '.join(sorted(PRESETS))}.",
    )


def _target_opt() -> typer.models.OptionInfo:
    return typer.Option("--target", "-t", help="Deployment target: compose | k8s | dev.")


def _hostname_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--hostname",
        help=(
            "External hostname as seen by browsers. Defaults to "
            "'localhost' when --target dev (no /etc/hosts magic) and "
            "'wip.local' otherwise."
        ),
    )


def _resolve_hostname(hostname: str | None, target: str) -> str:
    """Resolve --hostname's default based on target.

    - target=dev defaults to 'localhost' (browser-reachable on the
      same machine, no /etc/hosts entry required).
    - target=compose|k8s defaults to 'wip.local' for backwards
      compatibility (operator typically picks a real hostname).

    An explicit --hostname always overrides.
    """
    if hostname is not None:
        return hostname
    if target == "dev":
        return "localhost"
    return "wip.local"


def _tls_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--tls",
        help=(
            "TLS mode: internal | letsencrypt | external | self-signed. "
            "Default 'internal' is auto-upgraded to 'self-signed' for "
            "--target k8s (deployer generates a cert + Secret pre-install)."
        ),
    )


def _https_port_opt() -> typer.models.OptionInfo:
    return typer.Option("--https-port", help="External HTTPS port (compose).")


def _http_port_opt() -> typer.models.OptionInfo:
    return typer.Option("--http-port", help="External HTTP port (compose).")


def _data_dir_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--data-dir", help="Compose data directory. Defaults to <repo-root>/data."
    )


def _namespace_opt() -> typer.models.OptionInfo:
    return typer.Option("--namespace", help="K8s namespace.")


def _storage_class_opt() -> typer.models.OptionInfo:
    return typer.Option("--storage-class", help="K8s StorageClass for PVCs.")


def _ingress_class_opt() -> typer.models.OptionInfo:
    return typer.Option("--ingress-class", help="K8s IngressClass name.")


def _tls_secret_opt() -> typer.models.OptionInfo:
    return typer.Option("--tls-secret-name", help="K8s TLS Secret name.")


def _dev_mode_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--dev-mode",
        help="Dev mode: simple (compose + source mounts + --reload) | tilt (reserved, not implemented).",
    )


def _registry_opt() -> typer.models.OptionInfo:
    return typer.Option("--registry", help="Image registry (e.g., ghcr.io/you).")


def _tag_opt() -> typer.models.OptionInfo:
    return typer.Option("--tag", help="Image tag.")


def _add_opt() -> typer.models.OptionInfo:
    return typer.Option("--add", help="Add an optional module (repeatable).")


def _remove_opt() -> typer.models.OptionInfo:
    return typer.Option("--remove", help="Remove an optional module (repeatable).")


def _app_opt() -> typer.models.OptionInfo:
    return typer.Option("--app", help="Enable an app by name (repeatable).")


def _app_source_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--app-source",
        help=(
            "Dev-target only. Mount a local source checkout as the app's "
            "build context instead of pulling the registry image. "
            "Format: NAME=PATH. Repeatable. Example: "
            "--app-source clintrial=/Users/peter/Development/WIP-ClinTrial. "
            "If <PATH>/Dockerfile.dev exists, it's preferred over Dockerfile "
            "so the app can define its own dev-mode command (e.g., `npm run dev`). "
            "Implicitly enables the named app (no separate --app needed). "
            "Ignored for target!=dev."
        ),
    )


def _app_from_registry_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--app-from-registry",
        help=(
            "Dev-target only. Explicitly opt the named app into the "
            "registry-image fallback in dev mode (CASE-355). Without "
            "this, an enabled app with no --app-source and no local "
            "build context fails the dev install loudly. Repeatable. "
            "Example: --app-from-registry clintrial. "
            "Implicitly enables the named app (no separate --app "
            "needed). Ignored for target!=dev."
        ),
    )


def _remote_wip_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--remote-wip",
        help=(
            "URL of a remote WIP install this install's apps should "
            "talk to (e.g., https://wip-pi.local:8443). Apps that "
            "declare WIP_BASE_URL via `from_spec: network.external_base_url` "
            "resolve to this URL instead of the local install's public "
            "URL. Use for cross-host scenarios — Console-on-Mac pointing "
            "at WIP-on-Pi. CASE-358. Compose with --apps-only (CASE-359) "
            "for a true apps-only install with no local backend stack."
        ),
    )


def _apps_only_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--apps-only",
        help=(
            "Install ONLY the named apps + Caddy — no core services, "
            "no mongodb, no router, no Dex, no auth-gateway. Use for "
            "cross-host scenarios where this install's apps talk to a "
            "remote WIP via --remote-wip. Implies --auth-mode "
            "api-key-only and --auth-gateway false (no Dex available "
            "locally; /apps/* is publicly served — appropriate for a "
            "personal device). Requires at least one --app. CASE-359."
        ),
    )


def _parse_app_sources_or_exit(raw: list[str]) -> dict[str, Path]:
    """CLI-facing wrapper around `_parse_app_sources` that reports parse
    errors via typer and exits 2 on failure."""
    try:
        return _parse_app_sources(raw)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e


def _parse_app_sources(raw: list[str]) -> dict[str, Path]:
    """Parse `--app-source NAME=PATH` entries. Raise ValueError on bad input."""
    out: dict[str, Path] = {}
    for entry in raw:
        if "=" not in entry:
            raise ValueError(
                f"--app-source expects NAME=PATH, got {entry!r} (missing '=')"
            )
        name, path_str = entry.split("=", 1)
        name = name.strip()
        path_str = path_str.strip()
        if not name or not path_str:
            raise ValueError(
                f"--app-source expects non-empty NAME and PATH, got {entry!r}"
            )
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(
                f"--app-source {name!r}: path {path!s} is not a directory"
            )
        out[name] = path
    return out


def _auth_mode_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--auth-mode", help="Override auth mode: oidc | api-key-only | hybrid."
    )


def _auth_gateway_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--auth-gateway/--no-auth-gateway",
        help="Override auth gateway on/off.",
    )


def _secrets_backend_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--secrets-backend", help="Secret backend: file | k8s-secret | sops."
    )


def _secrets_location_opt() -> typer.models.OptionInfo:
    return typer.Option("--secrets-location", help="Secret backend location.")


def _repo_root_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--repo-root", help="Repo root (auto-detected from .git if omitted)."
    )


def _name_opt() -> typer.models.OptionInfo:
    return typer.Option(
        "--name",
        help=(
            "Deployment name. Determines the install dir "
            "(~/.wip-deploy/<name>/). When unspecified, defaults to "
            "the --namespace value for --target k8s, and to 'default' "
            "otherwise — so `--namespace wip-kb --target k8s` lands at "
            "~/.wip-deploy/wip-kb/ without needing to pass --name twice."
        ),
    )


def _resolve_name(
    name: str | None,
    *,
    target: str | None = None,
    namespace: str | None = None,
) -> str:
    """Resolve --name's sentinel to an effective deployment name.

    - User-provided name: use as-is.
    - Unspecified name + k8s target with a namespace: use the namespace
      (so install/render/etc. land at ~/.wip-deploy/<namespace>/).
    - Unspecified name otherwise: 'default' (matches the historical
      behaviour for compose/dev installs).

    Verbs that don't take --target / --namespace (status, rebuild,
    restart, nuke) call this with just `name`, falling through to
    'default' for the unspecified case.
    """
    if name is not None:
        return name
    if target == "k8s" and namespace:
        return namespace
    return "default"


# ────────────────────────────────────────────────────────────────────
# validate
# ────────────────────────────────────────────────────────────────────


@app.command()
def validate(
    preset: Annotated[str, _preset_opt()] = "standard",
    target: Annotated[str, _target_opt()] = "compose",
    hostname: Annotated[str | None, _hostname_opt()] = None,
    tls: Annotated[str, _tls_opt()] = "internal",
    https_port: Annotated[int | None, _https_port_opt()] = None,
    http_port: Annotated[int | None, _http_port_opt()] = None,
    data_dir: Annotated[Path | None, _data_dir_opt()] = None,
    namespace: Annotated[str, _namespace_opt()] = "wip",
    storage_class: Annotated[str, _storage_class_opt()] = "rook-ceph-block",
    ingress_class: Annotated[str, _ingress_class_opt()] = "nginx",
    tls_secret_name: Annotated[str, _tls_secret_opt()] = "wip-tls",
    dev_mode: Annotated[str, _dev_mode_opt()] = "simple",
    registry: Annotated[str | None, _registry_opt()] = None,
    tag: Annotated[str, _tag_opt()] = "latest",
    add: Annotated[list[str], _add_opt()] = [],
    remove: Annotated[list[str], _remove_opt()] = [],
    apps: Annotated[list[str], _app_opt()] = [],
    app_source: Annotated[list[str], _app_source_opt()] = [],
    app_from_registry: Annotated[list[str], _app_from_registry_opt()] = [],
    remote_wip: Annotated[str | None, _remote_wip_opt()] = None,
    apps_only: Annotated[bool, _apps_only_opt()] = False,
    auth_mode: Annotated[str | None, _auth_mode_opt()] = None,
    auth_gateway: Annotated[bool | None, _auth_gateway_opt()] = None,
    secrets_backend: Annotated[str | None, _secrets_backend_opt()] = None,
    secrets_location: Annotated[str | None, _secrets_location_opt()] = None,
    repo_root: Annotated[Path | None, _repo_root_opt()] = None,
    name: Annotated[str | None, _name_opt()] = None,
) -> None:
    """Validate a deployment configuration without rendering or applying.

    Builds the Deployment from preset + flags, discovers every
    wip-component.yaml / wip-app.yaml, and runs all cross-cutting
    validators. Exit 0 on success, 1 on failure.

    Examples:

      wip-deploy validate
      wip-deploy validate --preset full --target k8s
      wip-deploy validate --hostname wip.example.com --tls letsencrypt
    """
    hostname = _resolve_hostname(hostname, target)
    name = _resolve_name(name, target=target, namespace=namespace)
    deployment, components, apps_list = _assemble(
        preset=preset,
        target=target,
        hostname=hostname,
        tls=tls,
        https_port=https_port,
        http_port=http_port,
        data_dir=data_dir,
        namespace=namespace,
        storage_class=storage_class,
        ingress_class=ingress_class,
        tls_secret_name=tls_secret_name,
        dev_mode=dev_mode,
        registry=registry,
        tag=tag,
        add=add,
        remove=remove,
        apps=apps,
        app_sources=_parse_app_sources_or_exit(app_source),
        apps_from_registry=list(app_from_registry),
        remote_wip_url=remote_wip,
        apps_only=apps_only,
        auth_mode=auth_mode,
        auth_gateway=auth_gateway,
        secrets_backend=secrets_backend,
        secrets_location=secrets_location,
        repo_root=repo_root,
        name=name,
    )

    # Summary
    active = sorted(
        c.metadata.name for c in components if is_component_active(c, deployment)
    )
    enabled_apps = sorted(a.name for a in deployment.spec.apps if a.enabled)

    typer.echo(f"Preset:      {preset}")
    typer.echo(f"Target:      {target}")
    typer.echo(f"Hostname:    {hostname}")
    typer.echo(f"Auth:        mode={deployment.spec.auth.mode} gateway={deployment.spec.auth.gateway}")
    typer.echo(
        f"Components:  {len(active):>2} active  —  {', '.join(active) if active else '—'}"
    )
    typer.echo(
        f"Apps:        {len(enabled_apps):>2} enabled —  {', '.join(enabled_apps) if enabled_apps else '—'}"
    )
    typer.echo("")

    # Cross-cutting
    report = validate_all(deployment, components, apps_list)
    if report.ok:
        typer.echo(typer.style("✓ Deployment valid.", fg=typer.colors.GREEN, bold=True))
        raise typer.Exit(0)

    typer.echo(
        typer.style(
            f"✗ Validation failed ({len(report.errors)} error(s)):",
            fg=typer.colors.RED,
            bold=True,
        ),
        err=True,
    )
    for err in report.errors:
        typer.echo(f"  - {err}", err=True)
    raise typer.Exit(1)


# ────────────────────────────────────────────────────────────────────
# show-spec
# ────────────────────────────────────────────────────────────────────


@app.command("show-spec")
def show_spec(
    preset: Annotated[str, _preset_opt()] = "standard",
    target: Annotated[str, _target_opt()] = "compose",
    hostname: Annotated[str | None, _hostname_opt()] = None,
    tls: Annotated[str, _tls_opt()] = "internal",
    https_port: Annotated[int | None, _https_port_opt()] = None,
    http_port: Annotated[int | None, _http_port_opt()] = None,
    data_dir: Annotated[Path | None, _data_dir_opt()] = None,
    namespace: Annotated[str, _namespace_opt()] = "wip",
    storage_class: Annotated[str, _storage_class_opt()] = "rook-ceph-block",
    ingress_class: Annotated[str, _ingress_class_opt()] = "nginx",
    tls_secret_name: Annotated[str, _tls_secret_opt()] = "wip-tls",
    dev_mode: Annotated[str, _dev_mode_opt()] = "simple",
    registry: Annotated[str | None, _registry_opt()] = None,
    tag: Annotated[str, _tag_opt()] = "latest",
    add: Annotated[list[str], _add_opt()] = [],
    remove: Annotated[list[str], _remove_opt()] = [],
    apps: Annotated[list[str], _app_opt()] = [],
    app_source: Annotated[list[str], _app_source_opt()] = [],
    app_from_registry: Annotated[list[str], _app_from_registry_opt()] = [],
    remote_wip: Annotated[str | None, _remote_wip_opt()] = None,
    apps_only: Annotated[bool, _apps_only_opt()] = False,
    auth_mode: Annotated[str | None, _auth_mode_opt()] = None,
    auth_gateway: Annotated[bool | None, _auth_gateway_opt()] = None,
    secrets_backend: Annotated[str | None, _secrets_backend_opt()] = None,
    secrets_location: Annotated[str | None, _secrets_location_opt()] = None,
    repo_root: Annotated[Path | None, _repo_root_opt()] = None,
    name: Annotated[str | None, _name_opt()] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="yaml | json")
    ] = "yaml",
) -> None:
    """Build the Deployment from preset + flags and dump it.

    Useful for debugging: "what does --preset standard actually resolve to?"
    No discovery, no validation — just the computed spec.

    Examples:

      wip-deploy show-spec --preset standard
      wip-deploy show-spec --preset full --format json | jq '.spec.apps'
      wip-deploy show-spec --target dev
    """
    hostname = _resolve_hostname(hostname, target)
    name = _resolve_name(name, target=target, namespace=namespace)
    deployment, _components, _apps = _assemble(
        preset=preset,
        target=target,
        hostname=hostname,
        tls=tls,
        https_port=https_port,
        http_port=http_port,
        data_dir=data_dir,
        namespace=namespace,
        storage_class=storage_class,
        ingress_class=ingress_class,
        tls_secret_name=tls_secret_name,
        dev_mode=dev_mode,
        registry=registry,
        tag=tag,
        add=add,
        remove=remove,
        apps=apps,
        app_sources=_parse_app_sources_or_exit(app_source),
        apps_from_registry=list(app_from_registry),
        remote_wip_url=remote_wip,
        apps_only=apps_only,
        auth_mode=auth_mode,
        auth_gateway=auth_gateway,
        secrets_backend=secrets_backend,
        secrets_location=secrets_location,
        repo_root=repo_root,
        name=name,
        skip_discovery=True,
    )

    dumped = deployment.model_dump(mode="json")
    if output_format == "json":
        import json

        typer.echo(json.dumps(dumped, indent=2, default=str))
    else:
        typer.echo(yaml.safe_dump(dumped, sort_keys=False))


# ────────────────────────────────────────────────────────────────────
# render
# ────────────────────────────────────────────────────────────────────


@app.command()
def render(
    preset: Annotated[str, _preset_opt()] = "standard",
    target: Annotated[str, _target_opt()] = "compose",
    hostname: Annotated[str | None, _hostname_opt()] = None,
    tls: Annotated[str, _tls_opt()] = "internal",
    https_port: Annotated[int | None, _https_port_opt()] = None,
    http_port: Annotated[int | None, _http_port_opt()] = None,
    data_dir: Annotated[Path | None, _data_dir_opt()] = None,
    namespace: Annotated[str, _namespace_opt()] = "wip",
    storage_class: Annotated[str, _storage_class_opt()] = "rook-ceph-block",
    ingress_class: Annotated[str, _ingress_class_opt()] = "nginx",
    tls_secret_name: Annotated[str, _tls_secret_opt()] = "wip-tls",
    dev_mode: Annotated[str, _dev_mode_opt()] = "simple",
    registry: Annotated[str | None, _registry_opt()] = None,
    tag: Annotated[str, _tag_opt()] = "latest",
    add: Annotated[list[str], _add_opt()] = [],
    remove: Annotated[list[str], _remove_opt()] = [],
    apps: Annotated[list[str], _app_opt()] = [],
    app_source: Annotated[list[str], _app_source_opt()] = [],
    app_from_registry: Annotated[list[str], _app_from_registry_opt()] = [],
    remote_wip: Annotated[str | None, _remote_wip_opt()] = None,
    apps_only: Annotated[bool, _apps_only_opt()] = False,
    auth_mode: Annotated[str | None, _auth_mode_opt()] = None,
    auth_gateway: Annotated[bool | None, _auth_gateway_opt()] = None,
    secrets_backend: Annotated[str | None, _secrets_backend_opt()] = None,
    secrets_location: Annotated[str | None, _secrets_location_opt()] = None,
    repo_root: Annotated[Path | None, _repo_root_opt()] = None,
    name: Annotated[str | None, _name_opt()] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Where to write the rendered tree. Default: ~/.wip-deploy/<name>/",
        ),
    ] = None,
) -> None:
    """Render the deployment to a directory without applying.

    Useful for inspection: look at the generated docker-compose.yaml,
    Caddyfile, and Dex config before starting the stack.

    Examples:

      wip-deploy render --preset standard
      wip-deploy render --preset full --output-dir /tmp/wip-render
      wip-deploy render --target k8s --namespace wip
    """
    hostname = _resolve_hostname(hostname, target)
    name = _resolve_name(name, target=target, namespace=namespace)
    deployment, components, apps_list = _assemble(
        preset=preset,
        target=target,
        hostname=hostname,
        tls=tls,
        https_port=https_port,
        http_port=http_port,
        data_dir=data_dir,
        namespace=namespace,
        storage_class=storage_class,
        ingress_class=ingress_class,
        tls_secret_name=tls_secret_name,
        dev_mode=dev_mode,
        registry=registry,
        tag=tag,
        add=add,
        remove=remove,
        apps=apps,
        app_sources=_parse_app_sources_or_exit(app_source),
        apps_from_registry=list(app_from_registry),
        remote_wip_url=remote_wip,
        apps_only=apps_only,
        auth_mode=auth_mode,
        auth_gateway=auth_gateway,
        secrets_backend=secrets_backend,
        secrets_location=secrets_location,
        repo_root=repo_root,
        name=name,
    )

    _validate_or_exit(deployment, components, apps_list)
    install_dir = output_dir or _default_install_dir(name)

    secrets = _ensure_secrets_via_spec(deployment, components, apps_list)
    # CASE-355: render-time errors (e.g. dev app without source) surface
    # as a clean actionable message instead of a Python traceback.
    try:
        tree = _render_tree(deployment, components, apps_list, secrets)
    except ValueError as e:
        typer.echo(
            typer.style(f"✗ render aborted: {e}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(2) from e

    install_dir.mkdir(parents=True, exist_ok=True)
    tree.write(install_dir)

    typer.echo(typer.style(f"✓ Rendered to {install_dir}", fg=typer.colors.GREEN))
    for path in tree.paths():
        typer.echo(f"  {path}")


# ────────────────────────────────────────────────────────────────────
# install
# ────────────────────────────────────────────────────────────────────


@app.command()
def install(
    preset: Annotated[str, _preset_opt()] = "standard",
    target: Annotated[str, _target_opt()] = "compose",
    hostname: Annotated[str | None, _hostname_opt()] = None,
    tls: Annotated[str, _tls_opt()] = "internal",
    https_port: Annotated[int | None, _https_port_opt()] = None,
    http_port: Annotated[int | None, _http_port_opt()] = None,
    data_dir: Annotated[Path | None, _data_dir_opt()] = None,
    namespace: Annotated[str, _namespace_opt()] = "wip",
    storage_class: Annotated[str, _storage_class_opt()] = "rook-ceph-block",
    ingress_class: Annotated[str, _ingress_class_opt()] = "nginx",
    tls_secret_name: Annotated[str, _tls_secret_opt()] = "wip-tls",
    dev_mode: Annotated[str, _dev_mode_opt()] = "simple",
    registry: Annotated[str | None, _registry_opt()] = None,
    tag: Annotated[str, _tag_opt()] = "latest",
    add: Annotated[list[str], _add_opt()] = [],
    remove: Annotated[list[str], _remove_opt()] = [],
    apps: Annotated[list[str], _app_opt()] = [],
    app_source: Annotated[list[str], _app_source_opt()] = [],
    app_from_registry: Annotated[list[str], _app_from_registry_opt()] = [],
    remote_wip: Annotated[str | None, _remote_wip_opt()] = None,
    apps_only: Annotated[bool, _apps_only_opt()] = False,
    auth_mode: Annotated[str | None, _auth_mode_opt()] = None,
    auth_gateway: Annotated[bool | None, _auth_gateway_opt()] = None,
    secrets_backend: Annotated[str | None, _secrets_backend_opt()] = None,
    secrets_location: Annotated[str | None, _secrets_location_opt()] = None,
    repo_root: Annotated[Path | None, _repo_root_opt()] = None,
    name: Annotated[str | None, _name_opt()] = None,
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help="Where to materialize the stack. Default: ~/.wip-deploy/<name>/",
        ),
    ] = None,
    no_wait: Annotated[
        bool,
        typer.Option("--no-wait", help="Skip waiting for healthy."),
    ] = False,
    wait_timeout: Annotated[
        int | None,
        typer.Option(
            "--wait-timeout", help="Override spec.apply.timeout_seconds."
        ),
    ] = None,
    reconcile: Annotated[
        bool,
        typer.Option(
            "--reconcile",
            help=(
                "Allow this install to remove apps or optional modules "
                "that were present in the previous install but absent "
                "from the current invocation. Without this flag, an "
                "install that would drop state aborts with an actionable "
                "error listing the items at risk (CASE-331 Fix B)."
            ),
        ),
    ] = False,
) -> None:
    """Build → validate → render → apply. The end-to-end install verb.

    Generates secrets on first run (persists to the secret backend);
    re-runs pick up existing values and don't regenerate.

    Examples:

      # Quick localhost dev (no /etc/hosts magic, source bind-mounted, --reload)
      wip-deploy install --target dev --preset standard

      # First Pi install with self-signed TLS
      wip-deploy install --hostname wip-pi.local --preset standard

      # Pi or cloud install with Let's Encrypt
      wip-deploy install --hostname wip.example.com --tls letsencrypt

      # Hot-reload an app from a local checkout
      wip-deploy install --target dev --app-source react-console=$HOME/Dev/WIP-ReactConsole

      # Override image tag for one app (skip the manifest pin)
      wip-deploy install --tag v1.2.0 --app react-console

      # Render against K8s instead of compose
      wip-deploy install --target k8s --namespace wip --tls external

    Run 'wip-deploy examples' for more workflows.
    """
    if target not in ("compose", "k8s", "dev"):
        typer.echo(
            f"error: install supports target=compose|k8s|dev (got {target!r})",
            err=True,
        )
        raise typer.Exit(2)

    hostname = _resolve_hostname(hostname, target)
    name = _resolve_name(name, target=target, namespace=namespace)
    deployment, components, apps_list = _assemble(
        preset=preset,
        target=target,
        hostname=hostname,
        tls=tls,
        https_port=https_port,
        http_port=http_port,
        data_dir=data_dir,
        namespace=namespace,
        storage_class=storage_class,
        ingress_class=ingress_class,
        tls_secret_name=tls_secret_name,
        dev_mode=dev_mode,
        registry=registry,
        tag=tag,
        add=add,
        remove=remove,
        apps=apps,
        app_sources=_parse_app_sources_or_exit(app_source),
        apps_from_registry=list(app_from_registry),
        remote_wip_url=remote_wip,
        apps_only=apps_only,
        auth_mode=auth_mode,
        auth_gateway=auth_gateway,
        secrets_backend=secrets_backend,
        secrets_location=secrets_location,
        repo_root=repo_root,
        name=name,
    )

    _validate_or_exit(deployment, components, apps_list)

    # Apply CLI flag overrides on top of spec.apply.
    if no_wait:
        deployment.spec.apply.wait = False
    if wait_timeout is not None:
        deployment.spec.apply.timeout_seconds = wait_timeout

    target_dir = install_dir or _default_install_dir(name)

    # CASE-331 Fix B — refuse to silently drop apps or optional modules
    # that were in the previous install but absent from this invocation.
    # The pre-CASE-331 behaviour: running `wip-deploy install` with
    # reduced flags (e.g., omitting --app-source for an app that was
    # previously installed) silently removed that state. Compare new
    # spec against the persisted previous spec and abort with an
    # actionable error if anything is going away — unless --reconcile
    # is set, which is the deliberate "yes I really mean to drop these"
    # opt-in.
    previous = _try_load_previous_deployment(target_dir)
    if previous is not None:
        dropped_apps, dropped_modules = _diff_spec_for_drops(previous, deployment)
        if (dropped_apps or dropped_modules) and not reconcile:
            _print_drop_warning_and_exit(
                dropped_apps=dropped_apps,
                dropped_modules=dropped_modules,
            )

    # Preflight: catch port conflicts and stale containers before any work
    # happens. Only applies to compose/dev targets that bind host ports;
    # k8s installs happen inside the cluster.
    if deployment.spec.target in ("compose", "dev"):
        _run_preflight_or_exit(deployment, target_dir)

    # Clean up legacy CASE-294 state file before apply. Earlier builds
    # wrote `deployment.json` into the install dir; kubectl apply -R -f
    # picks it up as a (broken) manifest. _persist_deployment writes
    # to `deployment.deployer-state` now, but if a stale legacy file
    # lingers from a prior install we drop it here so apply doesn't
    # trip on it.
    legacy_state = target_dir / "deployment.json"
    if legacy_state.exists():
        legacy_state.unlink()

    secrets = _ensure_secrets_via_spec(deployment, components, apps_list)
    # CASE-355: render-time errors (e.g., dev app without source) bubble
    # as ValueError from the renderer. Surface them as a clean
    # actionable message + exit 2, not as a traceback.
    try:
        tree = _render_tree(deployment, components, apps_list, secrets)
    except ValueError as e:
        typer.echo(
            typer.style(f"✗ install aborted: {e}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(2) from e

    typer.echo(f"Install dir: {target_dir}")
    typer.echo(
        f"Services:    {len([c for c in components if is_component_active(c, deployment)])} active, "
        f"{len([a for a in apps_list if a.metadata.name in {ref.name for ref in deployment.spec.apps if ref.enabled}])} apps"
    )
    typer.echo("")

    apply_fn = apply_k8s if deployment.spec.target == "k8s" else apply_compose
    try:
        result = apply_fn(
            deployment=deployment,
            components=components,
            apps=apps_list,
            tree=tree,
            install_dir=target_dir,
        )
    except ApplyError as e:
        typer.echo(
            typer.style(f"✗ install failed: {e}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(1) from e

    # Persist the Deployment so `wip-deploy status --diff` can re-render
    # against the same spec without needing the install args. Written
    # post-apply because a failed install leaves the prior deployment.json
    # in place (last-known-good state for diff).
    _persist_deployment(deployment, target_dir)

    typer.echo("")
    if result.healthy:
        typer.echo(typer.style("✓ Install complete.", fg=typer.colors.GREEN, bold=True))
    else:
        typer.echo(
            typer.style(
                "⚠ Install finished, but not all services reached healthy",
                fg=typer.colors.YELLOW,
                bold=True,
            )
        )
    scheme_port = deployment.spec.network.https_port
    scheme_suffix = "" if scheme_port in (443,) else f":{scheme_port}"
    typer.echo(f"  https://{deployment.spec.network.hostname}{scheme_suffix}")


# ────────────────────────────────────────────────────────────────────
# status
# ────────────────────────────────────────────────────────────────────


@app.command()
def status(
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help=(
                "Install directory for compose/dev targets. Defaults to "
                "~/.wip-deploy/<name>/ when --name is used."
            ),
        ),
    ] = None,
    name: Annotated[str | None, _name_opt()] = None,
    namespace: Annotated[
        str | None,
        typer.Option(
            "--namespace", "-n",
            help=(
                "Kubernetes namespace to query. When set, queries k8s "
                "instead of compose. Requires kubectl to be configured "
                "against the target cluster."
            ),
        ),
    ] = None,
    diff: Annotated[
        bool,
        typer.Option(
            "--diff",
            help=(
                "Re-render manifests from the persisted spec and run "
                "`kubectl diff` against the live cluster. K8s only. "
                "Exits 0 on no drift, 1 on drift, 2 on error. Requires "
                "an install dir with a deployment.json (written by "
                "`wip-deploy install`)."
            ),
        ),
    ] = False,
) -> None:
    """Print a compact table of deployed services and their health.

    Compose/dev: reads `podman-compose ps` from the install directory.
    K8s: reads `kubectl get pods` from the given namespace.

    With `--diff` (k8s only): re-renders manifests from the persisted
    spec and shells out to `kubectl diff` against the live cluster.
    Catches drift from kubectl one-shots, deployer-side renderer
    changes, and edits to wip-component.yaml that haven't been
    re-installed yet.

    Examples:

      wip-deploy status
      wip-deploy status --name wip-dev-local
      wip-deploy status --namespace wip
      wip-deploy status --namespace wip-kb --diff
    """
    name = _resolve_name(
        name,
        target=("k8s" if namespace is not None else None),
        namespace=namespace,
    )

    if diff:
        resolved_dir = install_dir or _default_install_dir(name)
        _do_status_diff(resolved_dir, namespace_override=namespace)
        return

    from wip_deploy.status import (
        StatusError,
        format_table,
        read_compose_status,
        read_k8s_status,
        split_services_and_apps,
    )

    if namespace is not None:
        try:
            rows = read_k8s_status(namespace)
        except StatusError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo(f"Namespace: {namespace}")
        typer.echo("")
        typer.echo(format_table(rows))
        return

    resolved_dir = install_dir or _default_install_dir(name)
    try:
        rows = read_compose_status(resolved_dir)
    except StatusError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Install dir: {resolved_dir}")

    # CASE-331 Fix C — separate services from apps so the table makes
    # the "did the apps make it back up" question answerable from
    # `wip-deploy status` alone. App names come from the persisted
    # deployer-state; if the state file is missing or unreadable we
    # fall back to the pre-CASE-331 single-table render.
    previous = _try_load_previous_deployment(resolved_dir)
    if previous is None:
        typer.echo("")
        typer.echo(format_table(rows))
        return

    enabled_app_names = {a.name for a in previous.spec.apps if a.enabled}
    services_rows, apps_rows = split_services_and_apps(rows, enabled_app_names)

    typer.echo("")
    typer.echo("Services:")
    typer.echo(format_table(services_rows))

    if enabled_app_names:
        typer.echo("")
        typer.echo("Apps:")
        if apps_rows:
            typer.echo(format_table(apps_rows))
        else:
            # State declares apps but none are running — surface the
            # gap loudly. This is the post-reboot-without-apps shape
            # the case body called out.
            declared = ", ".join(sorted(enabled_app_names))
            typer.echo(
                typer.style(
                    f"  ⚠ declared but not running: {declared}",
                    fg=typer.colors.YELLOW,
                )
            )


# ────────────────────────────────────────────────────────────────────
# rebuild
# ────────────────────────────────────────────────────────────────────


@app.command()
def rebuild(
    services: Annotated[
        list[str],
        typer.Argument(
            help=(
                "One or more service names to rebuild (matches keys under "
                "`services:` in the rendered docker-compose.yaml). At least "
                "one is required."
            ),
        ),
    ],
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help=(
                "Compose install directory. Defaults to "
                "~/.wip-deploy/<name>/."
            ),
        ),
    ] = None,
    name: Annotated[str | None, _name_opt()] = None,
    no_wait: Annotated[
        bool,
        typer.Option(
            "--no-wait",
            help="Don't poll for healthy after rebuild.",
        ),
    ] = False,
    wait_timeout: Annotated[
        int,
        typer.Option(
            "--wait-timeout",
            help="Seconds to wait for healthy (default 120).",
        ),
    ] = 120,
) -> None:
    """Rebuild and recreate one or more services in an existing install.

    Compose/dev only. Reads the rendered docker-compose.yaml under the
    install directory (no spec, no render — the install must already
    exist) and runs `compose up -d --build --force-recreate <svc>...`
    for the requested services. Polls `compose ps` until each service
    with a healthcheck reports healthy unless `--no-wait` is set.

    For Dockerfile or requirements.txt edits — bind-mounted source is
    already live without a rebuild; just `podman restart wip-<svc>`.

    Examples:

      wip-deploy rebuild mcp-server
      wip-deploy rebuild registry def-store --name wip-dev-local
    """
    from wip_deploy.apply import ApplyError, rebuild_compose_services

    name = _resolve_name(name)
    target_dir = install_dir or _default_install_dir(name)

    if not services:
        typer.echo("error: at least one service name is required", err=True)
        raise typer.Exit(2)

    try:
        rebuild_compose_services(
            install_dir=target_dir,
            services=list(services),
            wait=not no_wait,
            timeout_seconds=wait_timeout,
        )
    except ApplyError as e:
        typer.echo(typer.style(f"✗ {e}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    rebuilt = ", ".join(services)
    typer.echo(
        typer.style(
            f"✓ Rebuilt {rebuilt}", fg=typer.colors.GREEN, bold=True
        )
    )


# ────────────────────────────────────────────────────────────────────
# restart
# ────────────────────────────────────────────────────────────────────


@app.command()
def restart(
    services: Annotated[
        list[str],
        typer.Argument(
            help=(
                "One or more service names to restart (matches keys "
                "under `services:` in the rendered docker-compose.yaml). "
                "At least one is required."
            ),
        ),
    ],
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help=(
                "Compose install directory. Defaults to "
                "~/.wip-deploy/<name>/."
            ),
        ),
    ] = None,
    name: Annotated[str | None, _name_opt()] = None,
) -> None:
    """Restart one or more services in an existing install.

    Compose/dev only. Reads the rendered docker-compose.yaml under
    the install directory and runs `compose restart <svc>...` — does
    not rebuild the image or recreate the container, just bounces
    the process. Picks up env-var changes that don't propagate
    through bind-mounted source.

    For Dockerfile or package.json/requirements.txt edits use
    `wip-deploy rebuild` instead — restart alone won't pick up
    a new image.

    Examples:

      wip-deploy restart react-console
      wip-deploy restart def-store template-store --name wip-dev-local
    """
    from wip_deploy.apply import ApplyError, restart_compose_services

    name = _resolve_name(name)
    target_dir = install_dir or _default_install_dir(name)

    if not services:
        typer.echo("error: at least one service name is required", err=True)
        raise typer.Exit(2)

    try:
        restart_compose_services(
            install_dir=target_dir,
            services=list(services),
        )
    except ApplyError as e:
        typer.echo(typer.style(f"✗ {e}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    restarted = ", ".join(services)
    typer.echo(
        typer.style(
            f"✓ Restarted {restarted}", fg=typer.colors.GREEN, bold=True
        )
    )


# ────────────────────────────────────────────────────────────────────
# up
# ────────────────────────────────────────────────────────────────────


@app.command()
def up(
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help=(
                "Compose install directory. Defaults to "
                "~/.wip-deploy/<name>/."
            ),
        ),
    ] = None,
    name: Annotated[str | None, _name_opt()] = None,
    no_wait: Annotated[
        bool,
        typer.Option("--no-wait", help="Skip waiting for healthy."),
    ] = False,
    wait_timeout: Annotated[
        int,
        typer.Option(
            "--wait-timeout", help="Seconds to wait for healthy (default 120)."
        ),
    ] = 120,
) -> None:
    """Bring an existing install back to running, no spec recomputation.

    Compose/dev only. Reads the rendered docker-compose.yaml under the
    install directory and runs `compose up -d` against it — picks up
    exited containers after a host reboot, laptop sleep, or
    podman-machine restart without going through the full
    `install` flow. No rebuild, no re-render, no risk of dropping
    apps that weren't re-specified on the command line (CASE-331
    Fix A; the install flow's destructive shape is the reason this
    subcommand exists).

    For Dockerfile / package.json edits use `wip-deploy rebuild`.
    For spec or component-manifest edits use `wip-deploy install`.

    Examples:

      wip-deploy up
      wip-deploy up --name wip-dev-local
      wip-deploy up --name wip-dev-local --no-wait
    """
    from wip_deploy.apply import ApplyError, up_compose_install

    name = _resolve_name(name)
    target_dir = install_dir or _default_install_dir(name)

    try:
        up_compose_install(
            install_dir=target_dir,
            wait=not no_wait,
            timeout_seconds=wait_timeout,
        )
    except ApplyError as e:
        typer.echo(typer.style(f"✗ {e}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    typer.echo(
        typer.style(
            f"✓ Brought up install at {target_dir}",
            fg=typer.colors.GREEN,
            bold=True,
        )
    )


# ────────────────────────────────────────────────────────────────────
# register-app / unregister-app (CASE-356)
#
# Per-operator local-app-source registry at `~/.wip-deploy/apps/`.
# Each entry is a one-key YAML file pointing at the app's local
# checkout. `wip-deploy install --target dev` consults the directory
# automatically; CLI `--app-source NAME=PATH` overrides on a per-
# invocation basis.
# ────────────────────────────────────────────────────────────────────


@app.command("register-app")
def register_app_cmd(
    app_name: Annotated[
        str,
        typer.Argument(
            help="App name (kebab-case, matches metadata.name in wip-app.yaml)."
        ),
    ],
    path: Annotated[
        Path,
        typer.Option(
            "--path",
            help=(
                "Absolute path to the app's source checkout. The directory "
                "must exist. Used as the build context for `--target dev` "
                "installs that enable this app."
            ),
        ),
    ],
) -> None:
    """Register a local source path for an app (CASE-356 Phase 1).

    Writes `~/.wip-deploy/apps/<name>.yaml`. Future `wip-deploy install
    --target dev --app <name>` invocations discover the path automatically
    — no need to re-type `--app-source NAME=PATH` on every install. CLI
    `--app-source` still wins per invocation, useful for testing a
    branch from a different checkout.

    Idempotent: re-running with the same name overwrites the entry
    (how you migrate a moved checkout). Operators can also edit the
    YAML file by hand.

    Examples:

      wip-deploy register-app react-console --path /Users/peter/Development/WIP-ReactConsole
      wip-deploy register-app kb --path /Users/peter/Development/WIP-KB
    """
    from wip_deploy.app_registry import AppRegistryError, register_app

    try:
        entry_file = register_app(app_name, path)
    except AppRegistryError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e

    typer.echo(
        typer.style(
            f"✓ Registered {app_name!r} → {path.expanduser().resolve()}\n"
            f"  {entry_file}",
            fg=typer.colors.GREEN,
        )
    )


@app.command("unregister-app")
def unregister_app_cmd(
    app_name: Annotated[
        str,
        typer.Argument(help="App name to unregister."),
    ],
) -> None:
    """Remove an app's local-source registration (CASE-356 Phase 1).

    Deletes `~/.wip-deploy/apps/<name>.yaml`. Silent + idempotent
    when the entry doesn't exist — safe to call repeatedly.

    Examples:

      wip-deploy unregister-app kb
    """
    from wip_deploy.app_registry import unregister_app

    existed = unregister_app(app_name)
    if existed:
        typer.echo(
            typer.style(f"✓ Unregistered {app_name!r}", fg=typer.colors.GREEN)
        )
    else:
        typer.echo(f"⊙ {app_name!r} was not registered — no change.")


# ────────────────────────────────────────────────────────────────────
# validate-manifest (CASE-353)
#
# Validates a single `wip-app.yaml` against the current WIP root's
# discovered components/apps — schema (Pydantic on `App`) + references
# (`from_component*`, `depends_on`, route collisions). The canonical
# validator the `/deploy ready` slash command wraps; APP-YACs can run
# it standalone too. Scope is schema + references only (no build
# context, no env value resolution, no live cluster).
# ────────────────────────────────────────────────────────────────────


@app.command("validate-manifest")
def validate_manifest_cmd(
    manifest_path: Annotated[
        Path,
        typer.Argument(
            help=(
                "Path to a `wip-app.yaml` file, or a directory containing "
                "one. The validator reads the file but never modifies it."
            ),
        ),
    ],
    repo_root: Annotated[
        Path | None,
        typer.Option(
            "--repo-root",
            help=(
                "WIP repo root to discover components/apps against. "
                "Defaults to the repo containing the running wip-deploy "
                "installation."
            ),
        ),
    ] = None,
) -> None:
    """Validate an external `wip-app.yaml` against the current WIP root.

    The canonical answer to "would this manifest be accepted by the
    deployer?" — without staging the manifest into the WIP repo first.
    Use it from an APP-YAC's repo to verify a draft manifest before
    asking an operator to integrate it.

    Two layers of validation:

      1. Schema — Pydantic validation on the `App` model. Catches
         missing fields, wrong types, bad enum values.
      2. References — names in `from_component*`, `depends_on`, and
         route paths are checked against the WIP root's discovered
         components and apps. Any reference that doesn't resolve is
         flagged with the list of valid alternatives.

    Out of scope (v1): build context, env value resolution
    (`from_secret` accepts any non-empty name), live cluster state.

    Exit codes:
      0  Manifest passes schema + reference validation.
      1  Validation failed (errors emitted to stderr).
      2  Could not load the manifest (file missing, YAML parse error,
         not a YAML mapping).

    Examples:

      wip-deploy validate-manifest /Users/peter/Development/WIP-KB/wip-app.yaml
      wip-deploy validate-manifest /Users/peter/Development/WIP-KB
      wip-deploy validate-manifest ./apps/react-console/wip-app.yaml --repo-root .
    """
    from wip_deploy.validate_manifest import (
        ManifestLoadError,
        resolve_manifest_path,
        validate_manifest,
    )

    try:
        resolved = resolve_manifest_path(manifest_path)
    except ManifestLoadError as e:
        typer.echo(f"error: {e.message}", err=True)
        raise typer.Exit(2) from None

    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except FileNotFoundError as e:
            typer.echo(
                f"error: cannot resolve WIP repo root — pass --repo-root: {e}",
                err=True,
            )
            raise typer.Exit(2) from None
    elif not repo_root.is_dir():
        typer.echo(
            f"error: --repo-root {repo_root} is not a directory",
            err=True,
        )
        raise typer.Exit(2)

    try:
        app_obj, errors = validate_manifest(resolved, repo_root)
    except ManifestLoadError as e:
        typer.echo(f"error: {e.message}", err=True)
        raise typer.Exit(2) from None

    if errors:
        typer.echo(
            typer.style(
                f"✗ {resolved}: {len(errors)} validation error"
                f"{'s' if len(errors) != 1 else ''}",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        for err in errors:
            typer.echo(err.format(), err=True)
        raise typer.Exit(1)

    assert app_obj is not None  # errors empty → app populated
    typer.echo(
        typer.style(
            f"✓ {resolved}: {app_obj.metadata.name!r} manifest is valid",
            fg=typer.colors.GREEN,
            bold=True,
        )
    )


# ────────────────────────────────────────────────────────────────────
# add-app / remove-app / add-module / remove-module (CASE-313)
#
# Additive mutation verbs. Load the persisted deployment-state, mutate
# the named field (apps or modules.optional), re-discover, validate,
# render, apply, persist. Lets the operator add WIP-KB without
# re-specifying every existing --app-source flag, and remove an app
# cleanly without dropping the rest of the install by accident.
# Symmetric to the CASE-331 Fix B drop-warning: that case made the
# silent destructive shape loud; these verbs make additive change
# the documented happy path.
# ────────────────────────────────────────────────────────────────────


def _load_and_discover_for_mutation(
    install_name: str,
    install_dir: Path | None,
) -> tuple[str, Path, Deployment, list, list]:
    """Shared prelude for the additive verbs.

    Resolves the install dir, loads the persisted Deployment (exits
    cleanly if missing — additive verbs only make sense against an
    existing install), discovers components + apps in the WIP repo,
    and returns the tuple. Errors hit typer.Exit with actionable
    messages.
    """
    resolved_name = _resolve_name(install_name)
    target_dir = install_dir or _default_install_dir(resolved_name)
    deployment = _load_deployment(target_dir)

    # Discover against the WIP repo root — components/apps may have
    # been added since the last install (which is exactly what the
    # operator is trying to use here).
    try:
        root = find_repo_root()
    except FileNotFoundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    discovery = discover(root)
    if not discovery.ok:
        typer.echo("manifest discovery errors:", err=True)
        for err in discovery.errors:
            typer.echo(f"  - {err}", err=True)
        raise typer.Exit(1)

    return resolved_name, target_dir, deployment, discovery.components, discovery.apps


def _apply_and_persist_mutation(
    deployment: Deployment,
    components: list,
    apps_list: list,
    target_dir: Path,
    action_label: str,
) -> None:
    """Shared post-mutation lifecycle: validate → render → apply → persist.

    Mirrors the back half of `install` but starts from a fully-mutated
    Deployment loaded off disk rather than reconstructed from CLI
    flags. The render+apply path is the same; persistence overwrites
    the deployment-state with the mutated spec so subsequent verbs
    see the latest.
    """
    _validate_or_exit(deployment, components, apps_list)

    secrets = _ensure_secrets_via_spec(deployment, components, apps_list)
    # CASE-355: surface render-time errors cleanly through the additive
    # verbs too — `add-app NAME` without --app-source on a dev install
    # hits the same gate.
    try:
        tree = _render_tree(deployment, components, apps_list, secrets)
    except ValueError as e:
        typer.echo(
            typer.style(f"✗ {action_label} aborted: {e}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(2) from e

    apply_fn = apply_k8s if deployment.spec.target == "k8s" else apply_compose
    try:
        result = apply_fn(
            deployment=deployment,
            components=components,
            apps=apps_list,
            tree=tree,
            install_dir=target_dir,
        )
    except ApplyError as e:
        typer.echo(
            typer.style(f"✗ {action_label} failed: {e}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(1) from e

    _persist_deployment(deployment, target_dir)

    if result.healthy:
        typer.echo(typer.style(f"✓ {action_label}.", fg=typer.colors.GREEN, bold=True))
    else:
        typer.echo(
            typer.style(
                f"⚠ {action_label} finished, but not all services reached healthy",
                fg=typer.colors.YELLOW,
                bold=True,
            )
        )


@app.command("add-app")
def add_app(
    app_name: Annotated[
        str,
        typer.Argument(
            help="Name of the app to enable (matches `apps/<name>/wip-app.yaml`)."
        ),
    ],
    source: Annotated[
        Path | None,
        typer.Option(
            "--app-source",
            "--source",
            help=(
                "Local checkout path for hot-reload (dev target only). "
                "Bind-mounts the source into the app container and runs "
                "the app's Dockerfile.dev. Equivalent to `--app-source "
                "NAME=PATH` on install."
            ),
        ),
    ] = None,
    name: Annotated[str | None, _name_opt()] = None,
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help="Install directory. Defaults to ~/.wip-deploy/<name>/.",
        ),
    ] = None,
) -> None:
    """Add an app to an existing install without dropping anything else.

    Loads the persisted spec, appends the named app, re-discovers,
    re-renders, re-applies, and overwrites the deployment-state.
    The rest of the install (other apps, optional modules, secrets,
    network) is preserved untouched (CASE-313).

    Examples:

      wip-deploy add-app react-console --name wip-dev-local
      wip-deploy add-app kb --app-source /Users/peter/Development/WIP-KB
    """
    resolved_name, target_dir, deployment, components, apps_list = (
        _load_and_discover_for_mutation(name, install_dir)
    )

    # Verify the named app actually has a manifest in the WIP repo.
    available = {a.metadata.name for a in apps_list}
    if app_name not in available:
        typer.echo(
            f"error: app {app_name!r} not found in `apps/*/wip-app.yaml`. "
            f"Available: {', '.join(sorted(available)) or '(none)'}",
            err=True,
        )
        raise typer.Exit(2)

    # source override only makes sense for dev target.
    if source is not None and deployment.spec.target != "dev":
        typer.echo(
            f"error: --app-source is only valid for target=dev "
            f"(this install's target is {deployment.spec.target!r})",
            err=True,
        )
        raise typer.Exit(2)
    if source is not None and not source.is_dir():
        typer.echo(f"error: --app-source path is not a directory: {source}", err=True)
        raise typer.Exit(2)

    # Already-enabled is a friendly no-op; flipping disabled → enabled
    # is a real mutation.
    existing = next((a for a in deployment.spec.apps if a.name == app_name), None)
    if existing is not None and existing.enabled and source is None:
        typer.echo(
            f"⊙ app {app_name!r} is already enabled in this install — no change."
        )
        return
    if existing is None:
        deployment.spec.apps.append(AppRef(name=app_name, enabled=True))
    else:
        existing.enabled = True

    if source is not None:
        # Mutate the DevPlatform.app_sources mapping.
        if deployment.spec.platform.dev is None:
            typer.echo(
                "error: install has no dev platform block; cannot set --app-source",
                err=True,
            )
            raise typer.Exit(2)
        deployment.spec.platform.dev.app_sources[app_name] = source

    _apply_and_persist_mutation(
        deployment,
        components,
        apps_list,
        target_dir,
        f"Added app {app_name!r} to {resolved_name}",
    )


@app.command("remove-app")
def remove_app(
    app_name: Annotated[
        str,
        typer.Argument(help="Name of the app to remove from the install."),
    ],
    name: Annotated[str | None, _name_opt()] = None,
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help="Install directory. Defaults to ~/.wip-deploy/<name>/.",
        ),
    ] = None,
) -> None:
    """Remove an app from an existing install without dropping the rest.

    Mutation is the inverse of `add-app`: drops the AppRef from
    `spec.apps`, drops any `app_sources` entry on the dev platform,
    re-renders, re-applies, persists. The removed app's container is
    force-removed via `podman rm -f wip-<name>` so it doesn't linger
    as an orphan after the rendered compose stops mentioning it
    (CASE-313).

    Examples:

      wip-deploy remove-app clintrial --name wip-dev-local
    """
    resolved_name, target_dir, deployment, components, apps_list = (
        _load_and_discover_for_mutation(name, install_dir)
    )

    before = len(deployment.spec.apps)
    deployment.spec.apps = [
        a for a in deployment.spec.apps if a.name != app_name
    ]
    removed = before > len(deployment.spec.apps)

    if not removed:
        typer.echo(
            f"⊙ app {app_name!r} is not in this install — no change."
        )
        return

    if deployment.spec.platform.dev is not None:
        deployment.spec.platform.dev.app_sources.pop(app_name, None)

    # Stop the orphan container BEFORE re-applying so the operator
    # doesn't see a "running" app that's no longer in the spec.
    from wip_deploy.apply import stop_and_remove_container
    stop_and_remove_container(app_name)

    _apply_and_persist_mutation(
        deployment,
        components,
        apps_list,
        target_dir,
        f"Removed app {app_name!r} from {resolved_name}",
    )


@app.command("add-module")
def add_module(
    module_name: Annotated[
        str,
        typer.Argument(
            help=(
                "Name of the optional module to enable (matches a "
                "component with `category: optional` in its manifest)."
            ),
        ),
    ],
    name: Annotated[str | None, _name_opt()] = None,
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help="Install directory. Defaults to ~/.wip-deploy/<name>/.",
        ),
    ] = None,
) -> None:
    """Enable an optional module in an existing install without dropping anything.

    Equivalent to passing `--add <module>` on a fresh install, but
    applied to the persisted spec. Other modules, apps, and platform
    state are preserved (CASE-313).

    Examples:

      wip-deploy add-module reporting-sync --name wip-dev-local
    """
    resolved_name, target_dir, deployment, components, apps_list = (
        _load_and_discover_for_mutation(name, install_dir)
    )

    # Module must exist as a discovered component with category=optional.
    optional_components = {
        c.metadata.name for c in components
        if c.metadata.category == "optional"
    }
    if module_name not in optional_components:
        typer.echo(
            f"error: {module_name!r} is not a discovered optional component. "
            f"Available: {', '.join(sorted(optional_components)) or '(none)'}",
            err=True,
        )
        raise typer.Exit(2)

    if module_name in deployment.spec.modules.optional:
        typer.echo(
            f"⊙ module {module_name!r} is already enabled in this install — no change."
        )
        return

    deployment.spec.modules.optional.append(module_name)

    _apply_and_persist_mutation(
        deployment,
        components,
        apps_list,
        target_dir,
        f"Added module {module_name!r} to {resolved_name}",
    )


@app.command("remove-module")
def remove_module(
    module_name: Annotated[
        str,
        typer.Argument(help="Name of the optional module to disable."),
    ],
    name: Annotated[str | None, _name_opt()] = None,
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help="Install directory. Defaults to ~/.wip-deploy/<name>/.",
        ),
    ] = None,
) -> None:
    """Disable an optional module in an existing install without dropping anything.

    Inverse of `add-module`. Force-removes the module's container via
    `podman rm -f wip-<name>` so it doesn't linger as an orphan
    (CASE-313).

    Examples:

      wip-deploy remove-module minio --name wip-dev-local
    """
    resolved_name, target_dir, deployment, components, apps_list = (
        _load_and_discover_for_mutation(name, install_dir)
    )

    if module_name not in deployment.spec.modules.optional:
        typer.echo(
            f"⊙ module {module_name!r} is not enabled in this install — no change."
        )
        return

    deployment.spec.modules.optional = [
        m for m in deployment.spec.modules.optional if m != module_name
    ]

    from wip_deploy.apply import stop_and_remove_container
    stop_and_remove_container(module_name)

    _apply_and_persist_mutation(
        deployment,
        components,
        apps_list,
        target_dir,
        f"Removed module {module_name!r} from {resolved_name}",
    )


# ────────────────────────────────────────────────────────────────────
# nuke
# ────────────────────────────────────────────────────────────────────


@app.command()
def nuke(
    install_dir: Annotated[
        Path | None,
        typer.Option(
            "--install-dir",
            help=(
                "Install directory to tear down. Defaults to "
                "~/.wip-deploy/<name>/ when --name is used."
            ),
        ),
    ] = None,
    name: Annotated[str | None, _name_opt()] = None,
    remove_data: Annotated[
        bool,
        typer.Option(
            "--remove-data",
            help=(
                "Remove named volumes (databases, file storage). Destructive."
            ),
        ),
    ] = False,
    remove_secrets: Annotated[
        bool,
        typer.Option(
            "--remove-secrets",
            help=(
                "Remove the secret backend directory. Without this, re-installing "
                "reuses the existing secrets."
            ),
        ),
    ] = False,
    remove_images: Annotated[
        bool,
        typer.Option(
            "--remove-images",
            help=(
                "Remove every image referenced by the compose's "
                "services.*.image entries (or, with --purge-all, every "
                "wip-* image on the host). Frees disk; forces the next "
                "install to re-pull or re-build."
            ),
        ),
    ] = False,
    secrets_location: Annotated[
        Path | None,
        typer.Option(
            "--secrets-location",
            help=(
                "Override secrets directory. Default: "
                "~/.wip-deploy/<name>/secrets/."
            ),
        ),
    ] = None,
    purge_all: Annotated[
        bool,
        typer.Option(
            "--purge-all",
            help=(
                "Nuclear: remove every wip-* container, pod, network, and "
                "(with --remove-data) volume on this host, regardless of "
                "compose project. Use for cross-install cleanup (v1 "
                "leftovers)."
            ),
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be removed; don't actually remove anything.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("-y", "--yes", help="Skip confirmation prompt."),
    ] = False,
) -> None:
    """Tear down a WIP install.

    By default, runs `compose down` in the install dir (scoped teardown).
    With `--purge-all`, removes every wip-* container/pod on the host.

    Examples:

      # Tear down (preserve data and secrets — re-install reuses both)
      wip-deploy nuke

      # Full teardown including data volumes (databases gone)
      wip-deploy nuke --remove-data --yes

      # Cross-install cleanup of every wip-* on host (DESTRUCTIVE)
      wip-deploy nuke --purge-all --remove-data --remove-secrets --yes

      # See what would be removed without removing it
      wip-deploy nuke --purge-all --dry-run
    """
    if purge_all:
        _nuke_purge_all(
            remove_data=remove_data,
            remove_images=remove_images,
            dry_run=dry_run,
            yes=yes,
        )
        return

    name = _resolve_name(name)
    target_dir = install_dir or _default_install_dir(name)
    target_secrets = secrets_location or (
        _default_install_dir(name) / "secrets" if remove_secrets else None
    )

    typer.echo(f"Install dir:   {target_dir}")
    if remove_data:
        typer.echo(typer.style("  + data volumes will be removed", fg=typer.colors.YELLOW))
    if remove_images:
        typer.echo(typer.style("  + images will be removed (next install re-pulls/builds)", fg=typer.colors.YELLOW))
    if remove_secrets:
        typer.echo(
            typer.style(
                f"  + secrets dir will be removed: {target_secrets}",
                fg=typer.colors.YELLOW,
            )
        )
    if not yes and not _confirm("Proceed with teardown?"):
        raise typer.Exit(0)

    try:
        report = nuke_install_dir(
            target_dir,
            remove_data=remove_data,
            secrets_location=target_secrets,
            remove_secrets=remove_secrets,
            remove_images=remove_images,
        )
    except NukeError as e:
        typer.echo(typer.style(f"✗ {e}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    typer.echo(typer.style(f"✓ {report.summary()}", fg=typer.colors.GREEN))


def _nuke_purge_all(
    *, remove_data: bool, remove_images: bool, dry_run: bool, yes: bool
) -> None:
    typer.echo(
        typer.style("Purge-all scans for every wip-* resource on this host.", bold=True)
    )
    if remove_data:
        typer.echo(
            typer.style(
                "  + named volumes will be removed — databases will be destroyed",
                fg=typer.colors.YELLOW,
            )
        )
    if remove_images:
        typer.echo(
            typer.style(
                "  + every wip-* image on this host will be removed",
                fg=typer.colors.YELLOW,
            )
        )
    if dry_run:
        typer.echo("  + dry-run: nothing will be removed")
    if not yes and not dry_run and not _confirm("Continue?"):
        raise typer.Exit(0)

    try:
        report = nuke_purge_all(
            remove_data=remove_data,
            remove_images=remove_images,
            dry_run=dry_run,
        )
    except NukeError as e:
        typer.echo(typer.style(f"✗ {e}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    if report.containers_removed:
        typer.echo(f"  containers: {', '.join(report.containers_removed)}")
    if report.pods_removed:
        typer.echo(f"  pods:       {', '.join(report.pods_removed)}")
    if report.volumes_removed:
        typer.echo(f"  volumes:    {', '.join(report.volumes_removed)}")
    if report.networks_removed:
        typer.echo(f"  networks:   {', '.join(report.networks_removed)}")
    if report.images_removed:
        typer.echo(f"  images:     {', '.join(report.images_removed)}")
    typer.echo(
        typer.style(
            f"✓ {'dry-run: ' if dry_run else ''}{report.summary()}",
            fg=typer.colors.GREEN,
        )
    )


# ────────────────────────────────────────────────────────────────────
# check-app-deployability (CASE-379-C)
# ────────────────────────────────────────────────────────────────────


@app.command("check-app-deployability")
def check_app_deployability_cmd(
    source_dir: Annotated[
        Path,
        typer.Argument(
            help="Path to the app's source directory (where Dockerfile.dev "
            "and package.json live).",
        ),
    ],
    manifest: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            help=(
                "Path to the app's wip-app.yaml. Auto-detected by reading "
                "the source dir's package.json `name` field and matching "
                "against apps/*/wip-app.yaml. Pass explicitly if "
                "auto-detection fails."
            ),
        ),
    ] = None,
    repo_root: Annotated[
        Path | None,
        typer.Option(
            "--repo-root",
            help=(
                "Path to the World-in-a-Pie repo root. Defaults to the "
                "auto-discovered root."
            ),
        ),
    ] = None,
) -> None:
    """Check if an app source repo + its manifest satisfy the
    wip-deployable-app contract (CASE-379).

    The contract that today's CASE-375 debugging journey hit four
    sequential gaps in. Run this BEFORE `wip-deploy add-app` to surface
    the gaps mechanically — a few seconds vs the same gaps surfacing
    in a crash-loop debugging session.

    Exit codes:
      0 — all checks passed; the app is wip-deployable
      1 — at least one check failed; fix-hints printed per failure
      2 — couldn't even start (source dir doesn't exist, etc.)

    Examples:

      # Most common — let auto-discovery find the manifest
      wip-deploy check-app-deployability ~/Development/WIP-KB

      # Explicit manifest (e.g., apps not yet registered in WIP repo)
      wip-deploy check-app-deployability /path/to/src \\
        --manifest /path/to/wip-app.yaml
    """
    from wip_deploy.check_app import check_app_deployability

    if not source_dir.expanduser().exists():
        typer.echo(
            typer.style(
                f"✗ source directory does not exist: {source_dir}",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        raise typer.Exit(2)

    try:
        report = check_app_deployability(
            source_dir=source_dir,
            manifest_path=manifest,
            repo_root=repo_root,
        )
    except FileNotFoundError as e:
        typer.echo(
            typer.style(f"✗ {e}", fg=typer.colors.RED), err=True,
        )
        raise typer.Exit(2) from e

    typer.echo(f"Checking app source: {report.source_dir}")
    if report.app_name:
        typer.echo(f"  app name:  {report.app_name}")
    if report.manifest_path:
        typer.echo(f"  manifest:  {report.manifest_path}")
    typer.echo("")

    for r in report.results:
        if r.passed:
            typer.echo(
                typer.style(f"  ✓ {r.name}", fg=typer.colors.GREEN)
                + f"  {r.message}"
            )
        else:
            typer.echo(
                typer.style(f"  ✗ {r.name}", fg=typer.colors.RED)
                + f"  {r.message}"
            )
            if r.fix_hint:
                # Indent the hint so it visually belongs to the failed check.
                for line in r.fix_hint.split("\n"):
                    typer.echo(f"      {line}")

    typer.echo("")
    if report.ok:
        typer.echo(
            typer.style(
                f"✓ {len(report.results)} check(s) passed. App is wip-deployable.",
                fg=typer.colors.GREEN,
                bold=True,
            )
        )
        raise typer.Exit(0)

    n_failed = len(report.failures)
    typer.echo(
        typer.style(
            f"✗ {n_failed} of {len(report.results)} check(s) failed.",
            fg=typer.colors.RED,
            bold=True,
        )
    )
    raise typer.Exit(1)


# ────────────────────────────────────────────────────────────────────
# export-ca (CASE-360)
# ────────────────────────────────────────────────────────────────────


@app.command("export-ca")
def export_ca(
    name: Annotated[str | None, _name_opt()] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help=(
                "Write the CA to this file. When omitted, the PEM is "
                "printed to stdout — handy for piping to `tee` or `openssl`."
            ),
        ),
    ] = None,
) -> None:
    """Export the Caddy-managed internal CA so off-host clients can trust it.

    `wip-deploy install --tls internal` (the compose/dev default) makes
    Caddy generate a self-signed root CA the first time it serves HTTPS.
    Same-host browsers accept it on first prompt; off-host clients
    (curl, Node.js, embedded devices) reject it without an explicit
    trust step. This verb extracts the CA so the operator can install
    it into the target system's trust store.

    The install must be running — the cert is read live from the
    wip-caddy container, not from a host-side path.

    Limitations:
      - Only supported for installs with `--tls internal` (the default
        for compose/dev). letsencrypt + external + self-signed paths
        each have a different right answer — see `wip-deploy help`.
      - k8s installs are not yet supported (they use an operator-
        provided cert in a TLS Secret; the deployer doesn't generate
        a CA).

    Examples:

      # Write to a file and follow the printed trust instructions
      wip-deploy export-ca --out wip-pi.local.crt

      # Print to stdout (pipe to a tool)
      wip-deploy export-ca | openssl x509 -text -noout
    """
    name = _resolve_name(name)
    install_dir = _default_install_dir(name)

    if not install_dir.exists():
        typer.echo(
            typer.style(
                f"✗ install dir does not exist: {install_dir}",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        typer.echo(
            "  Run `wip-deploy install --name ...` first, or pass --name "
            "to point at an existing install.",
            err=True,
        )
        raise typer.Exit(2)

    try:
        deployment = _load_deployment(install_dir)
    except typer.Exit:
        raise  # _load_deployment already printed an actionable message
    except Exception as e:
        typer.echo(
            typer.style(f"✗ could not load deployment spec: {e}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(2) from e

    # Validate target/tls combination — the friendly-error matrix from
    # the CASE-360 body. Each non-supported mode gets its own message
    # pointing at the right place to look.
    spec = deployment.spec
    if spec.target == "k8s":
        typer.echo(
            typer.style(
                "✗ export-ca is not supported for k8s installs.",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        typer.echo(
            f"  K8s installs use an operator-provided TLS cert in the "
            f"{spec.platform.k8s.tls_secret_name!r} Secret. "
            "Export it with: kubectl get secret "
            f"{spec.platform.k8s.tls_secret_name} -n {spec.platform.k8s.namespace} "
            "-o jsonpath='{.data.tls\\.crt}' | base64 -d",
            err=True,
        )
        raise typer.Exit(2)

    if spec.network.tls != "internal":
        typer.echo(
            typer.style(
                f"✗ export-ca only applies to --tls internal installs. "
                f"This install uses --tls {spec.network.tls!r}.",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        if spec.network.tls == "letsencrypt":
            typer.echo(
                "  letsencrypt issues a real certificate trusted by every "
                "browser and OS — no CA export needed. The browser already "
                "trusts the Let's Encrypt root.",
                err=True,
            )
        elif spec.network.tls == "external":
            typer.echo(
                "  --tls external means TLS terminates upstream of Caddy. "
                "The cert is your upstream terminator's responsibility — "
                "ask it for its CA chain.",
                err=True,
            )
        raise typer.Exit(2)

    try:
        pem = export_caddy_internal_ca()
    except ExportCAError as e:
        typer.echo(typer.style(f"✗ {e}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    if out is None:
        # stdout target — write bytes directly so pipe-to-openssl works.
        # typer.echo would add a trailing newline + apply text coercion.
        import sys

        sys.stdout.buffer.write(pem)
        return

    out.write_bytes(pem)
    typer.echo(
        typer.style(
            f"✓ Wrote internal CA to {out}", fg=typer.colors.GREEN,
        ),
    )
    typer.echo("")
    typer.echo(TRUST_INSTRUCTIONS.format(out=out))


def _confirm(prompt: str) -> bool:
    response = typer.prompt(f"{prompt} [y/N]", default="n", show_default=False)
    return response.strip().lower() in ("y", "yes")


# ────────────────────────────────────────────────────────────────────
# examples
# ────────────────────────────────────────────────────────────────────


_EXAMPLES_TEXT = """\
wip-deploy — common workflows

GETTING STARTED
  Localhost dev (recommended first install — no /etc/hosts magic):
    wip-deploy install --target dev --preset standard

  First Pi install with self-signed TLS:
    wip-deploy install --hostname wip-pi.local --preset standard

  Pi or cloud install with Let's Encrypt:
    wip-deploy install --hostname wip.example.com --tls letsencrypt

DEV LOOP — hot-reload an app from a local checkout
  React Console with bind-mounted source:
    wip-deploy install --target dev --app-source react-console=$HOME/Dev/WIP-ReactConsole

  Multiple apps from local checkouts (repeat --app-source):
    wip-deploy install --target dev \\
      --app-source react-console=$HOME/Dev/WIP-ReactConsole \\
      --app-source clintrial=$HOME/Dev/WIP-ClinTrial

  Override the image tag for one app (skip the manifest pin):
    wip-deploy install --tag v1.2.0 --app react-console

CHANGING AN EXISTING INSTALL
  Pick up an env-var change, no rebuild:
    wip-deploy restart def-store

  Pull a new image and recreate the container:
    wip-deploy rebuild registry

  See current state of running services:
    wip-deploy status

INSPECTION (no-op verbs — useful for debugging)
  See what a preset resolves to without applying:
    wip-deploy show-spec --preset full

  Render the compose / Caddyfile / Dex config to a directory:
    wip-deploy render --preset standard --output-dir /tmp/wip-render

  Validate without rendering or applying:
    wip-deploy validate --preset analytics --target k8s

KUBERNETES
  Render manifests for inspection before applying:
    wip-deploy render --target k8s --namespace wip --preset standard

  Install against a real cluster (needs kubectl context set):
    wip-deploy install --target k8s --namespace wip --tls external

TEARDOWN
  Tear down (preserve data and secrets — re-install reuses both):
    wip-deploy nuke

  Full teardown including data volumes:
    wip-deploy nuke --remove-data --yes

  Cross-install cleanup of every wip-* on host (DESTRUCTIVE):
    wip-deploy nuke --purge-all --remove-data --remove-secrets --yes

PRESETS — pick one, then tweak with --add NAME / --remove NAME / --app NAME
  core       minimal API-only backend
  headless   same as core, no UI surface
  standard   core + OIDC + auth-gateway (most common)
  analytics  standard + reporting (Postgres + reporting-sync)
  full       analytics + files (MinIO) + ingest (NATS streaming)

TARGETS
  compose   production-style podman-compose / docker-compose deployment
  dev       hot-reload for local development (compose + bind-mounts + --reload)
  k8s       Kubernetes manifests (Ingress + Deployment + Service)

For verb-specific options:
  wip-deploy COMMAND --help
"""


@app.command()
def examples() -> None:
    """Print common wip-deploy workflows with their exact commands.

    A curated map of "what do I run for X?" — grouped by intent
    (getting started, dev loop, ops, inspection, k8s, teardown). For
    verb-specific options, run 'wip-deploy COMMAND --help'.
    """
    typer.echo(_EXAMPLES_TEXT)


# ────────────────────────────────────────────────────────────────────
# Internal glue
# ────────────────────────────────────────────────────────────────────


def _assemble(
    *,
    preset: str,
    target: str,
    hostname: str,
    tls: str,
    https_port: int | None,
    http_port: int | None,
    data_dir: Path | None,
    namespace: str,
    storage_class: str,
    ingress_class: str,
    tls_secret_name: str,
    dev_mode: str,
    registry: str | None,
    tag: str,
    add: list[str],
    remove: list[str],
    apps: list[str],
    app_sources: dict[str, Path],
    apps_from_registry: list[str],
    auth_mode: str | None,
    auth_gateway: bool | None,
    secrets_backend: str | None,
    secrets_location: str | None,
    repo_root: Path | None,
    name: str,
    remote_wip_url: str | None = None,
    apps_only: bool = False,
    skip_discovery: bool = False,
) -> tuple[Deployment, list, list]:  # type: ignore[type-arg]
    """Assemble the Deployment and, unless skipped, discover manifests.

    Centralizes the error-to-exit-code mapping so both `validate` and
    `show-spec` share it.
    """
    # Repo root + data_dir defaulting
    try:
        root = repo_root or find_repo_root()
    except FileNotFoundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    if target == "compose" and data_dir is None:
        data_dir = root / "data"

    # CASE-313 (related): --app-source NAME=PATH implicitly enables NAME.
    # Without this, a user who passes ONLY --app-source has to also
    # remember --app NAME, which the help-text example omits and which
    # produced silent app-drop in past sessions (an --app-source without
    # --app rendered with apps=[] and dropped the app from the deployment
    # entirely). Bind-mounting source for an app you didn't enable has
    # no coherent meaning anyway, so the implication is one-way and safe.
    apps = list(apps)  # copy to avoid mutating caller's list
    for src_name in app_sources:
        if src_name not in apps:
            apps.append(src_name)
    # CASE-355: same implicit-enable for --app-from-registry NAME. Opting
    # an app into the registry-image fallback only makes sense for an
    # app that's actually enabled, so we extend `apps` symmetrically.
    for reg_name in apps_from_registry:
        if reg_name not in apps:
            apps.append(reg_name)

    # CASE-356: merge per-operator registry into app_sources. CLI
    # `--app-source NAME=PATH` wins per invocation; registered paths
    # back-fill for apps the CLI didn't override. Warn when both are
    # set (different) so the operator knows the CLI is shadowing the
    # registered path.
    from wip_deploy.app_registry import read_registry
    # Local name avoids shadowing the `registry` parameter (image registry URL).
    app_path_registry = read_registry()
    final_app_sources: dict[str, Path] = dict(app_sources)
    for reg_name, reg_path in app_path_registry.items():
        if reg_name not in apps:
            # Registry entry for an app that's not enabled in this
            # install — ignore. Registry holds machine state for ALL
            # cloned apps; an install enables a subset.
            continue
        if reg_name in final_app_sources:
            if final_app_sources[reg_name] != reg_path:
                # CASE-366: warnings on stderr, never stdout. Operators
                # piping `--format json` into jq (and tests calling
                # `json.loads(r.output)`) get a clean JSON payload;
                # the warning is still visible on the terminal but on
                # the right channel.
                typer.echo(
                    typer.style(
                        f"⚠ --app-source override for {reg_name!r} shadows "
                        f"registered path {reg_path}",
                        fg=typer.colors.YELLOW,
                    ),
                    err=True,
                )
            continue
        final_app_sources[reg_name] = reg_path
    app_sources = final_app_sources

    # Build
    inputs = BuildInputs(
        name=name,
        preset=preset,
        target=target,
        hostname=hostname,
        tls=tls,
        https_port=https_port,
        http_port=http_port,
        compose_data_dir=data_dir,
        k8s_namespace=namespace,
        k8s_storage_class=storage_class,
        k8s_ingress_class=ingress_class,
        k8s_tls_secret_name=tls_secret_name,
        dev_mode=dev_mode,
        registry=registry,
        tag=tag,
        add=add,
        remove=remove,
        apps=apps,
        app_sources=app_sources,
        apps_from_registry=list(apps_from_registry),
        auth_mode=auth_mode,
        auth_gateway=auth_gateway,
        secrets_backend=secrets_backend,
        secrets_location=secrets_location,
        remote_wip_url=remote_wip_url,
        apps_only=apps_only,
    )

    # CASE-359 validation: apps-only requires at least one --app. An
    # apps-only install with no apps is structurally useless — refuse
    # before render to give a clear actionable message.
    if apps_only and not apps:
        typer.echo(
            typer.style(
                "✗ --apps-only requires at least one --app NAME. "
                "Without apps, the install would deploy only Caddy "
                "with no routes — nothing to serve.",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        raise typer.Exit(2)

    # CASE-358 + CASE-359: when both are off, no warning. When only
    # --remote-wip is set, point at --apps-only as the cleaner path.
    # When only --apps-only is set, point at --remote-wip (apps will
    # default external_base_url to localhost, which probably isn't
    # what the operator wants).
    if apps_only and remote_wip_url is None:
        typer.echo(
            typer.style(
                "⚠ --apps-only without --remote-wip: apps that resolve "
                "WIP_BASE_URL via `from_spec: network.external_base_url` "
                "will point at this install's own hostname — which has "
                "no WIP backend in apps-only mode. Pass --remote-wip "
                "<URL> to point them at a real WIP install.",
                fg=typer.colors.YELLOW,
            ),
            err=True,
        )
    elif remote_wip_url is not None and not apps_only:
        typer.echo(
            typer.style(
                f"⚠ --remote-wip {remote_wip_url} plumbs the URL but the local "
                "install still deploys its full backend stack. For a true "
                "apps-only install pointed at a remote WIP, also pass "
                "--apps-only (CASE-359).",
                fg=typer.colors.YELLOW,
            ),
            err=True,
        )

    try:
        deployment = build_deployment(inputs)
    except KeyError as e:
        typer.echo(f"error: {e.args[0]}", err=True)
        raise typer.Exit(2) from e
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    except Exception as e:
        # Pydantic ValidationError and anything else
        typer.echo(f"spec construction failed:\n{e}", err=True)
        raise typer.Exit(2) from e

    if skip_discovery:
        return deployment, [], []

    # Discover
    discovery = discover(root)
    if not discovery.ok:
        typer.echo("manifest discovery errors:", err=True)
        for err in discovery.errors:
            typer.echo(f"  - {err}", err=True)
        raise typer.Exit(1)

    return deployment, discovery.components, discovery.apps


def _validate_or_exit(
    deployment: Deployment, components: list[Component], apps_list: list[App]
) -> None:
    report = validate_all(deployment, components, apps_list)
    if not report.ok:
        typer.echo(
            typer.style(
                f"✗ Validation failed ({len(report.errors)} error(s)):",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        for err in report.errors:
            typer.echo(f"  - {err}", err=True)
        raise typer.Exit(1)


def _run_preflight_or_exit(deployment: Deployment, install_dir: Path) -> None:
    """Port + stale-container checks before compose/dev apply.

    Fatal errors abort with exit 1. Warnings are printed but don't block
    — the user can decide whether to proceed.
    """
    from wip_deploy.preflight import (
        PreflightError,
        check_no_stale_containers,
        check_ports_free,
    )

    ports = [deployment.spec.network.https_port, deployment.spec.network.http_port]
    try:
        check_ports_free(ports, install_dir=install_dir)
    except PreflightError as e:
        typer.echo(
            typer.style(f"✗ {e}", fg=typer.colors.RED, bold=True), err=True
        )
        raise typer.Exit(1) from e

    warnings = check_no_stale_containers(install_dir)
    for w in warnings:
        typer.echo(
            typer.style(f"⚠ {w.message}", fg=typer.colors.YELLOW), err=True
        )


def _default_install_dir(name: str) -> Path:
    """Default materialization directory — `~/.wip-deploy/<name>/`."""
    return Path.home() / ".wip-deploy" / name


# ────────────────────────────────────────────────────────────────────
# Spec persistence (CASE-294 — for `status --diff` re-rendering)
# ────────────────────────────────────────────────────────────────────


_DEPLOYMENT_JSON_VERSION = 1


# File name uses a non-yaml/json extension so kubectl's recursive
# apply filter (`-R -f <install_dir>`) skips it. yaml/yml/json files
# in the install dir are k8s manifests; this one is deployer-internal
# state that would fail kubectl validation if picked up.
_DEPLOYMENT_STATE_FILENAME = "deployment.deployer-state"


def _persist_deployment(deployment: Deployment, install_dir: Path) -> None:
    """Persist the Deployment to the install dir so `status --diff`
    can re-render against the same spec without needing the install
    args. Versioned envelope so future schema evolution can refuse
    older files cleanly.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
        "deployment": deployment.model_dump(mode="json"),
    }
    (install_dir / _DEPLOYMENT_STATE_FILENAME).write_text(
        json.dumps(payload, indent=2, default=str) + "\n"
    )

    # Migration: earlier CASE-294 wrote `deployment.json`, which
    # kubectl picks up as a (broken) manifest on the next install.
    # If a stale copy exists, remove it. Same content lives in the
    # new file.
    legacy = install_dir / "deployment.json"
    if legacy.exists():
        legacy.unlink()


def _load_deployment(install_dir: Path) -> Deployment:
    """Load a previously-persisted Deployment. Refuses missing or
    older-version files with an actionable error."""
    target = install_dir / _DEPLOYMENT_STATE_FILENAME
    # Fall back to the legacy filename so installs done with the
    # earlier CASE-294 build can still --diff. _persist_deployment
    # migrates the file on next install.
    legacy = install_dir / "deployment.json"
    if not target.exists() and legacy.exists():
        target = legacy
    if not target.exists():
        typer.echo(
            f"error: {install_dir / _DEPLOYMENT_STATE_FILENAME} not found. "
            "Run `wip-deploy install` first (installs since CASE-294 "
            "persist the spec).",
            err=True,
        )
        raise typer.Exit(2)
    try:
        payload = json.loads(target.read_text())
    except json.JSONDecodeError as e:
        typer.echo(f"error: {target} is not valid JSON: {e}", err=True)
        raise typer.Exit(2) from e
    version = payload.get("wip_deploy_format_version")
    if version != _DEPLOYMENT_JSON_VERSION:
        typer.echo(
            f"error: {target} is version {version!r}; this deployer "
            f"expects {_DEPLOYMENT_JSON_VERSION}. Re-run `wip-deploy "
            "install` to refresh.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        return Deployment.model_validate(payload["deployment"])
    except Exception as e:
        typer.echo(f"error: {target} failed to deserialize: {e}", err=True)
        raise typer.Exit(2) from e


def _try_load_previous_deployment(install_dir: Path) -> Deployment | None:
    """Return the persisted Deployment, or None if absent / unreadable.

    Forgiving counterpart to `_load_deployment` (which exits the process
    on missing or invalid state). Used by the install flow's
    CASE-331 Fix B drop-warning logic: if there's nothing to compare
    against (first install, corrupt state file, schema mismatch), we
    simply skip the comparison and proceed.
    """
    target = install_dir / _DEPLOYMENT_STATE_FILENAME
    legacy = install_dir / "deployment.json"
    if not target.exists() and legacy.exists():
        target = legacy
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text())
    except json.JSONDecodeError:
        return None
    if payload.get("wip_deploy_format_version") != _DEPLOYMENT_JSON_VERSION:
        return None
    try:
        return Deployment.model_validate(payload["deployment"])
    except Exception:
        return None


def _diff_spec_for_drops(
    previous: Deployment, current: Deployment
) -> tuple[list[str], list[str]]:
    """Return (apps_dropped, modules_dropped) when re-running install.

    An app is "dropped" if it was enabled in the previous spec but is
    either absent or disabled in the current one. A module is "dropped"
    if it was in the previous `modules.optional` list but is absent from
    the current one. (CASE-331 Fix B.)
    """
    prev_enabled_apps = {a.name for a in previous.spec.apps if a.enabled}
    new_enabled_apps = {a.name for a in current.spec.apps if a.enabled}
    dropped_apps = sorted(prev_enabled_apps - new_enabled_apps)

    prev_modules = set(previous.spec.modules.optional)
    new_modules = set(current.spec.modules.optional)
    dropped_modules = sorted(prev_modules - new_modules)

    return dropped_apps, dropped_modules


def _print_drop_warning_and_exit(
    *,
    dropped_apps: list[str],
    dropped_modules: list[str],
) -> None:
    """Emit the CASE-331 Fix B abort message and exit non-zero."""
    typer.echo(
        typer.style(
            "✗ install aborted: this would remove state from the "
            "previous install.",
            fg=typer.colors.RED,
            bold=True,
        ),
        err=True,
    )
    if dropped_apps:
        typer.echo("", err=True)
        typer.echo("  Apps to remove:", err=True)
        for name in dropped_apps:
            typer.echo(f"    - {name}", err=True)
    if dropped_modules:
        typer.echo("", err=True)
        typer.echo("  Optional modules to remove:", err=True)
        for name in dropped_modules:
            typer.echo(f"    - {name}", err=True)
    typer.echo("", err=True)
    typer.echo(
        "  If this is what you want, re-run with --reconcile. Otherwise "
        "re-add the missing --app-source / --add flags so the previous "
        "state is preserved.",
        err=True,
    )
    raise typer.Exit(2)


def _do_status_diff(install_dir: Path, namespace_override: str | None) -> None:
    """Re-render manifests from the persisted Deployment, write to a
    temp dir, run `kubectl diff` against the live cluster, exit with
    its return code. K8s target only.
    """
    deployment = _load_deployment(install_dir)
    if deployment.spec.target != "k8s":
        typer.echo(
            f"error: --diff currently supports k8s target only "
            f"(got {deployment.spec.target!r})",
            err=True,
        )
        raise typer.Exit(2)

    try:
        root = find_repo_root()
    except FileNotFoundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    discovery = discover(root)
    if not discovery.ok:
        typer.echo("manifest discovery errors:", err=True)
        for err in discovery.errors:
            typer.echo(f"  - {err}", err=True)
        raise typer.Exit(1)

    secrets = _ensure_secrets_via_spec(
        deployment, discovery.components, discovery.apps
    )
    tree = _render_tree(
        deployment, discovery.components, discovery.apps, secrets
    )

    with tempfile.TemporaryDirectory(prefix="wip-deploy-diff-") as td:
        td_path = Path(td)
        tree.write(td_path)

        ns = namespace_override or (
            deployment.spec.platform.k8s.namespace
            if deployment.spec.platform.k8s
            else None
        )
        cmd = ["kubectl", "diff", "-f", str(td_path), "-R"]
        if ns:
            cmd.extend(["-n", ns])

        try:
            result = subprocess.run(cmd, check=False)
        except FileNotFoundError as e:
            typer.echo(
                "error: kubectl not found on PATH. Install kubectl and "
                "ensure it's configured against the target cluster.",
                err=True,
            )
            raise typer.Exit(2) from e
        raise typer.Exit(result.returncode)


def _ensure_secrets_via_spec(
    deployment: Deployment,
    components: list[Component],
    apps_list: list[App],
) -> ResolvedSecrets:
    """Construct the secret backend from `spec.secrets` and run
    `ensure_secrets`."""
    spec = deployment.spec.secrets
    if spec.backend != "file":
        typer.echo(
            f"error: only the file secret backend is implemented; "
            f"got {spec.backend!r}",
            err=True,
        )
        raise typer.Exit(2)

    location = spec.location
    if location is None:
        typer.echo("error: secrets.location is required for file backend", err=True)
        raise typer.Exit(2)

    backend = FileSecretBackend(Path(location))
    return ensure_secrets(deployment, components, apps_list, backend)


def _render_tree(
    deployment: Deployment,
    components: list[Component],
    apps_list: list[App],
    secrets: ResolvedSecrets,
) -> FileTree:
    """Dispatch to the right renderer for the deployment's target."""
    if deployment.spec.target == "compose":
        return render_compose(deployment, components, apps_list, secrets)
    if deployment.spec.target == "k8s":
        return render_k8s(deployment, components, apps_list, secrets)
    if deployment.spec.target == "dev":
        dev_plat = deployment.spec.platform.dev
        if dev_plat is None or dev_plat.mode != "simple":
            typer.echo(
                f"error: dev target requires mode='simple'; got "
                f"{dev_plat.mode if dev_plat else None!r} "
                f"(tilt mode is a follow-up)",
                err=True,
            )
            raise typer.Exit(2)
        try:
            root = find_repo_root()
        except FileNotFoundError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1) from e
        return render_dev_simple(
            deployment, components, apps_list, secrets, repo_root=root,
        )
    typer.echo(
        f"error: renderer for target={deployment.spec.target!r} not implemented yet",
        err=True,
    )
    raise typer.Exit(2)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
