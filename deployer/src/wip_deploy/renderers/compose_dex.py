"""Dex config YAML emitter.

Converts a `DexConfig` (structured) into the YAML Dex expects, bcrypting
the user passwords with a cost of 10 (matches v1's htpasswd -nbBC 10).

The Dex binary wants a very particular shape — see
https://dexidp.io/docs/configuration/ — so this is the one place where
we write schema that isn't our own.
"""

from __future__ import annotations

from typing import Any

import bcrypt
import yaml

from wip_deploy.config_gen.dex import DexConfig
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
        plaintext = secrets.get(user.password_secret.name)
        hashed = bcrypt.hashpw(
            plaintext.encode(), bcrypt.gensalt(rounds=10)
        ).decode()
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
