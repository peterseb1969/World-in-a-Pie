"""Per-workload hash of the configuration a container only reads at start.

A service receives its secrets as environment values and its key file
(`api-keys.json`) and, on k8s, the router's Caddyfile through `subPath`
mounts. All of these are read once, when the process starts, and a
`subPath` mount is never refreshed in a running pod. Changing only their
content — a rotated key, a re-rendered key file — leaves the rendered
workload definition identical, so `kubectl apply` rolls nothing and
`compose up -d` recreates nothing: the change is silently not applied
until something restarts the service.

The renderers stamp this hash on each workload (k8s: a pod-template
annotation, compose: a service label), so a content change becomes a
definition change and the apply restarts exactly the services whose inputs
moved.

Scope: services without storage only. Storage-bearing components render as
StatefulSets on k8s, whose rolling replacement of a ReadWriteOnce volume can
stall, and their secrets (database passwords) cannot be rotated by a
restart anyway — the password lives in the data volume.
"""

from __future__ import annotations

import hashlib
import json

from wip_deploy.config_gen.api_keys import declares_api_keys_file, generate_api_keys_json
from wip_deploy.config_gen.env import ResolvedEnv, SecretRef
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

CONFIG_HASH_KEY = "wip.dev/config-hash"


def config_hash(
    owner: Component | App,
    deployment: Deployment,
    env: ResolvedEnv,
    secrets: ResolvedSecrets,
    *,
    mounted_files: dict[str, str] | None = None,
) -> str | None:
    """Hash of the secrets and start-time files `owner` consumes, or None.

    None for storage-bearing owners and for owners that consume none of
    these inputs (their definition already captures everything they read).
    Inputs are rendered deterministically (api-keys.json carries plaintext,
    hashed by wip-auth on load), so an unchanged install hashes identically
    and a no-op re-apply restarts nothing.
    """
    if owner.spec.storage:
        return None

    inputs: dict[str, str] = {}
    for value in env.merged().values():
        if isinstance(value, SecretRef):
            inputs[f"secret:{value.name}"] = secrets.values.get(value.name, "")
    if declares_api_keys_file(owner):
        inputs["file:api-keys.json"] = generate_api_keys_json(deployment, secrets)
    for name, content in (mounted_files or {}).items():
        inputs[f"file:{name}"] = content

    if not inputs:
        return None
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return digest[:16]
