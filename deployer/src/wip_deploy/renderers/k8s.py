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

from typing import Any

import yaml

from wip_deploy.config_gen import (
    ResolvedEnv,
    SecretRef,
    generate_dex_config,
    generate_ingress_config,
    make_spec_context,
    resolve_all_env,
)
from wip_deploy.config_gen.env import Literal
from wip_deploy.config_gen.router import generate_router_config
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.compose_dex import render_dex_config
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

    for owner in active:
        name = owner.metadata.name
        env = resolved_env.get(name)
        if env is None:
            continue
        content = _render_component(owner, deployment, env, ns)
        # Storage-bearing → infrastructure/, otherwise services/
        if owner.spec.storage:
            tree.add(f"infrastructure/{name}.yaml", content)
        else:
            tree.add(f"services/{name}.yaml", content)

    # Ingress
    ingress_cfg = generate_ingress_config(deployment, components, apps)
    tree.add("ingress.yaml", _render_ingress(ingress_cfg, ns))

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
# Secrets
# ────────────────────────────────────────────────────────────────────


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
    dex_cfg = generate_dex_config(deployment, components, apps)
    if dex_cfg is not None:
        dex_yaml = render_dex_config(dex_cfg, secrets)
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
        svc_ports = [
            {"port": p.container_port, "targetPort": p.container_port, "protocol": p.protocol}
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


def _image_ref(owner: Component | App, deployment: Deployment) -> str:
    """Same logic as compose renderer — fully-qualified images are
    untouched; short names get the registry prefix."""
    ref = owner.spec.image
    spec_images = deployment.spec.images
    if "/" in ref.name:
        tag = ref.tag or "latest"
        return f"{ref.name}:{tag}"
    tag = ref.tag or spec_images.tag
    if spec_images.registry:
        return f"{spec_images.registry}/{ref.name}:{tag}"
    return f"{ref.name}:{tag}"


def _command_for(owner: Component | App) -> list[str] | None:
    """Same as compose: explicit command, Dex/MinIO overrides, or None."""
    if owner.spec.command is not None:
        return list(owner.spec.command)
    if isinstance(owner, App):
        return None
    name = owner.metadata.name
    if name == "dex":
        return ["dex", "serve", "/etc/dex/config.yaml"]
    if name == "minio":
        return ["server", "/data", "--console-address", ":9001"]
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

    # Classify rules
    api_rules = [r for r in cfg.rules if not r.auth_protected and r.backend_service != "wip-auth-gateway"]
    auth_gateway_rules = [r for r in cfg.rules if r.backend_service == "wip-auth-gateway"]
    app_rules = [r for r in cfg.rules if r.auth_protected]

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

    return _dump_multi(docs)


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
