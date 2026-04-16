"""wip-deploy CLI entrypoint.

Step 3 scope: `validate` and `show-spec` verbs. Renderers + `install` /
`upgrade` / `render` come in later steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml

from wip_deploy import __version__
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import discover, find_repo_root
from wip_deploy.presets import PRESETS
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.validators import validate_all

app = typer.Typer(
    help="WIP declarative deployer (v2).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
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
    return typer.Option("--hostname", help="External hostname as seen by browsers.")


def _tls_opt() -> typer.models.OptionInfo:
    return typer.Option("--tls", help="TLS mode: internal | letsencrypt | external.")


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
    return typer.Option("--dev-mode", help="Dev mode: tilt | simple.")


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
    return typer.Option("--name", help="Deployment name.")


# ────────────────────────────────────────────────────────────────────
# validate
# ────────────────────────────────────────────────────────────────────


@app.command()
def validate(
    preset: Annotated[str, _preset_opt()] = "standard",
    target: Annotated[str, _target_opt()] = "compose",
    hostname: Annotated[str, _hostname_opt()] = "wip.local",
    tls: Annotated[str, _tls_opt()] = "internal",
    https_port: Annotated[int, _https_port_opt()] = 8443,
    http_port: Annotated[int, _http_port_opt()] = 8080,
    data_dir: Annotated[Path | None, _data_dir_opt()] = None,
    namespace: Annotated[str, _namespace_opt()] = "wip",
    storage_class: Annotated[str, _storage_class_opt()] = "rook-ceph-block",
    ingress_class: Annotated[str, _ingress_class_opt()] = "nginx",
    tls_secret_name: Annotated[str, _tls_secret_opt()] = "wip-tls",
    dev_mode: Annotated[str, _dev_mode_opt()] = "tilt",
    registry: Annotated[str | None, _registry_opt()] = None,
    tag: Annotated[str, _tag_opt()] = "latest",
    add: Annotated[list[str], _add_opt()] = [],
    remove: Annotated[list[str], _remove_opt()] = [],
    apps: Annotated[list[str], _app_opt()] = [],
    auth_mode: Annotated[str | None, _auth_mode_opt()] = None,
    auth_gateway: Annotated[bool | None, _auth_gateway_opt()] = None,
    secrets_backend: Annotated[str | None, _secrets_backend_opt()] = None,
    secrets_location: Annotated[str | None, _secrets_location_opt()] = None,
    repo_root: Annotated[Path | None, _repo_root_opt()] = None,
    name: Annotated[str, _name_opt()] = "default",
) -> None:
    """Validate a deployment configuration without rendering or applying.

    Builds the Deployment from preset + flags, discovers every
    wip-component.yaml / wip-app.yaml, and runs all cross-cutting
    validators. Exit 0 on success, 1 on failure.
    """
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
    hostname: Annotated[str, _hostname_opt()] = "wip.local",
    tls: Annotated[str, _tls_opt()] = "internal",
    https_port: Annotated[int, _https_port_opt()] = 8443,
    http_port: Annotated[int, _http_port_opt()] = 8080,
    data_dir: Annotated[Path | None, _data_dir_opt()] = None,
    namespace: Annotated[str, _namespace_opt()] = "wip",
    storage_class: Annotated[str, _storage_class_opt()] = "rook-ceph-block",
    ingress_class: Annotated[str, _ingress_class_opt()] = "nginx",
    tls_secret_name: Annotated[str, _tls_secret_opt()] = "wip-tls",
    dev_mode: Annotated[str, _dev_mode_opt()] = "tilt",
    registry: Annotated[str | None, _registry_opt()] = None,
    tag: Annotated[str, _tag_opt()] = "latest",
    add: Annotated[list[str], _add_opt()] = [],
    remove: Annotated[list[str], _remove_opt()] = [],
    apps: Annotated[list[str], _app_opt()] = [],
    auth_mode: Annotated[str | None, _auth_mode_opt()] = None,
    auth_gateway: Annotated[bool | None, _auth_gateway_opt()] = None,
    secrets_backend: Annotated[str | None, _secrets_backend_opt()] = None,
    secrets_location: Annotated[str | None, _secrets_location_opt()] = None,
    repo_root: Annotated[Path | None, _repo_root_opt()] = None,
    name: Annotated[str, _name_opt()] = "default",
    output_format: Annotated[
        str, typer.Option("--format", help="yaml | json")
    ] = "yaml",
) -> None:
    """Build the Deployment from preset + flags and dump it.

    Useful for debugging: "what does --preset standard actually resolve to?"
    No discovery, no validation — just the computed spec.
    """
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
# Internal glue
# ────────────────────────────────────────────────────────────────────


def _assemble(
    *,
    preset: str,
    target: str,
    hostname: str,
    tls: str,
    https_port: int,
    http_port: int,
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
    auth_mode: str | None,
    auth_gateway: bool | None,
    secrets_backend: str | None,
    secrets_location: str | None,
    repo_root: Path | None,
    name: str,
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
        auth_mode=auth_mode,
        auth_gateway=auth_gateway,
        secrets_backend=secrets_backend,
        secrets_location=secrets_location,
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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
