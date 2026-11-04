"""Config-file API key generation (CASE-655).

Spec-declared API keys (`auth.api_keys`), each app's derived key and the
canary render into one JSON document, the key file. The plaintext for
each key comes from the install's secret backend (`<name>-api-key`,
generated on first apply, stable across re-renders), and each entry
carries the key's read scope and write grants.

Only the Registry reads the key file (WIP_REGISTRY_KEY_FILE): it imports
every key, its grants and the reservations into its own records, and
every other service resolves keys through the Registry. The file lives
in a directory of its own, mounted into exactly the manifests that
declare it, so no other container holds the plaintext. A manifest that
still declares WIP_AUTH_API_KEYS_FILE is mounted the same directory and
loads the file as wip-auth config keys (the pre-import model).

Target-neutral decision layer: compose/dev mount the rendered file,
k8s mounts it from a Secret. Renderers only serialize.
"""

from __future__ import annotations

import json
from typing import Literal

from wip_deploy.secrets_backend.base import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Component, Deployment
from wip_deploy.spec.deployment import CANARY_KEY_NAME, SpecAPIKey

# The key file has a directory of its own, mounted only into the manifests
# that declare it (the Registry), so the plaintext reaches no other
# container. Mounted through the DIRECTORY, never as a single file: a
# running container then sees a rewritten file (k8s refreshes a directory-
# mounted Secret in place, a compose directory bind follows the host
# directory), and the Registry re-imports it without a restart. A k8s
# `subPath` or a single-file bind would pin the content the container
# started with. Injected as WIP_REGISTRY_KEY_FILE via
# `from_spec: security.api_keys_file`.
KEY_FILE_CONTAINER_DIR = "/etc/wip-keys"
API_KEYS_CONTAINER_PATH = f"{KEY_FILE_CONTAINER_DIR}/api-keys.json"
# Relative paths inside the rendered install tree (compose/dev).
KEY_FILE_RENDER_DIR = "config/keys"
API_KEYS_RENDER_PATH = f"{KEY_FILE_RENDER_DIR}/api-keys.json"
KEY_FILE_ENVS = ("WIP_REGISTRY_KEY_FILE", "WIP_AUTH_API_KEYS_FILE")
# Where renders before the move wrote the key file: in the auth config
# directory, which every federated-groups reader mounts. A compose or dev
# render removes it, so an upgraded install keeps no plaintext there.
LEGACY_API_KEYS_RENDER_PATH = "config/auth/api-keys.json"

# The auth config directory: the federated group policy and the namespace
# reservations, mounted into every manifest that reads one of them. It
# holds no key material.
API_KEYS_CONTAINER_DIR = "/etc/wip"
API_KEYS_RENDER_DIR = "config/auth"

# The federated group policy lives in the auth config directory, re-read
# while running.
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


# The app edge's key (CASE-1052): its group may read the Registry's app
# attachments and nothing else. Scoped to the canary's namespace, which
# nothing creates, so it reads no data; the group is not one that may assert
# identities. Rendered whenever the edge is active.
APP_EDGE_GROUP = "wip-app-edge"
APP_EDGE_KEY = SpecAPIKey(
    name="wip-app-edge",
    namespaces=[CANARY_NAMESPACE],
    groups=[APP_EDGE_GROUP],
    owner="system:app-edge",
)


def app_edge_active(deployment: Deployment) -> bool:
    """The app-edge component's activation (its manifest: the gateway on,
    core not suppressed), for the key that goes with it."""
    return deployment.spec.auth.gateway and not deployment.spec.modules.suppress_core


def effective_api_keys(
    deployment: Deployment, apps: list[App] | None = None
) -> list[SpecAPIKey]:
    """The config-file keys an install carries: its spec's, one per enabled
    app (derived, never stored in the spec), the app edge's when it is
    active, plus the canary."""
    edge = [APP_EDGE_KEY] if app_edge_active(deployment) else []
    return [*deployment.spec.auth.api_keys, *app_api_keys(deployment, apps or []), *edge, CANARY_KEY]


def api_key_secret_names(deployment: Deployment, apps: list[App] | None = None) -> list[str]:
    """Secret-backend names backing the config-file keys."""
    return [k.secret_name for k in effective_api_keys(deployment, apps)]


MASTER_KEY_SECRET = "api-key"
"""The install's master key, which every service also loads as a key."""


def shared_api_key_values(
    deployment: Deployment, apps: list[App], secrets: ResolvedSecrets
) -> list[list[str]]:
    """Groups of key secrets that hold the same value.

    A plaintext must belong to exactly one key. Services match a presented
    key against their keys in order and cache the first match, so a value
    two keys share authenticates as whichever one a service happened to
    match — and services that started at different times disagree. The
    master key counts: an app key equal to it would sometimes be the master
    key.
    """
    names = dict.fromkeys([MASTER_KEY_SECRET, *api_key_secret_names(deployment, apps)])
    by_value: dict[str, list[str]] = {}
    for name in names:
        value = secrets.values.get(name)
        if value:
            by_value.setdefault(value, []).append(name)
    return sorted(group for group in by_value.values() if len(group) > 1)


def app_key_secrets(deployment: Deployment, apps: list[App]) -> dict[str, str]:
    """Secret name -> app, for the install's derived app keys."""
    return {
        k.secret_name: k.owner.removeprefix("app:")
        for k in app_api_keys(deployment, apps)
    }


def declares_api_keys_file(owner: Component | App) -> bool:
    """Does this component/app manifest read the rendered key file?

    The env declaration (WIP_REGISTRY_KEY_FILE, or the pre-import
    WIP_AUTH_API_KEYS_FILE) is the opt-in: renderers mount the key file's
    directory for exactly the manifests that declare it — no renderer-side
    component name list to drift.
    """
    names = {ev.name for ev in (*owner.spec.env.required, *owner.spec.env.optional)}
    return bool(names & set(KEY_FILE_ENVS))


def mounts_auth_config_dir(owner: Component | App) -> bool:
    """Does this manifest read a file from the auth config directory (the
    federated group policy or the namespace reservations)? The env
    declarations are the opt-in, as for `declares_api_keys_file`."""
    names = {ev.name for ev in (*owner.spec.env.required, *owner.spec.env.optional)}
    return bool(
        names
        & {"WIP_AUTH_FEDERATED_GROUPS_FILE", "WIP_NAMESPACE_RESERVATIONS_FILE", "WIP_ATTACH_POLICY_FILE"}
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
