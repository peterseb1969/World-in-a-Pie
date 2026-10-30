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
from typing import Literal

from wip_deploy.secrets_backend.base import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Component, Deployment
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


# An app's own key is named after the app, so `rotate-key app-<name>` names it.
APP_KEY_PREFIX = "app-"


def app_api_key(app: App, ref: AppRef | None = None) -> SpecAPIKey | None:
    """The key an app runs with, derived from its manifest; None for the
    app that declares `privileged_key` (it holds the master key instead).

    `admin` on every namespace the app owns (`spec.namespaces`): its
    bootstrap creates them through their reservation and registers their
    views. `read` or `write` on each `spec.namespace_access` entry. `read`
    on the platform's `wip` namespace, whose vocabularies every app may
    browse. Nothing else: no admin group, no other namespace.

    `ref` is the app's entry in this install's spec: namespaces the operator
    added on this install (`wip-deploy app-namespace`) count like the
    manifest's own.
    """
    if app.spec.privileged_key:
        return None
    added = ref.namespaces if ref is not None else []
    added_access = ref.namespace_access if ref is not None else []
    owned = [n.name for n in app.spec.namespaces] + [n.name for n in added]
    grants: dict[str, Literal["read", "write", "admin"]] = dict.fromkeys(owned, "admin")
    for access in [*app.spec.namespace_access, *added_access]:
        grants[access.namespace] = access.permission
    scope = list(dict.fromkeys([*grants, "wip"]))
    return SpecAPIKey(
        name=f"{APP_KEY_PREFIX}{app.metadata.name}",
        namespaces=scope,
        grants={ns: perm for ns, perm in grants.items() if perm != "read"},
        owner=f"app:{app.metadata.name}",
    )


def app_api_keys(deployment: Deployment, apps: list[App]) -> list[SpecAPIKey]:
    """The derived keys of the install's enabled apps."""
    refs = {r.name: r for r in deployment.spec.apps if r.enabled}
    keys = (app_api_key(a, refs[a.metadata.name]) for a in apps if a.metadata.name in refs)
    return [k for k in keys if k is not None]


def effective_api_keys(
    deployment: Deployment, apps: list[App] | None = None
) -> list[SpecAPIKey]:
    """The config-file keys an install carries: its spec's, one per enabled
    app (derived, never stored in the spec), plus the canary."""
    return [*deployment.spec.auth.api_keys, *app_api_keys(deployment, apps or []), CANARY_KEY]


def api_key_secret_names(deployment: Deployment, apps: list[App] | None = None) -> list[str]:
    """Secret-backend names backing the config-file keys."""
    return [k.secret_name for k in effective_api_keys(deployment, apps)]


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
    deployment: Deployment, secrets: ResolvedSecrets, apps: list[App] | None = None
) -> str:
    """The WIP_AUTH_API_KEYS_FILE document. Every install has one: the
    canary is always in it.

    Deterministic for a fixed spec + secret values: plaintext `key`
    entries (hashed by wip-auth on load) keep renders byte-stable —
    rendering bcrypt hashes instead would produce a fresh hash every
    render and false drift in `status --diff`.
    """
    entries = []
    for k in effective_api_keys(deployment, apps):
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
                    else f"App key for {k.owner.removeprefix('app:')} — derived from "
                    "its manifest (spec.namespaces, spec.namespace_access)"
                    if k.owner.startswith("app:")
                    else "Spec-declared key (wip-deploy auth.api_keys) — "
                    "scope and grants resolve locally in wip-auth"
                ),
            }
        )
    return json.dumps({"keys": entries}, indent=2) + "\n"
