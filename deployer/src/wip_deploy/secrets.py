"""Secrets orchestrator.

Given a Deployment + discovered manifests, collect every required secret
name from two sources:

  1. `from_secret: <name>` references in **required** env sections of
     active components and enabled apps. (Optional env vars' secrets
     are not auto-generated — supply them manually if needed.)
  2. Dex user passwords + static client secrets emitted by
     `generate_dex_config` (when Dex is active). For each user
     password, a paired derived secret `<name>.bcrypt` is also
     collected and cached so renders are deterministic (CASE-295).

Then ensure each named secret has a value via the backend, generating
fresh random values for any missing names and returning a
`ResolvedSecrets` for renderers to consume.
"""

from __future__ import annotations

import secrets as py_secrets
import string
from collections.abc import Callable

import bcrypt

from wip_deploy.config_gen.dex import generate_dex_config
from wip_deploy.config_gen.garage import (
    GARAGE_SECRET_NAMES,
    SECRET_KEY_ID,
    SECRET_KEY_SECRET,
    SECRET_RPC,
    garage_component,
)
from wip_deploy.config_gen.mongodb import (
    PASSWORD_SECRET_PREFIX as MONGO_PASSWORD_PREFIX,
)
from wip_deploy.config_gen.mongodb import (
    ROOT_PASSWORD_SECRET as MONGO_ROOT_PASSWORD_SECRET,
)
from wip_deploy.config_gen.mongodb import mongo_clients, mongodb_component
from wip_deploy.config_gen.mongodb import password_secret as mongo_password_secret
from wip_deploy.config_gen.nats import (
    PASSWORD_SECRET_PREFIX as NATS_PASSWORD_PREFIX,
)
from wip_deploy.config_gen.nats import nats_clients, password_secret
from wip_deploy.secrets_backend.base import ResolvedSecrets, SecretBackend
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# CASE-295: derived bcrypt-hash secret naming. Renderers read the
# cached hash via `secrets.get(name + BCRYPT_SUFFIX)` so the output
# is byte-stable across renders. Only renders should consume the
# hashed value; flows that need to verify a user-supplied password
# read the plaintext.
BCRYPT_SUFFIX = ".bcrypt"


def bcrypt_secret_name(plaintext_name: str) -> str:
    """Derived-secret naming convention. See `BCRYPT_SUFFIX`."""
    return f"{plaintext_name}{BCRYPT_SUFFIX}"

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
      - NATS user passwords (one per active `nats_client`, when NATS is active)
      - MongoDB root + user passwords (one per active `mongo_client`)
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

    # Spec-declared API keys (CASE-655): one secret per key holds the
    # plaintext, generated on first apply and stable thereafter — the
    # config-file key survives Mongo rebuilds because its material
    # lives here, not in the registry's api_keys collection.
    for k in deployment.spec.auth.api_keys:
        names.add(k.secret_name)

    # Garage object storage (CASE-864): its secrets live in the rendered
    # garage.toml and in document-store's env, not in garage's own env
    # declaration, so manifest scanning cannot find them.
    if garage_component(deployment, components) is not None:
        names.update(GARAGE_SECRET_NAMES)

    # NATS users (CASE-852): one password per declared nats_client. The
    # password reaches the client through env injection and NATS through
    # its own env, neither of which the manifest scan above can see.
    for client, _ in nats_clients(deployment, components, apps):
        names.add(password_secret(client))

    # MongoDB users (CASE-890): root plus one password per declared
    # mongo_client, reaching MongoDB and the client through env injection.
    if mongodb_component(deployment, components) is not None:
        names.add(MONGO_ROOT_PASSWORD_SECRET)
        for client, _ in mongo_clients(deployment, components, apps):
            names.add(mongo_password_secret(client))

    dex_cfg = generate_dex_config(deployment, components, apps)
    if dex_cfg is not None:
        for u in dex_cfg.users:
            names.add(u.password_secret.name)
            # CASE-295: pair the plaintext with a cached bcrypt hash so
            # renders are deterministic. Without this, each render
            # bcrypts afresh — bcrypt.gensalt() is non-deterministic by
            # design — and `wip-deploy status --diff` reports false drift
            # on the dex-config ConfigMap every time.
            names.add(bcrypt_secret_name(u.password_secret.name))
        for dex_client in dex_cfg.clients:
            names.add(dex_client.secret.name)

    return names


