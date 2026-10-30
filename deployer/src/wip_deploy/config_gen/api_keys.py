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
from wip_deploy.spec import App, Component, Deployment
from wip_deploy.spec.deployment import CANARY_KEY_NAME, SpecAPIKey

# Where the rendered file appears inside service containers; injected
# as WIP_AUTH_API_KEYS_FILE via `from_spec: security.api_keys_file`.
API_KEYS_CONTAINER_PATH = "/etc/wip/api-keys.json"

# The file is mounted through its DIRECTORY, never as a single file: a
# running container then sees a rewritten file (k8s refreshes a directory-
# mounted Secret in place, a compose directory bind follows the host
# directory), and wip-auth re-reads it without a restart. A k8s `subPath`
# or a single-file bind would pin the content the container started with.
API_KEYS_CONTAINER_DIR = "/etc/wip"

# Relative path inside the rendered install tree (compose/dev). The
# file carries plaintext keys, so it is written mode 0600 — same
# posture as the install's `.env`.
API_KEYS_RENDER_PATH = "config/auth/api-keys.json"
API_KEYS_RENDER_DIR = "config/auth"

# The federated group policy (CASE-947) lives in the same directory, so the
# one directory mount carries both and both are re-read while running.
# Rendered on every install — empty when no connector is declared — so the
# mount exists from the first render and a policy change alters only the
# file's content, never a container definition.
FEDERATED_GROUPS_FILENAME = "federated-groups.json"
FEDERATED_GROUPS_CONTAINER_PATH = f"/etc/wip/{FEDERATED_GROUPS_FILENAME}"
FEDERATED_GROUPS_RENDER_PATH = f"config/auth/{FEDERATED_GROUPS_FILENAME}"

# The namespace the canary is scoped to. Nothing creates it, so the
# canary can read nothing; `verify --live` checks that it still sees no
# namespace at all.
CANARY_NAMESPACE = "wip-canary-unassigned"

# Every install's canary (CASE-846): a key that authenticates but is
# authorized for nothing — no groups, no grants, scoped to a namespace
# that does not exist. `wip-deploy verify --security --live` sends it to
# each service's privileged and namespace-scoped routes and requires a
# refusal, so a running install's authorization is checked, not assumed.
# It is added to every install so the probe can run anywhere; its
# secret lives beside the master key in the same store.
CANARY_KEY = SpecAPIKey(name=CANARY_KEY_NAME, namespaces=[CANARY_NAMESPACE])


def effective_api_keys(deployment: Deployment) -> list[SpecAPIKey]:
    """The config-file keys an install carries: its spec's, plus the canary."""
    return [*deployment.spec.auth.api_keys, CANARY_KEY]


def api_key_secret_names(deployment: Deployment) -> list[str]:
    """Secret-backend names backing the config-file keys."""
    return [k.secret_name for k in effective_api_keys(deployment)]


def declares_api_keys_file(owner: Component | App) -> bool:
    """Does this component/app manifest consume the rendered key file?

    The WIP_AUTH_API_KEYS_FILE env declaration is the opt-in: renderers
    mount the file for exactly the manifests that declare it — no
    renderer-side component name list to drift.
    """
    return any(
        ev.name == "WIP_AUTH_API_KEYS_FILE" for ev in owner.spec.env.required
    )


def mounts_auth_config_dir(owner: Component | App) -> bool:
    """Does this manifest read a file from the auth config directory
    (the declared API keys, the federated group policy or the namespace
    reservations)? The env declarations are the opt-in, as for
    `declares_api_keys_file`."""
    names = {ev.name for ev in (*owner.spec.env.required, *owner.spec.env.optional)}
    return bool(
        names
        & {
            "WIP_AUTH_API_KEYS_FILE",
            "WIP_AUTH_FEDERATED_GROUPS_FILE",
            "WIP_NAMESPACE_RESERVATIONS_FILE",
        }
    )


def generate_api_keys_json(
    deployment: Deployment, secrets: ResolvedSecrets
) -> str:
    """The WIP_AUTH_API_KEYS_FILE document. Every install has one: the
    canary is always in it.

    Deterministic for a fixed spec + secret values: plaintext `key`
    entries (hashed by wip-auth on load) keep renders byte-stable —
    rendering bcrypt hashes instead would produce a fresh hash every
    render and false drift in `status --diff`.
    """
    entries = []
    for k in effective_api_keys(deployment):
        entries.append(
            {
                "name": k.name,
                "key": secrets.get(k.secret_name),
                "owner": k.owner,
                "groups": k.groups,
                "namespaces": k.namespaces,
                "grants": k.grants,
                "description": (
                    "Canary — authenticates, authorized for nothing; used by "
                    "wip-deploy verify --security --live"
                    if k.name == CANARY_KEY_NAME
                    else "Spec-declared key (wip-deploy auth.api_keys) — "
                    "scope and grants resolve locally in wip-auth"
                ),
            }
        )
    return json.dumps({"keys": entries}, indent=2) + "\n"
