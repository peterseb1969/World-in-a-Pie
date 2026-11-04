"""The attach policy: what an app attachment may not proxy to or take.

The Registry holds app attachments (an app put under the umbrella through
the API) and refuses, at attach time, an upstream that is one of this
install's own hosts, and a name one of the install's own apps routes under.
It knows neither, so the deployer renders them into a file beside the
namespace reservations (WIP_ATTACH_POLICY_FILE):

- `blocked_hosts`: every name at which a rendered container or service is
  reachable on the install's network: compose's service key and container
  name (`<name>`, `wip-<name>`), k8s's short and fully qualified service
  names, the public Caddy, the install's own public hostname (an upstream
  pointing back at it would loop), and on k8s the API server's names.
- `reserved_names`: the last segment of every enabled app's route prefix
  (`/apps/kb` → `kb`).

The Registry compares resolved addresses, so a blocked host's IP or alias is
refused too. The attach-time check is advisory: the router's app edge checks
again where it resolves the upstream.

Deterministic for a fixed spec: sorted, no timestamps.
"""

from __future__ import annotations

import json

from wip_deploy.spec import App, Component, Deployment
from wip_deploy.spec.activation import is_component_active

ATTACH_POLICY_FILENAME = "attach-policy.json"
ATTACH_POLICY_CONTAINER_PATH = f"/etc/wip/{ATTACH_POLICY_FILENAME}"
ATTACH_POLICY_RENDER_PATH = f"config/auth/{ATTACH_POLICY_FILENAME}"
ATTACH_POLICY_FILE_ENV = "WIP_ATTACH_POLICY_FILE"


def _hosts(name: str, deployment: Deployment) -> set[str]:
    if deployment.spec.target == "k8s" and deployment.spec.platform.k8s is not None:
        ns = deployment.spec.platform.k8s.namespace
        return {f"wip-{name}", f"wip-{name}.{ns}", f"wip-{name}.{ns}.svc", f"wip-{name}.{ns}.svc.cluster.local"}
    return {name, f"wip-{name}"}


def app_route_names(deployment: Deployment, apps: list[App]) -> list[str]:
    """The /apps/<name> segments the install's enabled apps route under."""
    enabled = {a.name for a in deployment.spec.apps if a.enabled}
    names: set[str] = set()
    for app in apps:
        if app.metadata.name not in enabled:
            continue
        prefix = app.app_metadata.route_prefix.rstrip("/")
        if prefix.startswith("/apps/"):
            names.add(prefix.removeprefix("/apps/").split("/", 1)[0])
    return sorted(names)


def attach_policy_json(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> str:
    """The WIP_ATTACH_POLICY_FILE document."""
    enabled = {a.name for a in deployment.spec.apps if a.enabled}
    hosts: set[str] = set()
    for c in components:
        if is_component_active(c, deployment):
            hosts |= _hosts(c.metadata.name, deployment)
    for a in apps:
        if a.metadata.name in enabled:
            hosts |= _hosts(a.metadata.name, deployment)
    if deployment.spec.target != "k8s":
        # The public Caddy, rendered by the compose and dev renderers rather
        # than from a component manifest.
        hosts |= {"caddy", "wip-caddy"}
    else:
        hosts |= {
            "kubernetes",
            "kubernetes.default",
            "kubernetes.default.svc",
            "kubernetes.default.svc.cluster.local",
        }
    # The public hostname resolves to the host, not to a container, so it
    # needs its own entry: /apps/x -> https://<host>/ would be a proxy loop.
    hosts.add(deployment.spec.network.hostname.lower())
    return json.dumps(
        {"blocked_hosts": sorted(hosts), "reserved_names": app_route_names(deployment, apps)},
        indent=2,
    ) + "\n"
