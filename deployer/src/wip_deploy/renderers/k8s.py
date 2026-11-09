"""K8s renderer — produces a flat directory of YAML manifests.

Turns the declarative spec + component manifests + resolved env +
secrets into a set of Kubernetes resources that `kubectl apply -f <dir>`
can directly consume.

Output tree:
    namespace.yaml
    secrets.yaml
    configmaps.yaml
    infrastructure/<name>.yaml    (StatefulSets for storage-bearing components)
    services/<name>.yaml          (Deployments for stateless services)
    ingress.yaml

Design decisions:
  - Flat directory, not kustomize overlays. Module activation happens at
    render time — inactive components are simply not emitted. Overlays
    are a follow-up for GitOps workflows.
  - Secret values are rendered inline (stringData) from the file backend.
    A native k8s-secret backend is a follow-up.
  - NetworkPolicies isolate the namespace as a whole (default deny, same-
    namespace traffic, the ingress controller, DNS and HTTPS egress). No
    per-component rules: inside the namespace, datastore access is limited
    by per-component credentials (MongoDB and NATS users, PostgreSQL roles).
  - Rendering only. Applying the tree and waiting for rollout is apply.py's
    job (`apply_k8s`).
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

import yaml

from wip_deploy.config_gen import (
    ResolvedEnv,
    SecretRef,
    generate_ingress_config,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.config_gen.api_keys import (
    API_KEYS_CONTAINER_DIR,
    FEDERATED_GROUPS_FILENAME,
    KEY_FILE_CONTAINER_DIR,
    app_edge_active,
    declares_api_keys_file,
    generate_api_keys_json,
    mounts_auth_config_dir,
)
from wip_deploy.config_gen.attach_policy import ATTACH_POLICY_FILENAME, attach_policy_json
from wip_deploy.config_gen.caddy import (
    PUBLIC_CADDY_IMAGE,
    PUBLIC_CADDY_K8S_PORT,
    generate_caddy_config,
)
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY, config_hash
from wip_deploy.config_gen.env import Literal
from wip_deploy.config_gen.garage import garage_component, render_garage_toml
from wip_deploy.config_gen.images import image_ref as _image_ref
from wip_deploy.config_gen.ingress import IngressConfig
from wip_deploy.config_gen.mongodb import (
    ENTRYPOINT_CONTAINER_PATH as MONGO_ENTRYPOINT_CONTAINER_PATH,
)
from wip_deploy.config_gen.mongodb import (
    ENTRYPOINT_RENDER_PATH as MONGO_ENTRYPOINT_RENDER_PATH,  # noqa: F401
)
from wip_deploy.config_gen.mongodb import (
    MONGODB_COMPONENT,
    mongodb_config_hash,
    render_mongodb_entrypoint,
)
from wip_deploy.config_gen.namespaces import (
    RESERVATIONS_FILENAME,
    namespace_reservations_json,
)
from wip_deploy.config_gen.nats import (
    NATS_COMPONENT,
    NATS_CONFIG_CONTAINER_PATH,
    nats_config_hash,
    render_nats_conf,
)
from wip_deploy.config_gen.router import generate_router_config
from wip_deploy.config_gen.spec_context import federated_groups_policy
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.compose_dex import (
    DEX_COMPONENT,
    dex_config_hash,
    render_dex_conf,
)
from wip_deploy.renderers.router_caddy import render_router_caddyfile
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────
# Main entry point
# ────────────────────────────────────────────────────────────────────

_LABELS_PART_OF = "app.kubernetes.io/part-of"
_LABELS_NAME = "app.kubernetes.io/name"
_LABELS_MANAGED = "app.kubernetes.io/managed-by"


def render_k8s(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    secrets: ResolvedSecrets,
) -> FileTree:
    """Render the complete k8s deployment to an in-memory FileTree."""
    if deployment.spec.target != "k8s":
        raise ValueError(
            f"render_k8s requires target=k8s, got {deployment.spec.target!r}"
        )

    k8s = deployment.spec.platform.k8s
    if k8s is None:
        raise ValueError("k8s target requires platform.k8s")

    ns = k8s.namespace
    ctx = make_spec_context(deployment, components, apps)
    resolved_env = resolve_all_env(
        deployment, components, apps, ctx,
        collected_secrets=set(secrets.values.keys()),
    )

    tree = FileTree()

    # Namespace
    tree.add("namespace.yaml", _render_namespace(ns))

    # Secrets
    tree.add("secrets.yaml", _render_secrets(ns, secrets), mode=0o600)

    # The key file (the spec's keys, each app's key and the canary) in a
    # Secret of its own, mounted as a directory into the manifests that read
    # it (the Registry); the auth config (federated groups, reservations) in
    # another, mounted where those are read. No other pod holds a plaintext.
    tree.add(
        "api-keys-secret.yaml",
        _render_api_keys_secret(
            ns,
            generate_api_keys_json(deployment, secrets, apps),
            federated_groups_policy(deployment),
            namespace_reservations_json(deployment, apps),
            attach_policy_json(deployment, components, apps),
        ),
        mode=0o600,
    )

    # ConfigMaps
    configmaps = _render_configmaps(deployment, components, apps, resolved_env, ns, secrets)
    tree.add("configmaps.yaml", configmaps)

    # Per-component resources
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}
    active: list[Component | App] = []
    for c in components:
        if is_component_active(c, deployment):
            active.append(c)
    for a in apps:
        if a.metadata.name in enabled_app_names:
            active.append(a)

    # The router reads its Caddyfile through a subPath mount, which a running
    # pod never sees change; it is part of the router's config hash.
    router_files: dict[str, str] = {}
    if any(o.metadata.name == "router" for o in active):
        router_files["Caddyfile"] = render_router_caddyfile(
            generate_router_config(deployment, components, apps)
        )

    for owner in active:
        name = owner.metadata.name
        env = resolved_env.get(name)
        if env is None:
            continue
        workload_hash = config_hash(
            owner, deployment, env, secrets,
            mounted_files=router_files if name == "router" else None,
        )
        if name == NATS_COMPONENT:
            # NATS is storage-bearing (no config_hash), but a changed config
            # must still roll it: nothing else restarts the StatefulSet.
            nats_conf = render_nats_conf(deployment, components, apps)
            workload_hash = nats_config_hash(nats_conf) if nats_conf else None
        if name == MONGODB_COMPONENT:
            # Same for MongoDB: its start script carries the user set.
            script = render_mongodb_entrypoint(deployment, components, apps)
            workload_hash = mongodb_config_hash(script) if script else None
        if name == DEX_COMPONENT:
            # And Dex: it reads its user and client set only at start.
            dex_conf = render_dex_conf(deployment, components, apps, secrets)
            workload_hash = dex_config_hash(dex_conf) if dex_conf else None
        content = _render_component(owner, deployment, env, ns, workload_hash)
        # Storage-bearing → infrastructure/, otherwise services/
        if owner.spec.storage:
            tree.add(f"infrastructure/{name}.yaml", content)
        else:
            tree.add(f"services/{name}.yaml", content)

    # Ingress
    # The front door: wip-caddy (the same Caddyfile compose runs) behind one
    # catch-all Ingress, whatever the controller.
    tree.add(
        f"services/{PUBLIC_CADDY_NAME}.yaml",
        _render_public_caddy(
            render_caddyfile(generate_caddy_config(deployment, components, apps)),
            deployment, ns,
        ),
    )
    tree.add("ingress.yaml", _render_ingress(generate_ingress_config(deployment), ns))

    # NetworkPolicies (CASE-238 MVP: namespace-wide isolation;
    # per-component egress rules are a follow-up).
    tree.add(
        "network-policies.yaml",
        _render_network_policies(
            ns,
            app_edge=app_edge_active(deployment),
            controller_ns=k8s.controller_namespace(),
            cluster_ranges=k8s.cluster_ranges.all(),
        ),
    )

    return tree


# ────────────────────────────────────────────────────────────────────
# Namespace
# ────────────────────────────────────────────────────────────────────


def _render_namespace(ns: str) -> str:
    return _dump({
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": ns,
            "labels": {_LABELS_PART_OF: "wip"},
        },
    })


# ────────────────────────────────────────────────────────────────────
# NetworkPolicies (CASE-238)
#
# MVP scope: namespace-wide isolation. Five policies:
#   - deny-all-default          → block all traffic by default
#   - allow-same-namespace      → pod ↔ pod inside ns works
#   - allow-ingress-controller  → the ingress controller reaches wip-caddy only
#   - allow-egress-dns          → kube-dns lookups for cluster DNS
#   - allow-egress-https        → outbound HTTPS (Anthropic, etc.)
#
# What this gets us:
#   - A compromised wip pod cannot pivot to other namespaces
#     (mongodb, teslamate, pihole, …) — the headline security win.
#   - Pods in the namespace can still talk to each other, can do
#     DNS lookups, can reach the internet over HTTPS. So nothing
#     functional breaks vs. the no-policy baseline.
#
# What this does NOT get us (follow-up case):
#   - Per-component egress rules (e.g. registry can ONLY reach
#     mongodb, not other components). The legacy
#     `k8s/network-policies.yaml` had ~14 such per-component
#     policies. Restoring them needs each component manifest to
#     declare its egress dependencies, which is a spec-level change
#     plus a renderer that walks the call graph. Out of MVP scope;
#     filed as a follow-up.
# ────────────────────────────────────────────────────────────────────


APP_EDGE_COMPONENT = "app-edge"


def _render_network_policies(
    ns: str, *, app_edge: bool = False, controller_ns: str = "ingress",
    cluster_ranges: list[str] | None = None,
) -> str:
    """Render the namespace-isolation NetworkPolicy bundle.

    CASE-238 MVP — restores the cross-namespace isolation guarantee
    that the legacy `k8s/network-policies.yaml` provided. Per-component
    egress rules are a follow-up case (see file header).

    The app edge proxies users' traffic to upstreams an app-admin chose, so
    its egress should not reach WIP's own pods or the cluster's control
    plane. Policies are additive, so the edge is left out of the bundle's
    broad allows (every pod to every pod, 443 to anywhere) and gets its own:
    the Registry and the auth gateway in this namespace, DNS, pods in other
    namespaces, and any address by ipBlock (attached apps may run anywhere).

    The cluster's own ranges (platform.k8s.cluster_ranges, `--cluster-cidr`)
    are excepted from that ipBlock allow: pod and Service ranges, and the
    node addresses where the API server's real endpoint and the kubelets
    listen (ports such as 6443/16443 and 10250). The node ranges also go
    into the attach policy as `blocked_cidrs`, which the Registry and the
    edge check. Without ranges the allow reaches all of those (the CLI warns),
    and the edge's resolve-time blocklist covers only the install's names,
    the `kubernetes…` names and their ClusterIP. Pods and Services in other
    namespaces stay reachable through the namespace rule: attaching is full
    trust.
    """
    labels = {_LABELS_PART_OF: "wip"}
    # Every pod but the edge, for the broad allows.
    not_edge: dict[str, Any] = (
        {"matchExpressions": [{
            "key": _LABELS_NAME, "operator": "NotIn", "values": [APP_EDGE_COMPONENT],
        }]}
        if app_edge else {}
    )

    deny_all = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "deny-all-default",
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
        },
    }

    allow_same_ns = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "allow-same-namespace",
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "podSelector": not_edge,
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"from": [{"podSelector": {}}]}],
            "egress": [{"to": [{"podSelector": {}}]}],
        },
    }

    # The ingress controller reaches wip-caddy, the front door, and nothing
    # else: every route goes through wip-caddy's forward_auth and header
    # strip. Its namespace comes from platform.k8s (ingress for MicroK8s's
    # nginx addon, kube-system for k3s's Traefik). The
    # kubernetes.io/metadata.name label is auto-added by k8s 1.21+ on
    # every namespace.
    allow_ingress_controller = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "allow-ingress-controller",
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "podSelector": {"matchLabels": {_LABELS_NAME: PUBLIC_CADDY_NAME}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": controller_ns,
                                },
                            },
                        },
                    ],
                },
            ],
        },
    }

    # DNS — UDP/TCP 53 to anywhere. Required for cluster service DNS
    # (*.svc.cluster.local) AND external lookups.
    allow_egress_dns = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "allow-egress-dns",
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "ports": [
                        {"port": 53, "protocol": "UDP"},
                        {"port": 53, "protocol": "TCP"},
                    ],
                },
            ],
        },
    }

    # HTTPS egress — Anthropic API for askBar/NL queries (RC, dnd-mcp),
    # external image registries, OIDC discovery for federated providers.
    # Open to anywhere on 443/TCP, matching the legacy posture.
    allow_egress_https = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "allow-egress-https",
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "podSelector": not_edge,
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "ports": [
                        {"port": 443, "protocol": "TCP"},
                    ],
                },
            ],
        },
    }

    parts = [
        _dump(deny_all),
        _dump(allow_same_ns),
        _dump(allow_ingress_controller),
        _dump(allow_egress_dns),
        _dump(allow_egress_https),
    ]
    if app_edge:
        parts.append(_dump(_app_edge_egress_policy(ns, labels, cluster_ranges or [])))
    return "---\n".join(parts)


def _anywhere_except(cidr: str, ranges: list[str]) -> dict[str, Any]:
    """An ipBlock for one IP family's "anywhere", excepting the cluster's own
    ranges of that family (platform.k8s.cluster_ranges)."""
    import ipaddress

    family = ipaddress.ip_network(cidr).version
    excepted = [r for r in ranges if ipaddress.ip_network(r).version == family]
    block: dict[str, Any] = {"cidr": cidr}
    if excepted:
        block["except"] = excepted
    return {"ipBlock": block}


def _app_edge_egress_policy(
    ns: str, labels: dict[str, str], cluster_ranges: list[str] | None = None,
) -> dict[str, Any]:
    def pods(name: str) -> dict[str, Any]:
        return {"podSelector": {"matchLabels": {_LABELS_NAME: name}}}

    ranges = cluster_ranges or []

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "app-edge-egress", "namespace": ns, "labels": labels},
        "spec": {
            "podSelector": {"matchLabels": {_LABELS_NAME: APP_EDGE_COMPONENT}},
            "policyTypes": ["Ingress", "Egress"],
            # Its only caller is wip-caddy, which hands it the /apps paths no
            # deployer app claims.
            "ingress": [{"from": [pods(PUBLIC_CADDY_NAME)]}],
            "egress": [
                # Its records, and the login check.
                {"to": [pods("registry"), pods("auth-gateway")]},
                # Attached apps running in other namespaces of the cluster.
                {"to": [{"namespaceSelector": {"matchExpressions": [{
                    "key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": [ns],
                }]}}]},
                # Attached apps outside the cluster, but not the cluster's own
                # pod, Service and node ranges: without the except this rule
                # reaches the API server's endpoint and the kubelets on the
                # node addresses, and (plugin-dependent) pod and Service IPs.
                {"to": [_anywhere_except("0.0.0.0/0", ranges), _anywhere_except("::/0", ranges)]},
            ],
        },
    }


# ────────────────────────────────────────────────────────────────────
# Secrets
# ────────────────────────────────────────────────────────────────────


KEY_FILE_SECRET = "wip-key-file"
# The auth config Secret (federated groups, reservations). Renamed from
# "wip-api-keys-config", which also carried the key file: apply cannot drop
# a key from a Secret's stringData, so the old Secret is left out of the
# render and the apply's prune deletes it, plaintexts included.
AUTH_CONFIG_SECRET = "wip-auth-config"


def _render_api_keys_secret(
    ns: str, api_keys_json: str, federated_groups: str, reservations: str, attach_policy: str
) -> str:
    """Two Secrets, each mounted as a directory so a running pod sees it
    change (wip-secrets carries flat name→value pairs, not files): the key
    file alone, for the manifests that read it, and the auth config (the
    federated group policy and the namespace reservations)."""
    key_file = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": KEY_FILE_SECRET,
            "namespace": ns,
            "labels": {_LABELS_PART_OF: "wip"},
        },
        "type": "Opaque",
        "stringData": {"api-keys.json": api_keys_json},
    }
    auth_config = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": AUTH_CONFIG_SECRET,
            "namespace": ns,
            "labels": {_LABELS_PART_OF: "wip"},
        },
        "type": "Opaque",
        "stringData": {
            FEDERATED_GROUPS_FILENAME: federated_groups,
            RESERVATIONS_FILENAME: reservations,
            ATTACH_POLICY_FILENAME: attach_policy,
        },
    }
    return _dump_multi([key_file, auth_config])


def _render_secrets(ns: str, secrets: ResolvedSecrets) -> str:
    """Render all secrets into a single k8s Secret (stringData).

    stringData is the plaintext form — kubectl base64-encodes it
    automatically. Simpler than pre-encoding and using `data:`.
    """
    string_data: dict[str, str] = {}
    for name in sorted(secrets.values.keys()):
        string_data[name] = secrets.values[name]

    return _dump({
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "wip-secrets",
            "namespace": ns,
            "labels": {_LABELS_PART_OF: "wip"},
        },
        "type": "Opaque",
        "stringData": string_data,
    })


# ────────────────────────────────────────────────────────────────────
# ConfigMaps
# ────────────────────────────────────────────────────────────────────


def _render_configmaps(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    resolved_env: dict[str, ResolvedEnv],
    ns: str,
    secrets: ResolvedSecrets,
) -> str:
    """Render the Dex config ConfigMap, if Dex is active, and the other
    per-component ConfigMaps. There is no shared env ConfigMap: every
    variable is set inline on its container."""
    docs: list[dict[str, Any]] = []

    # Dex config
    dex_yaml = render_dex_conf(deployment, components, apps, secrets)
    if dex_yaml is not None:
        docs.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "wip-dex-config",
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
            },
            "data": {"config.yaml": dex_yaml},
        })

    # Garage config — carries secrets; lives in a ConfigMap like dex's
    # (the cluster boundary is the namespace, matching the compose
    # posture where the file sits 0600 in the install dir).
    if garage_component(deployment, components) is not None:
        docs.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "wip-garage-config",
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
            },
            "data": {"garage.toml": render_garage_toml(secrets)},
        })

    # MongoDB start script — no secrets (passwords are env references).
    mongo_entrypoint = render_mongodb_entrypoint(deployment, components, apps)
    if mongo_entrypoint is not None:
        docs.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "wip-mongodb-entrypoint",
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
            },
            "data": {PurePosixPath(MONGO_ENTRYPOINT_CONTAINER_PATH).name: mongo_entrypoint},
        })

    # NATS config — no secrets (passwords are env references resolved by
    # nats-server), so a plain ConfigMap.
    nats_conf = render_nats_conf(deployment, components, apps)
    if nats_conf is not None:
        docs.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "wip-nats-config",
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
            },
            "data": {PurePosixPath(NATS_CONFIG_CONTAINER_PATH).name: nats_conf},
        })

    # wip-router Caddyfile
    router_active = any(
        c.metadata.name == "router" and is_component_active(c, deployment)
        for c in components
    )
    if router_active:
        router_cfg = generate_router_config(deployment, components, apps)
        docs.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "wip-router-config",
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
            },
            "data": {"Caddyfile": render_router_caddyfile(router_cfg)},
        })

    return _dump_multi(docs)


# ────────────────────────────────────────────────────────────────────
# Per-component: Service + Deployment/StatefulSet
# ────────────────────────────────────────────────────────────────────


def _render_component(
    owner: Component | App,
    deployment: Deployment,
    env: ResolvedEnv,
    ns: str,
    workload_hash: str | None = None,
) -> str:
    name = owner.metadata.name
    svc_name = f"wip-{name}"
    labels = {
        _LABELS_NAME: name,
        _LABELS_PART_OF: "wip",
    }

    docs: list[dict[str, Any]] = []

    # PVCs for storage-bearing components
    for storage in owner.spec.storage:
        k8s_plat = deployment.spec.platform.k8s
        sc = k8s_plat.storage_class if k8s_plat else "rook-ceph-block"
        docs.append({
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": f"{svc_name}-{storage.name}",
                "namespace": ns,
                "labels": labels,
            },
            "spec": {
                "storageClassName": sc,
                "accessModes": [storage.access_mode],
                "resources": {"requests": {"storage": storage.size}},
            },
        })

    # Service
    if owner.spec.ports:
        # k8s requires port `name` on multi-port Services (and recommends
        # it on single-port too). Use the manifest's declared port name.
        svc_ports = [
            {"name": p.name, "port": p.container_port, "targetPort": p.container_port, "protocol": p.protocol}
            for p in owner.spec.ports
        ]
        docs.append({
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": svc_name,
                "namespace": ns,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {_LABELS_NAME: name},
                "ports": svc_ports,
            },
        })

    # Deployment or StatefulSet
    has_storage = bool(owner.spec.storage)
    workload_kind = "StatefulSet" if has_storage else "Deployment"

    container = _container_spec(owner, deployment, env, ns)

    # Volume mounts + volumes
    volume_mounts: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    for storage in owner.spec.storage:
        pvc_name = f"{svc_name}-{storage.name}"
        volume_mounts.append({
            "name": storage.name,
            "mountPath": storage.mount_path,
        })
        volumes.append({
            "name": storage.name,
            "persistentVolumeClaim": {"claimName": pvc_name},
        })

    # The auth config (federated groups, reservations) and the key file,
    # each mounted for exactly the manifests that declare its env.
    if mounts_auth_config_dir(owner):
        volume_mounts.append({
            "name": "api-keys-config",
            "mountPath": API_KEYS_CONTAINER_DIR,
            "readOnly": True,
        })
        volumes.append({
            "name": "api-keys-config",
            "secret": {"secretName": AUTH_CONFIG_SECRET},
        })
    if declares_api_keys_file(owner):
        volume_mounts.append({
            "name": "key-file",
            "mountPath": KEY_FILE_CONTAINER_DIR,
            "readOnly": True,
        })
        volumes.append({
            "name": "key-file",
            "secret": {"secretName": KEY_FILE_SECRET},
        })

    # Config-file mounts for known components. Generalizing via
    # `config_files` on the Component spec is a tracked follow-up.
    if name == "dex":
        volume_mounts.append({
            "name": "config",
            "mountPath": "/etc/dex",
            "readOnly": True,
        })
        volumes.append({
            "name": "config",
            "configMap": {"name": "wip-dex-config"},
        })
    elif name == "garage":
        volume_mounts.append({
            "name": "config",
            "mountPath": "/etc/garage.toml",
            "subPath": "garage.toml",
            "readOnly": True,
        })
        volumes.append({
            "name": "config",
            "configMap": {"name": "wip-garage-config"},
        })
    elif name == MONGODB_COMPONENT:
        volume_mounts.append({
            "name": "config",
            "mountPath": str(PurePosixPath(MONGO_ENTRYPOINT_CONTAINER_PATH).parent),
            "readOnly": True,
        })
        volumes.append({
            "name": "config",
            "configMap": {"name": "wip-mongodb-entrypoint"},
        })
    elif name == NATS_COMPONENT:
        volume_mounts.append({
            "name": "config",
            "mountPath": str(PurePosixPath(NATS_CONFIG_CONTAINER_PATH).parent),
            "readOnly": True,
        })
        volumes.append({
            "name": "config",
            "configMap": {"name": "wip-nats-config"},
        })
    elif name == "router":
        # Caddy reads /etc/caddy/Caddyfile. Mount just that key via
        # subPath so we don't replace the directory.
        volume_mounts.append({
            "name": "config",
            "mountPath": "/etc/caddy/Caddyfile",
            "subPath": "Caddyfile",
            "readOnly": True,
        })
        volumes.append({
            "name": "config",
            "configMap": {"name": "wip-router-config"},
        })

    if volume_mounts:
        container["volumeMounts"] = volume_mounts

    pod_spec: dict[str, Any] = {"containers": [container]}
    if volumes:
        pod_spec["volumes"] = volumes

    # Lower ndots so the FQDNs in the rendered env are tried as written instead
    # of being walked through the three cluster search domains first — see
    # K8sPlatform.pod_dns_ndots for why the kubelet default of 5 quadruples the
    # query count for every inter-service call.
    k8s_plat = deployment.spec.platform.k8s
    ndots = k8s_plat.pod_dns_ndots if k8s_plat else 1
    if ndots is not None:
        pod_spec["dnsConfig"] = {
            "options": [{"name": "ndots", "value": str(ndots)}],
        }

    # Dex needs fsGroup for sqlite
    if name == "dex":
        pod_spec["securityContext"] = {"fsGroup": 1001}

    workload: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": workload_kind,
        "metadata": {
            "name": svc_name,
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {_LABELS_NAME: name}},
            "template": {
                "metadata": {"labels": labels},
                "spec": pod_spec,
            },
        },
    }

    # StatefulSet needs serviceName
    if workload_kind == "StatefulSet":
        workload["spec"]["serviceName"] = svc_name

    # A changed secret or key file must roll the pods that read it at start;
    # the hash makes that content change a template change (config_hash).
    # Only NATS, MongoDB and Dex reach here with a StatefulSet (their config hash).
    if workload_hash is not None:
        workload["spec"]["template"]["metadata"]["annotations"] = {
            CONFIG_HASH_KEY: workload_hash,
        }

    docs.append(workload)
    return _dump_multi(docs)


def _container_spec(
    owner: Component | App,
    deployment: Deployment,
    env: ResolvedEnv,
    ns: str,
) -> dict[str, Any]:
    name = owner.metadata.name
    container: dict[str, Any] = {
        "name": name,
        "image": _image_ref(owner, deployment),
    }

    # WIP-owned images (short name → served from the deployment registry;
    # backend services AND apps built in their own repos alike) get same-tag
    # re-pushes routinely, and the k8s default `IfNotPresent` causes silent
    # stale-digest rollouts — an app rolled to a reused tag keeps the cached
    # image. `Always` does an HTTP HEAD per pod start; cost is negligible
    # against a LAN registry. Fully-qualified external images (mongo, dex,
    # caddy — name contains `/`) carry upstream version pins and stay on the
    # k8s default. Using build_context here would miss apps (built in their
    # own repos, build_context=None) despite them being WIP images.
    # `spec.images.pull_policy: always` escalates to Always for every image.
    wip_owned = "/" not in owner.spec.image.name
    if deployment.spec.images.pull_policy == "always" or wip_owned:
        container["imagePullPolicy"] = "Always"

    if owner.spec.ports:
        container["ports"] = [
            {"containerPort": p.container_port, "protocol": p.protocol}
            for p in owner.spec.ports
        ]

    # Command
    cmd = _command_for(owner)
    if cmd is not None:
        container["command"] = cmd

    # Env: every variable as an explicit env entry (secrets from secretKeyRef,
    # literals inline) — nothing comes from a shared ConfigMap, so the pod's
    # config hash and `status --diff` see every value.
    env_entries: list[dict[str, Any]] = []
    for k, v in sorted(env.merged().items()):
        if isinstance(v, SecretRef):
            env_entries.append({
                "name": k,
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "wip-secrets",
                        "key": v.name,
                    },
                },
            })
        elif isinstance(v, Literal):
            env_entries.append({"name": k, "value": v.value})

    if env_entries:
        container["env"] = env_entries

    container.update(_probes(owner))

    # Resources
    res = owner.spec.resources
    if res is not None:
        resources: dict[str, dict[str, str]] = {}
        requests: dict[str, str] = {}
        limits: dict[str, str] = {}
        if res.cpu_request:
            requests["cpu"] = res.cpu_request
        if res.memory_request:
            requests["memory"] = res.memory_request
        if res.cpu_limit:
            limits["cpu"] = res.cpu_limit
        if res.memory_limit:
            limits["memory"] = res.memory_limit
        if requests:
            resources["requests"] = requests
        if limits:
            resources["limits"] = limits
        if resources:
            container["resources"] = resources

    return container


def _command_for(owner: Component | App) -> list[str] | None:
    """Same as compose: explicit command, the Dex override, or None."""
    if owner.spec.command is not None:
        return list(owner.spec.command)
    if isinstance(owner, App):
        return None
    name = owner.metadata.name
    if name == "dex":
        return ["dex", "serve", "/etc/dex/config.yaml"]
    return None


# How long a WIP service may retry its dependencies before it serves HTTP:
# wip_auth's startup retry waits up to 120 s for MongoDB, PostgreSQL or the
# Registry inside the FastAPI lifespan, and uvicorn opens no socket until the
# lifespan finishes. The startup probe's budget must outlast that, with margin.
_STARTUP_RETRY_SECONDS = 150
_STARTUP_PERIOD_SECONDS = 5


def _probes(owner: Component | App) -> dict[str, Any]:
    """The container's probes, from the owner's healthcheck.

    - readinessProbe checks `endpoint`. It may fail while a dependency is
      down; k8s then only stops routing to the pod.
    - livenessProbe checks `liveness_endpoint`, else `endpoint`. A failure
      restarts the pod, which cannot bring a dependency back, so a service
      whose `endpoint` checks one sets a dependency-free `liveness_endpoint`.
    - startupProbe runs the liveness check every 5 s for `start_period +
      150` s. k8s runs neither of the others until it first succeeds, so a
      pod still retrying a dependency at startup is not killed mid-retry.

    `retries` is each probe's failureThreshold.
    """
    hc = owner.spec.healthcheck
    if hc is None:
        return {}

    def check(path: str | None) -> dict[str, Any]:
        # A fresh dict per probe: a shared one would be dumped as a YAML alias.
        if hc.command is not None:
            return {"exec": {"command": list(hc.command)}}
        return {"httpGet": {"path": path, "port": _resolve_check_port(owner, hc.port)}}

    liveness_path = hc.liveness_endpoint or hc.endpoint
    startup_budget = hc.start_period_seconds + _STARTUP_RETRY_SECONDS
    return {
        "readinessProbe": {
            **check(hc.endpoint),
            "initialDelaySeconds": hc.start_period_seconds,
            "periodSeconds": hc.interval_seconds,
            "timeoutSeconds": hc.timeout_seconds,
            "failureThreshold": hc.retries,
        },
        "livenessProbe": {
            **check(liveness_path),
            "initialDelaySeconds": hc.start_period_seconds + 5,
            "periodSeconds": max(hc.interval_seconds * 3, 30),
            "timeoutSeconds": hc.timeout_seconds,
            "failureThreshold": hc.retries,
        },
        "startupProbe": {
            **check(liveness_path),
            "periodSeconds": _STARTUP_PERIOD_SECONDS,
            "timeoutSeconds": hc.timeout_seconds,
            "failureThreshold": -(-startup_budget // _STARTUP_PERIOD_SECONDS),
        },
    }


def _resolve_check_port(owner: Component | App, explicit: str | None) -> int:
    by_name = {p.name: p for p in owner.spec.ports}
    if explicit is not None:
        return by_name[explicit].container_port
    if "http" in by_name:
        return by_name["http"].container_port
    if owner.spec.ports:
        return owner.spec.ports[0].container_port
    raise ValueError(f"{owner.metadata.name} has no ports for healthcheck")


# ────────────────────────────────────────────────────────────────────
# Ingress
# ────────────────────────────────────────────────────────────────────


def _render_ingress(cfg: IngressConfig, ns: str) -> str:
    """The install's one Ingress: the hostname, its TLS Secret, and every
    path to wip-caddy.

    Every route, forward_auth, the identity-header strip and the root
    redirect live in wip-caddy's Caddyfile (the same one compose runs), so
    this object carries nothing per route: only `networking.k8s.io/v1`
    fields every conforming controller reads, plus the flavour's few
    annotations (config_gen/ingress.controller_annotations).
    """
    return _dump({
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": "wip-ingress",
            "namespace": ns,
            "labels": {_LABELS_PART_OF: "wip"},
            "annotations": dict(cfg.annotations),
        },
        "spec": {
            "ingressClassName": cfg.ingress_class,
            "tls": [{"hosts": [cfg.hostname, *cfg.app_hosts], "secretName": cfg.tls_secret_name}],
            "rules": [
                {
                    "host": host,
                    "http": {"paths": [{
                        "path": "/",
                        "pathType": "Prefix",
                        "backend": {"service": {
                            "name": cfg.backend_service,
                            "port": {"number": cfg.backend_port},
                        }},
                    }]},
                }
                for host in (cfg.hostname, *cfg.app_hosts)
            ],
        },
    })


# The public front door's name: Service wip-caddy, pods labelled caddy.
PUBLIC_CADDY_NAME = "caddy"


def _render_public_caddy(caddyfile: str, deployment: Deployment, ns: str) -> str:
    """wip-caddy on k8s: its Caddyfile ConfigMap, Service and Deployment.

    The same Caddyfile generator as compose's wip-caddy, in plain-HTTP mode
    behind the ingress controller. The Caddyfile is mounted by subPath, which
    a running pod never sees change, so its hash is a template annotation:
    a changed route rolls the pod.
    """
    labels = {_LABELS_NAME: PUBLIC_CADDY_NAME, _LABELS_PART_OF: "wip"}
    svc = f"wip-{PUBLIC_CADDY_NAME}"
    port = PUBLIC_CADDY_K8S_PORT
    pod_spec: dict[str, Any] = {
        "containers": [{
            "name": PUBLIC_CADDY_NAME,
            "image": PUBLIC_CADDY_IMAGE,
            "command": ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
            "ports": [{"name": "http", "containerPort": port, "protocol": "TCP"}],
            "volumeMounts": [{
                "name": "config",
                "mountPath": "/etc/caddy/Caddyfile",
                "subPath": "Caddyfile",
                "readOnly": True,
            }],
            # Any answer is a live listener: bare `/` may redirect, and an
            # unrouted path is a 404 (caddy_common.write_unrouted_404).
            "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 5},
            "livenessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 10, "failureThreshold": 6},
        }],
        "volumes": [{"name": "config", "configMap": {"name": f"{svc}-config"}}],
    }
    k8s_plat = deployment.spec.platform.k8s
    ndots = k8s_plat.pod_dns_ndots if k8s_plat else 1
    if ndots is not None:
        pod_spec["dnsConfig"] = {"options": [{"name": "ndots", "value": str(ndots)}]}
    digest = hashlib.sha256(caddyfile.encode()).hexdigest()[:16]
    docs = [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"{svc}-config", "namespace": ns, "labels": labels},
            "data": {"Caddyfile": caddyfile},
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": svc, "namespace": ns, "labels": labels},
            "spec": {
                "type": "ClusterIP",
                "selector": {_LABELS_NAME: PUBLIC_CADDY_NAME},
                "ports": [{"name": "http", "port": port, "targetPort": port, "protocol": "TCP"}],
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": svc, "namespace": ns, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {_LABELS_NAME: PUBLIC_CADDY_NAME}},
                "template": {
                    "metadata": {"labels": labels, "annotations": {CONFIG_HASH_KEY: digest}},
                    "spec": pod_spec,
                },
            },
        },
    ]
    return _dump_multi(docs)


# ────────────────────────────────────────────────────────────────────
# YAML helpers
# ────────────────────────────────────────────────────────────────────


def _dump(obj: dict[str, Any]) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)


def _dump_multi(docs: list[dict[str, Any]]) -> str:
    return "---\n".join(_dump(d) for d in docs)
