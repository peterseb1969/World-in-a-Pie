"""Dex config YAML emitter.

Converts a `DexConfig` (structured) into the YAML Dex expects. Reads
the bcrypt-hashed password from the secret backend (cached by
`ensure_secrets` per CASE-295) so the rendered output is
byte-deterministic across renders. Pre-CASE-295 this module hashed
on each render with `bcrypt.gensalt()`, which is non-deterministic
by design — `wip-deploy status --diff` then reported false drift on
the dex-config ConfigMap on every invocation.

The Dex binary wants a very particular shape — see
https://dexidp.io/docs/configuration/ — so this is the one place where
we write schema that isn't our own.
"""

from __future__ import annotations

from typing import Any

import yaml

from wip_deploy.config_gen.dex import DexConfig
from wip_deploy.secrets import bcrypt_secret_name
from wip_deploy.secrets_backend import ResolvedSecrets


def render_dex_config(dex: DexConfig, secrets: ResolvedSecrets) -> str:
    """Render a Dex config.yaml as a string."""
    body: dict[str, Any] = {
        "issuer": dex.issuer,
        "storage": {"type": "sqlite3", "config": {"file": "/data/dex.db"}},
        "web": {"http": "0.0.0.0:5556"},
        "expiry": {
            "idTokens": dex.id_token_ttl,
            "signingKeys": "6h",
            "refreshTokens": {
                "validIfNotUsedFor": "168h",
                "absoluteLifetime": "720h",
            },
        },
        "oauth2": {
            "skipApprovalScreen": True,
            "passwordConnector": "local",
            "responseTypes": ["code"],
        },
        "enablePasswordDB": True,
        "staticClients": _render_static_clients(dex, secrets),
        "staticPasswords": _render_static_passwords(dex, secrets),
        "connectors": [],
    }
    return yaml.safe_dump(body, sort_keys=False)


def _render_static_clients(
    dex: DexConfig, secrets: ResolvedSecrets
) -> list[dict[str, Any]]:
    return [
        {
            "id": c.client_id,
            "name": c.name,
            "secret": secrets.get(c.secret.name),
            "redirectURIs": list(c.redirect_uris),
        }
        for c in dex.clients
    ]


def _render_static_passwords(
    dex: DexConfig, secrets: ResolvedSecrets
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for user in dex.users:
        # CASE-295: read the cached bcrypt hash. ensure_secrets
        # generated this once at first install and persisted it; every
        # subsequent render reads the same value, making the dex-config
        # render deterministic.
        hashed = secrets.get(bcrypt_secret_name(user.password_secret.name))
        out.append(
            {
                "email": user.email,
                "hash": hashed,
                "username": user.username,
                "userID": user.user_id,
                "groups": [user.group],
            }
        )
    return out
