"""wip-caddy keeps the ingress controller's X-Forwarded-For on k8s.

Caddy replaces X-Forwarded-For with its peer's address unless the peer is a
trusted proxy. On k8s the peer is the ingress controller, so without trust
every request reached the backends as the controller pod, and the per-IP
rate limit (keyed on the first X-Forwarded-For entry) was one bucket for
everyone. wip-caddy now trusts the cluster's pod ranges (--cluster-cidr
pods=), where the controller runs. On compose and dev wip-caddy faces the
client itself and trusts no one, so a client cannot choose its own address.
(CASE-1071, finding 6.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.spec.deployment import K8sClusterRanges

from .test_compose import REPO_ROOT, _minimal_compose
from .test_compose import _secrets as _compose_secrets
from .test_k8s import _k8s_deployment
from .test_k8s import _secrets as _k8s_secrets


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _k8s_caddyfile(discovery: Discovery, tmp_path: Path, **ranges: list[str]) -> str:
    d = _k8s_deployment(apps=["wip-kb"])
    assert d.spec.platform.k8s is not None
    d.spec.platform.k8s.cluster_ranges = K8sClusterRanges(**ranges)
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))
    docs = yaml.safe_load_all(tree.files[Path("services/caddy.yaml")].content)
    return next(x for x in docs if x["kind"] == "ConfigMap")["data"]["Caddyfile"]


def _global_block(caddyfile: str) -> str:
    return caddyfile[: caddyfile.index("\n}\n") + 3]


def test_k8s_trusts_the_pod_ranges(discovery, tmp_path):
    caddyfile = _k8s_caddyfile(
        discovery, tmp_path,
        pods=["10.42.0.0/16", "fd42::/56"], services=["10.43.0.0/16"], nodes=["192.168.1.20/32"],
    )
    block = _global_block(caddyfile)
    assert "    servers {\n        trusted_proxies static 10.42.0.0/16 fd42::/56\n    }\n" in block
    # Only the pod ranges: Services and nodes never call wip-caddy as a proxy.
    assert "10.43.0.0/16" not in block and "192.168.1.20/32" not in block


def test_k8s_without_pod_ranges_trusts_no_one(discovery, tmp_path):
    caddyfile = _k8s_caddyfile(discovery, tmp_path, nodes=["192.168.1.20/32"])
    assert "trusted_proxies" not in caddyfile


def test_compose_trusts_no_one(discovery, tmp_path):
    d = _minimal_compose(apps=["wip-kb"])
    tree = render_compose(d, discovery.components, discovery.apps, _compose_secrets(tmp_path, d, discovery))
    assert "trusted_proxies" not in tree.files[Path("config/caddy/Caddyfile")].content
