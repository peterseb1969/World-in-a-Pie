"""The app edge component (CASE-1052).

The edge serves the apps attached through the Registry's API. The deployer
renders it whenever the auth gateway is on, gives it a key that may read
attachments and nothing else, hands it every /apps path no deployer app's
longer route claims, and on k8s keeps its egress away from WIP's own pods.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from wip_deploy.config_gen.api_keys import APP_EDGE_KEY, effective_api_keys, generate_api_keys_json
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.spec.activation import is_component_active

from .renderers.test_compose import REPO_ROOT, _minimal_compose
from .renderers.test_compose import _secrets as _compose_secrets
from .renderers.test_k8s import _k8s_deployment
from .renderers.test_k8s import _secrets as _k8s_secrets


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _edge(discovery: Discovery):
    return next(c for c in discovery.components if c.metadata.name == "app-edge")


def test_active_with_the_gateway_only(discovery: Discovery) -> None:
    d = _minimal_compose()
    assert d.spec.auth.gateway
    assert is_component_active(_edge(discovery), d)
    assert APP_EDGE_KEY in effective_api_keys(d)

    d.spec.auth.gateway = False
    d.spec.auth.mode = "api-key-only"
    assert not is_component_active(_edge(discovery), d)
    assert APP_EDGE_KEY not in effective_api_keys(d)


def test_its_key_reads_attachments_and_nothing_else(tmp_path: Path, discovery: Discovery) -> None:
    d = _minimal_compose()
    s = _compose_secrets(tmp_path, d, discovery)
    keys = {k["name"]: k for k in json.loads(generate_api_keys_json(d, s))["keys"]}
    edge = keys["wip-app-edge"]
    assert edge["groups"] == ["wip-app-edge"]
    assert edge["namespaces"] == ["wip-canary-unassigned"]
    assert edge["grants"] == {}
    assert edge["key"] == s.get("wip-app-edge-api-key")
    assert not {"wip-admins", "wip-services"} & set(edge["groups"])


@pytest.fixture(scope="module")
def compose_tree(tmp_path_factory: pytest.TempPathFactory, discovery: Discovery):
    d = _minimal_compose(apps=["wip-kb"])
    s = _compose_secrets(tmp_path_factory.mktemp("edge"), d, discovery)
    return render_compose(d, discovery.components, discovery.apps, s)


def test_compose_service_and_env(compose_tree) -> None:
    compose = yaml.safe_load(compose_tree.files[Path("docker-compose.yaml")].content)
    svc = compose["services"]["app-edge"]
    assert svc["container_name"] == "wip-app-edge"
    env = svc["environment"]
    assert env["GATEWAY_IDENTITY_HEADERS"] == "X-WIP-User,X-WIP-Groups"
    assert env["CLIENT_IDENTITY_HEADERS"] == "X-WIP-User,X-WIP-Groups,X-WIP-Auth-Method"
    assert env["PUBLIC_BASE_URL"].startswith("https://")
    assert env["WIP_ATTACH_POLICY_FILE"] == "/etc/wip/attach-policy.json"
    assert "8001" in env["REGISTRY_URL"] and "4180" in env["GATEWAY_URL"]
    assert env["EDGE_API_KEY"] == "${WIP_APP_EDGE_API_KEY}" or env["EDGE_API_KEY"]
    # The policy file reaches it; no key file does.
    vols = " ".join(svc.get("volumes", []))
    assert "config/auth:/etc/wip:ro" in vols
    assert "config/keys" not in vols


def test_the_router_hands_the_rest_of_apps_to_the_edge(compose_tree) -> None:
    caddyfile = compose_tree.files[Path("config/caddy/Caddyfile")].content
    start = caddyfile.index("    handle /apps/* {\n")
    block = caddyfile[start:caddyfile.index("\n    }\n", start)]
    assert "reverse_proxy wip-app-edge:8080" in block
    assert "forward_auth" not in block  # the edge runs it per attachment
    # Deployer apps' longer routes come first in the file and win by length.
    assert caddyfile.index("handle /apps/kb/*") < start


def test_k8s_ingress_and_network_policy(tmp_path: Path, discovery: Discovery) -> None:
    d = _k8s_deployment(apps=["wip-kb"])
    ns = d.spec.platform.k8s.namespace
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))

    ingress = [doc for doc in yaml.safe_load_all(tree.files[Path("ingress.yaml")].content) if doc]
    to_edge = [
        (doc, p) for doc in ingress for rule in doc["spec"]["rules"]
        for p in rule["http"]["paths"] if p["backend"]["service"]["name"] == "wip-app-edge"
    ]
    assert to_edge
    for doc, path in to_edge:
        assert path["path"].startswith("/apps")
        assert "nginx.ingress.kubernetes.io/auth-url" not in (doc["metadata"].get("annotations") or {})

    policies = {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content) if doc}
    edge = policies["app-edge-egress"]["spec"]
    assert edge["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "app-edge"}}
    targets = [t for rule in edge["egress"] for t in rule["to"]]
    assert {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "registry"}}} in targets
    assert {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "auth-gateway"}}} in targets
    other_ns = next(t for t in targets if "namespaceSelector" in t)
    assert other_ns["namespaceSelector"]["matchExpressions"][0] == {
        "key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": [ns],
    }
    # The broad allows leave the edge out.
    excluded = {"matchExpressions": [{"key": "app.kubernetes.io/name", "operator": "NotIn", "values": ["app-edge"]}]}
    assert policies["allow-same-namespace"]["spec"]["podSelector"] == excluded
    assert policies["allow-egress-https"]["spec"]["podSelector"] == excluded


def test_without_the_edge_the_policies_are_unchanged(tmp_path: Path, discovery: Discovery) -> None:
    d = _k8s_deployment(apps=["wip-kb"])
    d.spec.auth.gateway = False
    d.spec.auth.mode = "api-key-only"
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))
    policies = {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content) if doc}
    assert "app-edge-egress" not in policies
    assert policies["allow-same-namespace"]["spec"]["podSelector"] == {}
