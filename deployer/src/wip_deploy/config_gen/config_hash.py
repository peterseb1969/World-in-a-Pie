"""Per-workload hash of the configuration a container only reads at start.

A service receives its secrets as environment values and, on k8s, the
router its Caddyfile through a `subPath` mount. Both are read once, when the
process starts, and a `subPath` mount is never refreshed in a running pod.
Changing only their content — a rotated master key, a re-rendered route —
leaves the rendered workload definition identical, so `kubectl apply` rolls
nothing and `compose up -d` recreates nothing: the change is silently not
applied until something restarts the service.

The declared-key file (`api-keys.json`) is deliberately NOT an input: it is
mounted through its directory and wip-auth re-reads it while running, so a
declared key is added, removed or rotated without restarting anything.

The renderers stamp this hash on each workload (k8s: a pod-template
annotation, compose: a service label), so a content change becomes a
definition change and the apply restarts exactly the services whose inputs
moved.

Scope: every owner except a storage-bearing component. A storage-bearing
component (a database, the object store) renders as a StatefulSet on k8s,
whose rolling replacement of a ReadWriteOnce volume can stall, and its
secrets (database passwords) cannot be rotated by a restart anyway — the
password lives in the data volume. An app that declares storage is hashed
like any app: its secrets are keys it reads at start, which a restart does
rotate, and its volume holds app state, which the restart keeps. Without
the hash, rotating such an app's key left it running on the old key, which
the install refuses once the rotation applies.
"""

from __future__ import annotations

import hashlib
import json

from wip_deploy.config_gen.env import ResolvedEnv, SecretRef
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

CONFIG_HASH_KEY = "wip.dev/config-hash"


def restarts_on_config_change(owner: Component | App) -> bool:
    """Whether `owner` is restarted when the secrets or start-time files it
    reads change: every owner but a storage-bearing component (see the module
    docstring)."""
    return isinstance(owner, App) or not owner.spec.storage


def config_hash(
    owner: Component | App,
    deployment: Deployment,
    env: ResolvedEnv,
    secrets: ResolvedSecrets,
    *,
    mounted_files: dict[str, str] | None = None,
) -> str | None:
    """Hash of the secrets and start-time files `owner` consumes, or None.

    None for storage-bearing components and for owners that consume none of
    these inputs (their definition already captures everything they read).
    Inputs are rendered deterministically, so an unchanged install hashes
    identically and a no-op re-apply restarts nothing.
    """
    if not restarts_on_config_change(owner):
        return None

    inputs: dict[str, str] = {}
    for value in env.merged().values():
        if isinstance(value, SecretRef):
            inputs[f"secret:{value.name}"] = secrets.values.get(value.name, "")
    for name, content in (mounted_files or {}).items():
        inputs[f"file:{name}"] = content

    if not inputs:
        return None
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return digest[:16]
