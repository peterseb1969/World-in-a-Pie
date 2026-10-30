"""Namespace reservations: the namespaces enabled apps own on the install.

Each app declares its namespaces and their settings in its manifest
(`spec.namespaces`). The deployer renders them as one JSON document the
Registry reads through WIP_NAMESPACE_RESERVATIONS_FILE. A reservation lets
the app's own key create its namespace, with exactly the declared settings,
so an app triggers its bootstrap without a platform admin key; and it
supplies the settings any create of that namespace leaves out.

The file lives in the auth config directory beside the API keys
(config_gen/api_keys.py), mounted the same way, so a running Registry sees
a change without a restart. It is rendered on every install, empty when no
app declares a namespace, so the mount exists from the first render.

The deployer checks what it can see across the install: a namespace has one
owner, and two reservations never claim one ID prefix. The id_config rules
themselves stay in the Registry, which refuses an invalid reservation by
name at the first upsert.
"""

from __future__ import annotations

import json
from typing import Any

from wip_deploy.spec import App, AppRef, Deployment
from wip_deploy.spec.component import EnvSource, EnvVar

RESERVATIONS_FILENAME = "namespace-reservations.json"
RESERVATIONS_CONTAINER_PATH = f"/etc/wip/{RESERVATIONS_FILENAME}"
RESERVATIONS_RENDER_PATH = f"config/auth/{RESERVATIONS_FILENAME}"
RESERVATIONS_FILE_ENV = "WIP_NAMESPACE_RESERVATIONS_FILE"


def _enabled_apps(deployment: Deployment, apps: list[App]) -> list[App]:
    enabled = {a.name for a in deployment.spec.apps if a.enabled}
    return sorted(
        (a for a in apps if a.metadata.name in enabled), key=lambda a: a.metadata.name
    )


def owned_namespaces(app: App, ref: AppRef | None) -> list[tuple[str, dict[str, Any]]]:
    """(name, declared settings) of every namespace the app owns: its
    manifest's, then those the operator added on this install."""
    added = ref.namespaces if ref is not None else []
    return [
        *((n.name, n.settings.model_dump(exclude_none=True)) for n in app.spec.namespaces),
        *((n.name, n.settings.model_dump(exclude_none=True)) for n in added),
    ]


def _refs(deployment: Deployment) -> dict[str, AppRef]:
    return {r.name: r for r in deployment.spec.apps}


def namespace_reservations_json(deployment: Deployment, apps: list[App]) -> str:
    """The WIP_NAMESPACE_RESERVATIONS_FILE document. Deterministic: sorted by
    app, declaration order within an app."""
    refs = _refs(deployment)
    reservations = [
        {"prefix": name, "app": app.metadata.name, "settings": settings}
        for app in _enabled_apps(deployment, apps)
        for name, settings in owned_namespaces(app, refs.get(app.metadata.name))
    ]
    if not reservations:
        return ""
    return json.dumps({"reservations": reservations}, indent=2) + "\n"


def namespace_env(owner: Any) -> list[EnvVar]:
    """The env vars an app's namespace declarations set: each `env` to its
    namespace's name on this install."""
    return [
        EnvVar(name=ns.env, source=EnvSource(literal=ns.name))
        for ns in owner.spec.namespaces
        if ns.env
    ]


def _prefixed_ids(settings: dict[str, Any]) -> dict[str, str]:
    """entity type → ID prefix, for the id_config entries that mint prefixed IDs."""
    out: dict[str, str] = {}
    for entity_type, cfg in (settings.get("id_config") or {}).items():
        if isinstance(cfg, dict) and cfg.get("algorithm") == "prefixed" and cfg.get("prefix"):
            out[entity_type] = cfg["prefix"]
    return out


def reservation_conflicts(deployment: Deployment, apps: list[App]) -> list[str]:
    """What the install's reservations contradict each other on.

    - A namespace is owned by one app; two apps reserving it would both get
      to create it, with different settings.
    - An ID prefix names one ID sequence on the install, across namespaces
      and entity types; two reservations claiming it would mint the same IDs.
      (Prefixes held by namespaces that already exist are the Registry's to
      check.)
    """
    errors: list[str] = []
    owners: dict[str, str] = {}
    prefixes: dict[str, str] = {}
    refs = _refs(deployment)
    for app in _enabled_apps(deployment, apps):
        ref = refs.get(app.metadata.name)
        errors.extend(_addition_errors(app, ref))
        for name, settings in owned_namespaces(app, ref):
            owner = owners.setdefault(name, app.metadata.name)
            if owner != app.metadata.name:
                errors.append(
                    f"namespace {name!r} is reserved by both app {owner!r} and "
                    f"app {app.metadata.name!r}; a namespace has one owning app"
                )
            for entity_type, prefix in sorted(_prefixed_ids(settings).items()):
                where = f"{name}.{entity_type} (app {app.metadata.name!r})"
                if prefix in prefixes:
                    errors.append(
                        f"ID prefix {prefix!r} is reserved twice: {prefixes[prefix]} "
                        f"and {where}; each prefix names one ID sequence on the install"
                    )
                else:
                    prefixes[prefix] = where
    return errors


def _addition_errors(app: App, ref: AppRef | None) -> list[str]:
    """What an install's additions to an app contradict: a privileged app
    holds the master key (nothing to scope), and a namespace the manifest
    already declares needs no addition (two sources would disagree)."""
    if ref is None or not (ref.namespaces or ref.namespace_access):
        return []
    name = app.metadata.name
    if app.spec.privileged_key:
        return [
            f"app {name!r} holds the master key (privileged_key); namespaces "
            "added to it on this install would scope a key it does not use"
        ]
    declared = {n.name for n in app.spec.namespaces} | {
        a.namespace for a in app.spec.namespace_access
    }
    added = [n.name for n in ref.namespaces] + [a.namespace for a in ref.namespace_access]
    return [
        f"app {name!r}: namespace {ns!r} is already declared in its manifest; "
        "remove it from this install's additions"
        for ns in added
        if ns in declared
    ]
