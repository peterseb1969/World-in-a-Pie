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
  - NetworkPolicies are omitted — the hand-written v1 policies had
    cross-namespace bugs. Proper policies need a separate design pass.
  - No apply/wait logic — manual `kubectl apply -f` for now.
"""

from __future__ import annotations

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
    API_KEYS_CONTAINER_PATH,
    declares_api_keys_file,
    generate_api_keys_json,
)
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY, config_hash
from wip_deploy.config_gen.env import Literal
from wip_deploy.config_gen.garage import garage_component, render_garage_toml
from wip_deploy.config_gen.images import image_ref as _image_ref
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
from wip_deploy.config_gen.nats import (
    NATS_COMPONENT,
    NATS_CONFIG_CONTAINER_PATH,
    nats_config_hash,
    render_nats_conf,
)
from wip_deploy.config_gen.router import generate_router_config
from wip_deploy.renderers.base import FileTree
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
    ctx = make_spec_context(deployment, components)
    resolved_env = resolve_all_env(
        deployment, components, apps, ctx,
        collected_secrets=set(secrets.values.keys()),
    )

    tree = FileTree()

    # Namespace
    tree.add("namespace.yaml", _render_namespace(ns))

    # Secrets
    tree.add("secrets.yaml", _render_secrets(ns, secrets), mode=0o600)

    # Spec-declared config-file API keys: a dedicated Secret carrying
    # the rendered api-keys.json, mounted (subPath) into components
    # whose manifest declares WIP_AUTH_API_KEYS_FILE.
    api_keys_json = generate_api_keys_json(deployment, secrets)
    if api_keys_json is not None:
        tree.add(
            "api-keys-secret.yaml",
            _render_api_keys_secret(ns, api_keys_json),
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
    ingress_cfg = generate_ingress_config(deployment, components, apps)
    tree.add("ingress.yaml", _render_ingress(ingress_cfg, ns))

    # NetworkPolicies (CASE-238 MVP: namespace-wide isolation;
    # per-component egress rules are a follow-up).
    tree.add("network-policies.yaml", _render_network_policies(ns))

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
#   - allow-ingress-controller  → nginx-ingress (in 'ingress' ns) reaches us
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


def _render_network_policies(ns: str) -> str:
    """Render the namespace-isolation NetworkPolicy bundle.

    CASE-238 MVP — restores the cross-namespace isolation guarantee
    that the legacy `k8s/network-policies.yaml` provided. Per-component
    egress rules are a follow-up case (see file header).
    """
    labels = {_LABELS_PART_OF: "wip"}

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
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"from": [{"podSelector": {}}]}],
            "egress": [{"to": [{"podSelector": {}}]}],
        },
    }

    # NGINX ingress controller runs in the 'ingress' namespace on
    # MicroK8s. The kubernetes.io/metadata.name label is auto-added
    # by k8s 1.21+ on every namespace.
    allow_ingress_controller = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "allow-ingress-controller",
            "namespace": ns,
            "labels": labels,
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": "ingress",
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
            "podSelector": {},
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
    return "---\n".join(parts)


# ────────────────────────────────────────────────────────────────────
# Secrets
# ────────────────────────────────────────────────────────────────────


def _render_api_keys_secret(ns: str, api_keys_json: str) -> str:
    """The rendered WIP_AUTH_API_KEYS_FILE document as its own Secret,
    so it can be subPath-mounted as a file (wip-secrets carries flat
    name→value pairs, not files)."""
    return _dump({
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "wip-api-keys-config",
            "namespace": ns,
            "labels": {_LABELS_PART_OF: "wip"},
        },
        "type": "Opaque",
        "stringData": {"api-keys.json": api_keys_json},
    })


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
    """Render shared config as a ConfigMap + Dex config if active."""
    docs: list[dict[str, Any]] = []

    # wip-config: union of all literal env vars that appear in 2+ components.
    # In practice, the shared ones are CORS, file storage, postgres, service
    # URLs, auth settings. We just dump the full literal env from the first
    # core component (registry) as the shared set — components already
    # get envFrom: configMapRef so they'll pick up everything.
    shared_env: dict[str, str] = {}
    for _name, env in resolved_env.items():
        for k, v in env.merged().items():
            if isinstance(v, Literal) and k not in shared_env:
                shared_env[k] = v.value

    # Filter to truly shared keys (appear in 2+ envs).
    key_counts: dict[str, int] = {}
    for _name, env in resolved_env.items():
        for k in env.merged():
            key_counts[k] = key_counts.get(k, 0) + 1
    shared_keys = {k for k, count in key_counts.items() if count >= 2}
    shared_data: dict[str, str] = {
        k: v for k, v in sorted(shared_env.items()) if k in shared_keys
    }

    docs.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "wip-config",
            "namespace": ns,
            "labels": {_LABELS_PART_OF: "wip"},
        },
        "data": shared_data,
    })

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

    # Spec-declared API keys file — mounted for exactly the manifests
    # that declare WIP_AUTH_API_KEYS_FILE (the env is the opt-in).
    if deployment.spec.auth.api_keys and declares_api_keys_file(owner):
        volume_mounts.append({
            "name": "api-keys-config",
            "mountPath": API_KEYS_CONTAINER_PATH,
            "subPath": "api-keys.json",
            "readOnly": True,
        })
        volumes.append({
            "name": "api-keys-config",
            "secret": {"secretName": "wip-api-keys-config"},
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

    # Env: per-component vars as explicit env entries (secrets from secretKeyRef,
    # literals inline). Shared literals come from envFrom configMapRef.
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

    # Probes
    hc = owner.spec.healthcheck
    if hc is not None:
        if hc.endpoint is not None:
            port = _resolve_check_port(owner, hc.port)
            probe = {
                "httpGet": {"path": hc.endpoint, "port": port},
            }
        elif hc.command is not None:
            probe = {"exec": {"command": list(hc.command)}}
        else:
            probe = {}

        container["readinessProbe"] = {
            **probe,
            "initialDelaySeconds": hc.start_period_seconds,
            "periodSeconds": hc.interval_seconds,
            "timeoutSeconds": hc.timeout_seconds,
        }
        container["livenessProbe"] = {
            **probe,
            "initialDelaySeconds": hc.start_period_seconds + 5,
            "periodSeconds": max(hc.interval_seconds * 3, 30),
            "timeoutSeconds": hc.timeout_seconds,
        }

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
    """Same as compose: explicit command, Dex/MinIO overrides, or None."""
    if owner.spec.command is not None:
        return list(owner.spec.command)
    if isinstance(owner, App):
        return None
    name = owner.metadata.name
    if name == "dex":
        return ["dex", "serve", "/etc/dex/config.yaml"]
    return None


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


def _render_ingress(ingress_cfg: Any, ns: str) -> str:
    """Render IngressConfig into one or more Ingress resources.

    API routes get a single shared Ingress (no auth annotations).
    Each auth-protected app gets its own Ingress (with auth-url annotations).
    The auth gateway itself gets a direct Ingress (no auth-url — it IS the auth).
    """
    from wip_deploy.config_gen.nginx_ingress import IngressConfig

    cfg: IngressConfig = ingress_cfg
    docs: list[dict[str, Any]] = []

    # Classify rules. strip_prefix rules need per-rule Ingresses because
    # the rewrite-target annotation is per-Ingress and applying it across
    # mixed paths would strip prefixes from routes that don't want it.
    # upstream_host_header also forces a per-rule Ingress: the
    # upstream-vhost annotation is per-Ingress, like rewrite-target.
    strip_rules = [r for r in cfg.rules if r.strip_prefix or r.upstream_host_header]
    non_strip = [r for r in cfg.rules if not (r.strip_prefix or r.upstream_host_header)]
    api_rules = [r for r in non_strip if not r.auth_protected and r.backend_service != "wip-auth-gateway"]
    auth_gateway_rules = [r for r in non_strip if r.backend_service == "wip-auth-gateway"]
    app_rules = [r for r in non_strip if r.auth_protected]

    tls_block = [{
        "hosts": [cfg.hostname],
        "secretName": cfg.tls_secret_name,
    }]

    base_annotations: dict[str, str] = {
        "nginx.ingress.kubernetes.io/ssl-redirect": "true",
        "nginx.ingress.kubernetes.io/proxy-body-size": cfg.proxy_body_size,
        # Stock timeouts (60s) are fine for normal API calls but kill
        # long uploads (restores, bulk ingest). 1 hour is what Caddy
        # does by default via its idle_timeout; matching here.
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
        "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
    }

    # Main ingress: auth gateway routes + API routes + Dex
    main_paths: list[dict[str, Any]] = []

    for r in auth_gateway_rules:
        main_paths.append(_ingress_path(r))

    for r in api_rules:
        path_entry = _ingress_path(r)
        main_paths.append(path_entry)

    if main_paths:
        main_annotations = dict(base_annotations)
        # Streaming support for routes that need it
        if any(r.streaming for r in api_rules + auth_gateway_rules):
            main_annotations["nginx.ingress.kubernetes.io/proxy-buffering"] = "off"

        docs.append({
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "wip-ingress",
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
                "annotations": main_annotations,
            },
            "spec": {
                "ingressClassName": cfg.ingress_class,
                "tls": tls_block,
                "rules": [{
                    "host": cfg.hostname,
                    "http": {"paths": main_paths},
                }],
            },
        })

    # Per-app ingresses (with auth-url annotations)
    for r in app_rules:
        app_annotations = dict(base_annotations)
        if cfg.gateway_auth_url:
            app_annotations["nginx.ingress.kubernetes.io/auth-url"] = cfg.gateway_auth_url
            app_annotations["nginx.ingress.kubernetes.io/auth-signin"] = (
                f"https://{cfg.hostname}/auth/login?return_to=$escaped_request_uri"
            )
            app_annotations["nginx.ingress.kubernetes.io/auth-response-headers"] = (
                "X-WIP-User,X-WIP-Groups,X-API-Key"
            )
        if r.streaming:
            app_annotations["nginx.ingress.kubernetes.io/proxy-buffering"] = "off"

        # Service name without wip- prefix for the ingress name
        ingress_name = f"{r.backend_service.removeprefix('wip-')}-ingress"
        docs.append({
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": ingress_name,
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
                "annotations": app_annotations,
            },
            "spec": {
                "ingressClassName": cfg.ingress_class,
                "tls": tls_block,
                "rules": [{
                    "host": cfg.hostname,
                    "http": {"paths": [_ingress_path(r)]},
                }],
            },
        })

    # Per-rule ingresses for strip_prefix routes. Uses nginx-ingress's
    # regex-path idiom (`/prefix(/|$)(.*)` → `rewrite-target: /$2`) so
    # the backend sees the request as if it had hit the service at its
    # root — needed for backends like MinIO whose SigV2 presigned URLs
    # are computed over a path that doesn't include the public prefix.
    for r in strip_rules:
        strip_annotations = dict(base_annotations)
        if r.strip_prefix:
            strip_annotations["nginx.ingress.kubernetes.io/use-regex"] = "true"
            strip_annotations["nginx.ingress.kubernetes.io/rewrite-target"] = "/$2"
        if r.upstream_host_header:
            # The Host the backend sees must equal the Host services sign
            # presigned URLs against — the internal endpoint host:port,
            # byte for byte. On k8s the env layer hands services the
            # fully-qualified service DNS (wip-<name>.<ns>.svc.cluster.local,
            # config_gen/env.py _component_host), so the restored Host must
            # be that same FQDN form: the short service name validated as a
            # different host and Garage/MinIO refused the SigV4 signature.
            strip_annotations["nginx.ingress.kubernetes.io/upstream-vhost"] = (
                f"{r.backend_service}.{ns}.svc.cluster.local:{r.backend_port}"
            )
        if cfg.gateway_auth_url and r.auth_protected:
            strip_annotations["nginx.ingress.kubernetes.io/auth-url"] = cfg.gateway_auth_url
            strip_annotations["nginx.ingress.kubernetes.io/auth-signin"] = (
                f"https://{cfg.hostname}/auth/login?return_to=$escaped_request_uri"
            )
            strip_annotations["nginx.ingress.kubernetes.io/auth-response-headers"] = (
                "X-WIP-User,X-WIP-Groups,X-API-Key"
            )
        if r.streaming:
            strip_annotations["nginx.ingress.kubernetes.io/proxy-buffering"] = "off"

        ingress_name = f"{r.backend_service.removeprefix('wip-')}-ingress"

        # CASE-248: when the route declares preserve_prefix_subpaths,
        # the strip rule's regex EXCLUDES those subpaths via a negative
        # lookahead, so the admin Ingress's Prefix paths win for them.
        # Without the exclusion, nginx-ingress's cross-Ingress merging
        # would let the strip regex (with rewrite-target annotation)
        # absorb requests that should reach the admin paths.
        # The lookahead requires segment boundary (`/` or end-of-string)
        # so e.g. a bucket named "healthy" still routes through the strip.
        if r.preserve_prefix_subpaths:
            excluded = []
            for sub in r.preserve_prefix_subpaths:
                # Strip the route prefix → admin segment name (e.g.
                # "/garage/health" → "health"). Skip if the subpath
                # isn't actually under the route prefix.
                if not sub.startswith(r.path + "/"):
                    continue
                excluded.append(sub[len(r.path) + 1:])
            if excluded:
                excl_alt = "|".join(excluded)
                # /garage/(?!health(/|$)|admin(/|$))(.*) — strip everything
                # under the prefix EXCEPT the listed preserved segments.
                strip_path = (
                    f"{r.path}/(?!(?:{excl_alt})(?:/|$))(.*)"
                )
                strip_rewrite = "/$1"
            else:
                strip_path = f"{r.path}(/|$)(.*)"
                strip_rewrite = "/$2"
        else:
            strip_path = f"{r.path}(/|$)(.*)"
            strip_rewrite = "/$2"

        if r.strip_prefix:
            # Override the rewrite-target if we computed a custom one.
            strip_annotations["nginx.ingress.kubernetes.io/rewrite-target"] = strip_rewrite
            path_entry = {"path": strip_path, "pathType": "ImplementationSpecific"}
        else:
            # upstream_host_header without strip: plain Prefix path,
            # no rewrite — the per-rule Ingress exists only to carry
            # the upstream-vhost annotation.
            path_entry = {"path": r.path, "pathType": "Prefix"}

        docs.append({
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": ingress_name,
                "namespace": ns,
                "labels": {_LABELS_PART_OF: "wip"},
                "annotations": strip_annotations,
            },
            "spec": {
                "ingressClassName": cfg.ingress_class,
                "tls": tls_block,
                "rules": [{
                    "host": cfg.hostname,
                    "http": {"paths": [{
                        **path_entry,
                        "backend": {
                            "service": {
                                "name": r.backend_service,
                                "port": {"number": r.backend_port},
                            },
                        },
                    }]},
                }],
            },
        })

        # Preserve-prefix subpaths get their own Ingress (no rewrite
        # annotation). With the strip regex now excluding these
        # segments, the admin Ingress's Prefix paths cleanly own
        # e.g. /<prefix>/health/* and /<prefix>/admin/* — no cross-Ingress fight.
        if r.preserve_prefix_subpaths:
            preserve_annotations = dict(base_annotations)
            if cfg.gateway_auth_url and r.auth_protected:
                preserve_annotations["nginx.ingress.kubernetes.io/auth-url"] = cfg.gateway_auth_url
                preserve_annotations["nginx.ingress.kubernetes.io/auth-signin"] = (
                    f"https://{cfg.hostname}/auth/login?return_to=$escaped_request_uri"
                )
                preserve_annotations["nginx.ingress.kubernetes.io/auth-response-headers"] = (
                    "X-WIP-User,X-WIP-Groups,X-API-Key"
                )
            if r.streaming:
                preserve_annotations["nginx.ingress.kubernetes.io/proxy-buffering"] = "off"

            preserve_paths = [{
                "path": sub,
                "pathType": "Prefix",
                "backend": {
                    "service": {
                        "name": r.backend_service,
                        "port": {"number": r.backend_port},
                    },
                },
            } for sub in r.preserve_prefix_subpaths]

            docs.append({
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": f"{ingress_name}-admin",
                    "namespace": ns,
                    "labels": {_LABELS_PART_OF: "wip"},
                    "annotations": preserve_annotations,
                },
                "spec": {
                    "ingressClassName": cfg.ingress_class,
                    "tls": tls_block,
                    "rules": [{
                        "host": cfg.hostname,
                        "http": {"paths": preserve_paths},
                    }],
                },
            })

    # Bare-host redirect (CASE-368): a standalone Ingress that 301s `/` to
    # the resolved target via nginx's permanent-redirect annotation. The
    # annotation fires before proxying, so the backend is never contacted —
    # but the Ingress schema still requires one, so we point it at the
    # service that owns the redirect target (longest path-prefix match).
    if cfg.root_redirect_target:
        backend = _root_redirect_backend(cfg.root_redirect_target, cfg.rules)
        if backend is not None:
            redirect_annotations = dict(base_annotations)
            redirect_annotations["nginx.ingress.kubernetes.io/permanent-redirect"] = (
                f"https://{cfg.hostname}{cfg.root_redirect_target}"
            )
            docs.append({
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": "wip-root-redirect",
                    "namespace": ns,
                    "labels": {_LABELS_PART_OF: "wip"},
                    "annotations": redirect_annotations,
                },
                "spec": {
                    "ingressClassName": cfg.ingress_class,
                    "tls": tls_block,
                    "rules": [{
                        "host": cfg.hostname,
                        "http": {"paths": [{
                            "path": "/",
                            "pathType": "Exact",
                            "backend": {
                                "service": {
                                    "name": backend[0],
                                    "port": {"number": backend[1]},
                                },
                            },
                        }]},
                    }],
                },
            })

    return _dump_multi(docs)


def _root_redirect_backend(
    target: str, rules: list[Any]
) -> tuple[str, int] | None:
    """Pick the (never-hit) placeholder backend for the root-redirect Ingress.

    The rule whose path is the longest prefix of the redirect target owns
    that target (e.g. target `/apps/rc/` → rule `/apps/rc`; `/auth/login` →
    rule `/auth`). Returns (service_name, port), or None if no rule matches
    (defensive — the target is derived from an enabled app or the gateway,
    both of which always contribute a rule).
    """
    best: Any = None
    for r in rules:
        if target.startswith(r.path) and (best is None or len(r.path) > len(best.path)):
            best = r
    if best is None:
        return None
    return best.backend_service, best.backend_port


def _ingress_path(rule: Any) -> dict[str, Any]:
    return {
        "path": rule.path,
        "pathType": "Prefix",
        "backend": {
            "service": {
                "name": rule.backend_service,
                "port": {"number": rule.backend_port},
            },
        },
    }


# ────────────────────────────────────────────────────────────────────
# YAML helpers
# ────────────────────────────────────────────────────────────────────


def _dump(obj: dict[str, Any]) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)


def _dump_multi(docs: list[dict[str, Any]]) -> str:
    return "---\n".join(_dump(d) for d in docs)
