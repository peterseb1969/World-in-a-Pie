"""NATS server configuration: authentication and per-service permissions.

NATS carries the platform's change events and the ingest work queue. It
used to run with no authentication, so any workload on the network could
read every event, forge events that reporting-sync applies, delete the
streams — and, through the ingest gateway, have documents written with the
platform's admin key. NATS now requires every client to authenticate.

Each component or app that declares a `nats_client` block becomes one NATS
user, named after the component, restricted to the subjects it declares:

- the deployer generates the user's password secret
  (`nats-password-<name>`);
- the component receives `NATS_USER` / `NATS_PASSWORD`, which
  `wip_auth.events.EventBus` passes to `nats.connect`;
- the NATS container receives every client's password as
  `NATS_PASSWORD_<NAME>`, and the rendered config references those
  variables (`password: $NATS_PASSWORD_<NAME>`) — nats-server resolves
  them from its environment, so the config file itself holds no secret
  and is written 0644 / rendered as a plain ConfigMap.

Apps get NATS access only through roles (`nats_client.roles`), which
`client_permissions` expands to fixed subject sets — an app manifest cannot
grant itself stream administration or another service's subjects:

- `ingest-producer` — publish ingest work items (`wip.ingest.<entity>.>`).
  ingest-gateway forwards each with the producer's own WIP API key from the
  message's X-API-Key header, so the target service applies the producer's
  grants. Reading the results stream is not part of the role: it carries
  every producer's outcomes.
- `event-subscriber` — a durable consumer on `WIP_EVENTS` named after the
  app. It sees every namespace's change events; grant it like an admin read.
- `replay-subscriber` — a plain subscription to `wip.replay.>`: replayed
  events arrive while the replay runs. No JetStream consumer rights — replay
  streams have per-session names, and a token wildcard broad enough to cover
  them would also cover `WIP_INGEST`, whose messages carry producers' keys.

Every user may also subscribe to its own reply inbox, `_INBOX_<name>.>`
(EventBus sets the matching inbox prefix): JetStream acks and pulled
messages arrive on reply inboxes, so a shared `_INBOX.>` grant would let
one service read another's.

The rendered config also moves two settings off their unsafe defaults:

- the JetStream store lives on NATS's storage volume (the default store
  is `/tmp/nats/jetstream` inside the container, which loses every stream
  on restart);
- the monitoring endpoint (no authentication of its own; `/connz` lists
  every client) listens on 127.0.0.1 only, and the healthcheck probes it
  from inside the container.

NATS is a storage component, which the per-workload config hash excludes;
the renderers stamp `nats_config_hash` on it instead, so a permission
change in a manifest restarts NATS rather than waiting for a restart that
may never come.
"""

from __future__ import annotations

import hashlib

from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component, NatsClientSpec

NATS_COMPONENT = "nats"
NATS_CLIENT_PORT = 4222
NATS_MONITOR_PORT = 8222

# Where the renderers put the config: install-dir-relative for compose/dev
# (bind-mounted read-only), container path everywhere.
NATS_CONFIG_RENDER_PATH = "config/nats/nats.conf"
NATS_CONFIG_CONTAINER_PATH = "/etc/nats/nats.conf"


def nats_component(
    deployment: Deployment, components: list[Component]
) -> Component | None:
    """The NATS component when it is active in this deployment."""
    for c in components:
        if c.metadata.name == NATS_COMPONENT and is_component_active(c, deployment):
            return c
    return None


