"""Dex OIDC provider config generation.

Produces a structured `DexConfig` from the Deployment + active components
+ enabled apps. Renderers serialize to Dex's YAML format and wire secrets
(user password hashes, client secrets) at render time.

Skipped entirely when `auth.mode == "api-key-only"` — the Dex component
isn't active in that mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from wip_deploy.config_gen.env import SecretRef
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


@dataclass(frozen=True)
class DexUserEntry:
    email: str
    username: str
    group: str
    password_secret: SecretRef  # the plain-text password; renderer bcrypts
    user_id: str  # stable ID; derived from username


@dataclass(frozen=True)
class DexClientEntry:
    client_id: str
    name: str  # human display name
    secret: SecretRef
    redirect_uris: list[str]


@dataclass(frozen=True)
class DexConfig:
    """Structured Dex configuration. Renderer emits Dex YAML."""

    issuer: str
    users: list[DexUserEntry]
    clients: list[DexClientEntry]
    id_token_ttl: str  # e.g., "15m" — comes from auth.session_ttl


# ────────────────────────────────────────────────────────────────────


def generate_dex_config(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> DexConfig | None:
    """Compute the Dex config for this deployment.

    Returns None when Dex isn't active (api-key-only mode). Callers should
    skip emitting Dex config in that case.
    """
    dex = next((c for c in components if c.metadata.name == "dex"), None)
    if dex is None or not is_component_active(dex, deployment):
        return None

    issuer = _issuer_url(deployment)
    users = _dex_users(deployment)
    clients = _dex_clients(deployment, components, apps, issuer_base=_public_base(deployment))

    return DexConfig(
        issuer=issuer,
        users=users,
        clients=clients,
        id_token_ttl=deployment.spec.auth.session_ttl,
    )


# ────────────────────────────────────────────────────────────────────


def _public_base(deployment: Deployment) -> str:
    """Public base URL. Delegates to spec_context's helper so Dex client
    redirect_uris exactly match what the browser actually requests."""
    from wip_deploy.config_gen.spec_context import _public_base as _spec_ctx_public_base
    return _spec_ctx_public_base(deployment)


def _issuer_url(deployment: Deployment) -> str:
    return f"{_public_base(deployment)}/dex"


def _user_id(username: str) -> str:
    # Stable, readable. Matches v1's "<role>-001" style for continuity.
    return f"{username}-001"


def _dex_users(deployment: Deployment) -> list[DexUserEntry]:
    out: list[DexUserEntry] = []
    for u in deployment.spec.auth.users:
        # Secret name: e.g., "dex-password-admin". One per user so they
        # can be rotated independently.
        secret_name = f"dex-password-{u.username}"
        out.append(
            DexUserEntry(
                email=u.email,
                username=u.username,
                group=u.group,
                password_secret=SecretRef(secret_name),
                user_id=_user_id(u.username),
            )
        )
    return out


def _dex_clients(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    *,
    issuer_base: str,
) -> list[DexClientEntry]:
    out: list[DexClientEntry] = []

    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    for c in components:
        if not is_component_active(c, deployment) or c.spec.oidc_client is None:
            continue
        out.append(
            _client_entry(
                client_id=c.spec.oidc_client.client_id,
                name=c.metadata.description.splitlines()[0].strip() or c.metadata.name,
                redirect_paths=c.spec.oidc_client.redirect_paths,
                issuer_base=issuer_base,
            )
        )

    for a in apps:
        if a.metadata.name not in enabled_app_names or a.spec.oidc_client is None:
            continue
        out.append(
            _client_entry(
                client_id=a.spec.oidc_client.client_id,
                name=a.app_metadata.display_name,
                redirect_paths=a.spec.oidc_client.redirect_paths,
                issuer_base=issuer_base,
                app_route_prefix=a.app_metadata.route_prefix,
            )
        )

    # Stable output order for deterministic renders.
    out.sort(key=lambda c: c.client_id)
    return out


def _client_entry(
    *,
    client_id: str,
    name: str,
    redirect_paths: list[str],
    issuer_base: str,
    app_route_prefix: str | None = None,
) -> DexClientEntry:
    # For apps, prefix redirect paths with the app's route so callbacks
    # land back at the app, not at the console.
    prefix = app_route_prefix or ""
    redirect_uris = [f"{issuer_base}{prefix}{p}" for p in redirect_paths]
    # OIDC client secrets live in the secret backend by a well-known name.
    secret = SecretRef(f"dex-client-{client_id}")
    return DexClientEntry(
        client_id=client_id,
        name=name,
        secret=secret,
        redirect_uris=redirect_uris,
    )
