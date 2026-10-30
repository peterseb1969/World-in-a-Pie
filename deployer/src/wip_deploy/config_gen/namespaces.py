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

from wip_deploy.spec import App, Deployment
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


def namespace_reservations_json(deployment: Deployment, apps: list[App]) -> str:
    """The WIP_NAMESPACE_RESERVATIONS_FILE document. Deterministic: sorted by
    app, declaration order within an app."""
    reservations = [
        {
            "prefix": ns.name,
            "app": app.metadata.name,
            "settings": ns.settings.model_dump(exclude_none=True),
        }
        for app in _enabled_apps(deployment, apps)
        for ns in app.spec.namespaces
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
    for app in _enabled_apps(deployment, apps):
        for ns in app.spec.namespaces:
            owner = owners.setdefault(ns.name, app.metadata.name)
            if owner != app.metadata.name:
                errors.append(
                    f"namespace {ns.name!r} is reserved by both app {owner!r} and "
                    f"app {app.metadata.name!r}; a namespace has one owning app"
                )
            settings = ns.settings.model_dump(exclude_none=True)
            for entity_type, prefix in sorted(_prefixed_ids(settings).items()):
                where = f"{ns.name}.{entity_type} (app {app.metadata.name!r})"
                if prefix in prefixes:
                    errors.append(
                        f"ID prefix {prefix!r} is reserved twice: {prefixes[prefix]} "
                        f"and {where}; each prefix names one ID sequence on the install"
                    )
                else:
                    prefixes[prefix] = where
    return errors