def nats_clients(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> list[tuple[str, NatsClientSpec]]:
    """Active components and enabled apps that declare a `nats_client`,
    sorted by name; empty when NATS itself is inactive."""
    if nats_component(deployment, components) is None:
        return []
    enabled_apps = {a.name for a in deployment.spec.apps if a.enabled}
    owners: list[Component | App] = [
        *(c for c in components if is_component_active(c, deployment)),
        *(a for a in apps if a.metadata.name in enabled_apps),
    ]
    return sorted(
        (o.metadata.name, o.spec.nats_client)
        for o in owners
        if o.spec.nats_client is not None
    )


def password_secret(client: str) -> str:
    """The secret holding NATS user `client`'s password."""
    return f"nats-password-{client}"


def server_password_env(client: str) -> str:
    """The variable through which NATS receives `client`'s password."""
    return "NATS_PASSWORD_" + client.upper().replace("-", "_")


def inbox_prefix(client: str) -> str:
    """The reply-inbox prefix of NATS user `client` (see EventBus)."""
    return f"_INBOX_{client}"


def _event_subscriber(owner: str) -> list[str]:
    durable = f"WIP_EVENTS.{owner}"
    return [
        "$JS.API.STREAM.INFO.WIP_EVENTS",
        f"$JS.API.CONSUMER.INFO.{durable}",
        f"$JS.API.CONSUMER.CREATE.{durable}",
        f"$JS.API.CONSUMER.CREATE.{durable}.>",
        f"$JS.API.CONSUMER.DURABLE.CREATE.{durable}",
        f"$JS.API.CONSUMER.DELETE.{durable}",
        f"$JS.API.CONSUMER.MSG.NEXT.{durable}",
        f"$JS.ACK.{durable}.>",
    ]


_INGEST_PRODUCER = [
    "wip.ingest.terminologies.>",
    "wip.ingest.terms.>",
    "wip.ingest.templates.>",
    "wip.ingest.documents.>",
]


def client_permissions(
    name: str, spec: NatsClientSpec
) -> tuple[list[str], list[str], list[str]]:
    """(publish allow, publish deny, subscribe allow) of NATS user `name`,
    with roles expanded and the user's own reply inbox added."""
    publish = list(spec.publish)
    subscribe = list(spec.subscribe)
    for role in spec.roles:
        if role == "ingest-producer":
            publish += _INGEST_PRODUCER
        elif role == "event-subscriber":
            publish += _event_subscriber(name)
        elif role == "replay-subscriber":
            subscribe.append("wip.replay.>")
    return publish, list(spec.publish_deny), [*subscribe, f"{inbox_prefix(name)}.>"]


def render_nats_conf(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> str | None:
    """The nats-server config for this deployment, or None when NATS is
    inactive. Deterministic: clients and subjects keep a fixed order."""
    nats = nats_component(deployment, components)
    if nats is None:
        return None
    store_dir = nats.spec.storage[0].mount_path if nats.spec.storage else "/data"

    lines = [
        "# Generated by wip-deploy — do not edit. Holds no secrets: passwords",
        "# are resolved from the container environment ($NATS_PASSWORD_*).",
        f"listen: 0.0.0.0:{NATS_CLIENT_PORT}",
        f"http: 127.0.0.1:{NATS_MONITOR_PORT}",
        f"jetstream {{ store_dir: {_quote(store_dir)} }}",
        "authorization {",
        "  users = [",
    ]
    for name, spec in nats_clients(deployment, components, apps):
        allow, deny, subscribe = client_permissions(name, spec)
        publish = f"allow: {_list(allow)}"
        if deny:
            publish += f", deny: {_list(deny)}"
        lines += [
            f"    {{ user: {_quote(name)}, password: ${server_password_env(name)},",
            "      permissions: {",
            f"        publish: {{ {publish} }},",
            f"        subscribe: {{ allow: {_list(subscribe)} }}",
            "      } }",
        ]
    lines += ["  ]", "}"]
    return "\n".join(lines) + "\n"


def nats_config_hash(conf: str) -> str:
    """Short digest of the rendered config, stamped on the NATS workload."""
    return hashlib.sha256(conf.encode()).hexdigest()[:16]


def _quote(value: str) -> str:
    # Quoted strings are literal to nats-server: "$JS.API..." is a subject,
    # not a variable reference. NatsClientSpec rejects embedded quotes.
    return f'"{value}"'


def _list(values: list[str]) -> str:
    return "[" + ", ".join(_quote(v) for v in values) + "]"