def _collect_from_env(owner: Component | App, names: set[str]) -> None:
    for ev in owner.spec.env.required:
        if ev.source.from_secret is not None:
            names.add(ev.source.from_secret)


# ────────────────────────────────────────────────────────────────────


def _hex32_generator() -> str:
    """32 random bytes, hex-encoded (64 chars) — Garage's rpc_secret and
    key-secret format."""
    return py_secrets.token_hex(32)


def _garage_key_id_generator() -> str:
    """A Garage access key id: "GK" + 12 random bytes hex-encoded —
    the format `garage key import` requires."""
    return "GK" + py_secrets.token_hex(12)


def _nats_password_generator() -> str:
    """A NATS user password: letter-first alphanumeric, 22 chars (~130 bits).

    The server config references these as bare `$NATS_PASSWORD_*` tokens —
    NATS substitutes the env value into the config text and re-parses it,
    so the value must read as a bare config word. A value starting with a
    digit is parsed as a number and kills the server at startup, and
    quoting the reference ("$VAR") would stop substitution entirely.
    """
    alphabet = string.ascii_letters + string.digits
    return py_secrets.choice(string.ascii_letters) + "".join(
        py_secrets.choice(alphabet) for _ in range(21)
    )


# Secrets whose consumer has a format constraint, keyed by exact name
# (the documented exception to "opaque random is strictly better").
# NATS passwords are per-client dynamic names — they match by prefix
# in _format_generator below.
_FORMAT_GENERATORS: dict[str, Callable[[], str]] = {
    SECRET_RPC: _hex32_generator,
    SECRET_KEY_SECRET: _hex32_generator,
    SECRET_KEY_ID: _garage_key_id_generator,
}


def _format_generator(name: str) -> Callable[[], str]:
    """The generator for a secret name: exact format map, then the NATS and
    MongoDB password names, then the opaque default."""
    if name in _FORMAT_GENERATORS:
        return _FORMAT_GENERATORS[name]
    if name.startswith(NATS_PASSWORD_PREFIX):
        return _nats_password_generator
    if name.startswith(MONGO_PASSWORD_PREFIX) or name == MONGO_ROOT_PASSWORD_SECRET:
        # Plain alphanumerics travel safely through every place a password
        # goes (env, mongosh, a URI if anyone builds one).
        return _nats_password_generator
    return default_generator


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
    *,
    persist: bool = True,
) -> ResolvedSecrets:
    """Ensure every required secret exists via the backend.

    The backend caches existing values and generates only for missing
    names. Persistence is a separate step (`backend.persist()`); this
    orchestrator calls it automatically on success. `persist=False` is the
    read-only mode a dry run needs: existing values are read, missing ones
    are generated in memory only, and nothing is written to the store.

    Derived secrets (CASE-295): names ending in `.bcrypt` are paired
    with the same name minus the suffix; the generator hashes the
    paired plaintext rather than producing a fresh random value. Sort
    order guarantees the plaintext is resolved first (e.g.,
    `dex-password-admin` < `dex-password-admin.bcrypt`).
    """
    names = collect_required_secrets(deployment, components, apps)

    resolved: dict[str, str] = {}
    for name in sorted(names):
        if name.endswith(BCRYPT_SUFFIX):
            plaintext_name = name[: -len(BCRYPT_SUFFIX)]
            plaintext = resolved.get(plaintext_name)
            if plaintext is None:
                # The plaintext should have been resolved earlier in
                # the sort order. If we get here, the convention was
                # violated (someone collected `<name>.bcrypt` without
                # `<name>`) — fail loudly.
                raise ValueError(
                    f"derived bcrypt secret {name!r} requires "
                    f"plaintext {plaintext_name!r} to be collected "
                    "alongside it"
                )
            generator = _bcrypt_generator(plaintext)
        else:
            generator = _format_generator(name)
        resolved[name] = backend.get_or_generate(name, generator)

    if persist:
        backend.persist()
    return ResolvedSecrets(values=resolved)


def _bcrypt_generator(plaintext: str):  # type: ignore[no-untyped-def]
    """Return a generator that hashes `plaintext` with bcrypt cost 10.

    Cost 10 matches the existing render path (was at compose_dex.py
    before CASE-295 moved the hashing into secret generation).
    """
    def _gen() -> str:
        return bcrypt.hashpw(
            plaintext.encode(), bcrypt.gensalt(rounds=10)
        ).decode()
    return _gen
