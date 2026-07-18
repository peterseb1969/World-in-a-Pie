"""Config-file API key generation (CASE-655).

Spec-declared API keys (`auth.api_keys`) render into wip-auth's
config-file key mechanism: a JSON document consumed via
WIP_AUTH_API_KEYS_FILE by every component whose manifest declares that
env var. The plaintext for each key comes from the install's secret
backend (`<name>-api-key`, generated on first apply, stable across
re-renders), and each entry carries the key's read scope and write
grants — so the whole credential survives a MongoDB wipe/restore with
no re-registration step.

Target-neutral decision layer: compose/dev mount the rendered file,
k8s mounts it from a Secret. Renderers only serialize.
"""

from __future__ import annotations

import json

from wip_deploy.secrets_backend.base import ResolvedSecrets
from wip_deploy.spec import Deployment

# Where the rendered file appears inside service containers; injected
# as WIP_AUTH_API_KEYS_FILE via `from_spec: security.api_keys_file`.
API_KEYS_CONTAINER_PATH = "/etc/wip/api-keys.json"

# Relative path inside the rendered install tree (compose/dev). The
# file carries plaintext keys, so it is written mode 0600 — same
# posture as the install's `.env`.
API_KEYS_RENDER_PATH = "config/auth/api-keys.json"


def api_key_secret_names(deployment: Deployment) -> list[str]:
    """Secret-backend names backing the spec-declared keys."""
    return [k.secret_name for k in deployment.spec.auth.api_keys]


def declares_api_keys_file(owner) -> bool:
    """Does this component/app manifest consume the rendered key file?

    The WIP_AUTH_API_KEYS_FILE env declaration is the opt-in: renderers
    mount the file for exactly the manifests that declare it — no
    renderer-side component name list to drift.
    """
    return any(
        ev.name == "WIP_AUTH_API_KEYS_FILE" for ev in owner.spec.env.required
    )


def generate_api_keys_json(
    deployment: Deployment, secrets: ResolvedSecrets
) -> str | None:
    """The WIP_AUTH_API_KEYS_FILE document, or None when no keys declared.

    Deterministic for a fixed spec + secret values: plaintext `key`
    entries (hashed by wip-auth on load) keep renders byte-stable —
    rendering bcrypt hashes instead would produce a fresh hash every
    render and false drift in `status --diff`.
    """
    keys = deployment.spec.auth.api_keys
    if not keys:
        return None

    entries = []
    for k in keys:
        entries.append(
            {
                "name": k.name,
                "key": secrets.get(k.secret_name),
                "owner": k.owner,
                "groups": k.groups,
                "namespaces": k.namespaces,
                "grants": k.grants,
                "description": (
                    "Spec-declared key (wip-deploy auth.api_keys) — "
                    "scope and grants resolve locally in wip-auth"
                ),
            }
        )
    return json.dumps({"keys": entries}, indent=2) + "\n"
