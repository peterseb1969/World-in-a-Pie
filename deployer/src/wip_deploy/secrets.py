"""Secrets orchestrator.

Given a Deployment + discovered manifests, collect every required secret
name from two sources:

  1. `from_secret: <name>` references in **required** env sections of
     active components and enabled apps. (Optional env vars' secrets
     are not auto-generated — supply them manually if needed.)
  2. Dex user passwords + static client secrets emitted by
     `generate_dex_config` (when Dex is active).

Then ensure each named secret has a value via the backend, generating
fresh random values for any missing names and returning a
`ResolvedSecrets` for renderers to consume.
"""

from __future__ import annotations

import secrets as py_secrets

from wip_deploy.config_gen.dex import generate_dex_config
from wip_deploy.secrets_backend.base import ResolvedSecrets, SecretBackend
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────


def collect_required_secrets(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> set[str]:
    """Return every secret name that MUST exist for this deployment.

    Sources:
      - `from_secret` in required env vars of active components + enabled apps
      - Dex user password secrets (when Dex is active)
      - Dex static client secrets (one per active OIDC-capable component/app)
    """
    names: set[str] = set()

    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    for c in components:
        if not is_component_active(c, deployment):
            continue
        _collect_from_env(c, names)

    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        _collect_from_env(a, names)

    dex_cfg = generate_dex_config(deployment, components, apps)
    if dex_cfg is not None:
        for u in dex_cfg.users:
            names.add(u.password_secret.name)
        for client in dex_cfg.clients:
            names.add(client.secret.name)

    return names


def _collect_from_env(owner: Component | App, names: set[str]) -> None:
    for ev in owner.spec.env.required:
        if ev.source.from_secret is not None:
            names.add(ev.source.from_secret)


# ────────────────────────────────────────────────────────────────────


def default_generator() -> str:
    """Default secret value generator: 22-char URL-safe random string (16
    bytes of entropy).

    Tune this per-name only when the consumer has a format constraint —
    otherwise opaque random is strictly better than anything readable.
    """
    return py_secrets.token_urlsafe(16)


def ensure_secrets(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    backend: SecretBackend,
) -> ResolvedSecrets:
    """Ensure every required secret exists via the backend.

    The backend caches existing values and generates only for missing
    names. Persistence is a separate step (`backend.persist()`); this
    orchestrator calls it automatically on success.
    """
    names = collect_required_secrets(deployment, components, apps)

    resolved: dict[str, str] = {}
    for name in sorted(names):
        resolved[name] = backend.get_or_generate(name, default_generator)

    backend.persist()
    return ResolvedSecrets(values=resolved)
