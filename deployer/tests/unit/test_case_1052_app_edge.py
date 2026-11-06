"""The app edge component (CASE-1052).

The edge serves the apps attached through the Registry's API. The deployer
renders it when the `app-edge` module is enabled, gives it a key that may read
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


EDGE_MODULES = ["mcp-server", "app-edge"]


def test_active_when_the_module_is_enabled(discovery: Discovery) -> None:
    d = _minimal_compose(modules=EDGE_MODULES)
    assert is_component_active(_edge(discovery), d)
    assert APP_EDGE_KEY in effective_api_keys(d)

    d = _minimal_compose()
    assert d.spec.auth.gateway
    assert not is_component_active(_edge(discovery), d)
    assert APP_EDGE_KEY not in effective_api_keys(d)


def test_its_key_reads_attachments_and_nothing_else(tmp_path: Path, discovery: Discovery) -> None:
    d = _minimal_compose(modules=EDGE_MODULES)
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
    d = _minimal_compose(modules=EDGE_MODULES, apps=["wip-kb"])
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


def test_the_router_hands_every_other_origin_to_the_edge(compose_tree) -> None:
    caddyfile = compose_tree.files[Path("config/caddy/Caddyfile")].content
    start = caddyfile.index("\nhttps://:8443 {")
    block = caddyfile[start:caddyfile.index("\n}\n", start)]
    assert "reverse_proxy wip-app-edge:8080" in block
    assert "forward_auth" not in block  # the edge runs it per attachment
    # Deployer apps have sites of their own, named, ahead of the catch-all.
    assert caddyfile.index("\nkb.") < start


def test_k8s_ingress_and_network_policy(tmp_path: Path, discovery: Discovery) -> None:
    """wip-caddy hands every origin no deployer app has to the edge, with no
    gateway in front (the edge runs its own forward_auth); the edge accepts
    traffic from wip-caddy only and its egress is limited."""
    d = _k8s_deployment(modules=EDGE_MODULES, apps=["wip-kb"])
    ns = d.spec.platform.k8s.namespace
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))

    docs = yaml.safe_load_all(tree.files[Path("services/caddy.yaml")].content)
    caddyfile = next(x for x in docs if x["kind"] == "ConfigMap")["data"]["Caddyfile"]
    edge = caddyfile[caddyfile.index("\n:8080 {"):]
    edge = edge[edge.index("    handle {\n"):]
    edge = edge[: edge.index("\n    }\n")]
    assert f"reverse_proxy wip-app-edge.{ns}.svc.cluster.local:8080" in edge
    assert "forward_auth" not in edge
    # Deployer apps have sites of their own, ahead of the catch-all.
    assert "\nhttp://kb." in caddyfile

    policies = {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content) if doc}
    edge_policy = policies["app-edge-egress"]["spec"]
    assert edge_policy["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "app-edge"}}
    assert edge_policy["policyTypes"] == ["Ingress", "Egress"]
    assert edge_policy["ingress"] == [{"from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "caddy"}}}]}]
    targets = [t for rule in edge_policy["egress"] for t in rule["to"]]
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
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))
    policies = {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content) if doc}
    assert "app-edge-egress" not in policies
    assert policies["allow-same-namespace"]["spec"]["podSelector"] == {}
