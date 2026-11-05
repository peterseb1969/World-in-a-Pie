"""The app edge on k8s may not reach the cluster's own ranges (CASE-1062).

The edge reaches attached apps anywhere (`ipBlock` 0.0.0.0/0 and ::/0). The
cluster's own pod, Service and node ranges (platform.k8s.cluster_ranges, set
with --cluster-cidr) are excepted from that rule, so the API server's endpoint
and the kubelets on the node addresses, and pod and Service IPs, are not
reachable through it. The node ranges also go into the attach policy as
`blocked_cidrs`, which the Registry and the edge check. Without ranges the
CLI warns. Attached apps in other namespaces stay reachable through the
edge's namespace rule, which this case leaves as it is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from wip_deploy.config_gen.attach_policy import attach_policy_json
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_k8s
from wip_deploy.spec.deployment import K8sClusterRanges

from .renderers.test_compose import REPO_ROOT
from .renderers.test_k8s import _k8s_deployment
from .renderers.test_k8s import _secrets as _k8s_secrets

RANGES = {"pods": ["10.42.0.0/16", "fd42::/56"], "services": ["10.43.0.0/16"], "nodes": ["192.168.1.20/32"]}


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _with_ranges(**ranges: list[str]):
    # The app edge is an optional module: enable it, or there is no edge to fence.
    d = _k8s_deployment(apps=["wip-kb"], modules=["mcp-server", "app-edge"])
    assert d.spec.platform.k8s is not None
    d.spec.platform.k8s.cluster_ranges = K8sClusterRanges(**ranges)
    return d


def _edge_egress(d, discovery: Discovery, tmp_path: Path) -> list[dict]:
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))
    docs = [x for x in yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content) if x]
    policy = next(x for x in docs if x["metadata"]["name"] == "app-edge-egress")
    return policy["spec"]["egress"]


def _ip_blocks(egress: list[dict]) -> dict[str, dict]:
    return {t["ipBlock"]["cidr"]: t["ipBlock"] for rule in egress for t in rule["to"] if "ipBlock" in t}


def test_the_anywhere_rule_excepts_the_cluster_ranges_per_family(discovery, tmp_path):
    blocks = _ip_blocks(_edge_egress(_with_ranges(**RANGES), discovery, tmp_path))
    assert blocks["0.0.0.0/0"]["except"] == ["10.42.0.0/16", "10.43.0.0/16", "192.168.1.20/32"]
    assert blocks["::/0"]["except"] == ["fd42::/56"]


def test_without_ranges_the_rule_is_unchanged(discovery, tmp_path):
    blocks = _ip_blocks(_edge_egress(_with_ranges(), discovery, tmp_path))
    assert blocks == {"0.0.0.0/0": {"cidr": "0.0.0.0/0"}, "::/0": {"cidr": "::/0"}}


def test_the_namespace_rule_is_left_as_it_is(discovery, tmp_path):
    """Decision CASE-1062#2: rule 2 (every other namespace) stays."""
    egress = _edge_egress(_with_ranges(**RANGES), discovery, tmp_path)
    selectors = [t["namespaceSelector"] for rule in egress for t in rule["to"] if "namespaceSelector" in t]
    assert selectors == [{"matchExpressions": [{
        "key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": ["wip-test"],
    }]}]


def test_the_node_ranges_join_the_attach_policy(discovery):
    policy = json.loads(attach_policy_json(_with_ranges(**RANGES), discovery.components, discovery.apps))
    assert policy["blocked_cidrs"] == ["192.168.1.20/32"]


def test_without_node_ranges_the_attach_policy_is_unchanged(discovery):
    """Pod and Service ranges never block an upstream: attached apps in other
    namespaces live there. And no field at all without node ranges."""
    policy = json.loads(attach_policy_json(
        _with_ranges(pods=["10.42.0.0/16"], services=["10.43.0.0/16"]), discovery.components, discovery.apps,
    ))
    assert "blocked_cidrs" not in policy


@pytest.mark.parametrize("bad", ["10.42.0.1/16", "not-a-range", "10.42.0.0/33"])
def test_a_range_must_be_a_network(bad):
    with pytest.raises(ValidationError):
        K8sClusterRanges(pods=[bad])


def test_cluster_cidr_inputs_build_the_ranges(tmp_path):
    from wip_deploy.build import BuildInputs, build_deployment

    d = build_deployment(BuildInputs(
        target="k8s", variant="dev", hostname="wip.example", tls="internal",
        k8s_cluster_cidrs=["pods=10.42.0.0/16", "nodes=192.168.1.20/32", "nodes=192.168.1.21/32"],
    ))
    assert d.spec.platform.k8s is not None
    ranges = d.spec.platform.k8s.cluster_ranges
    assert ranges.pods == ["10.42.0.0/16"]
    assert ranges.nodes == ["192.168.1.20/32", "192.168.1.21/32"]
    assert ranges.services == []


@pytest.mark.parametrize("bad", ["10.42.0.0/16", "cluster=10.42.0.0/16", "pods="])
def test_a_cluster_cidr_needs_a_kind(bad):
    from wip_deploy.build import BuildInputs, build_deployment

    with pytest.raises(ValueError, match="pods=CIDR"):
        build_deployment(BuildInputs(
            target="k8s", variant="dev", hostname="wip.example", tls="internal", k8s_cluster_cidrs=[bad],
        ))


def test_the_cli_warns_without_ranges(capsys):
    from wip_deploy.cli import _warn_app_edge_reach

    _warn_app_edge_reach(_with_ranges())
    assert "no --cluster-cidr" in capsys.readouterr().err
    _warn_app_edge_reach(_with_ranges(**RANGES))
    assert capsys.readouterr().err == ""
    # No edge, nothing to warn about.
    _warn_app_edge_reach(_k8s_deployment(apps=["wip-kb"]))
    assert capsys.readouterr().err == ""
