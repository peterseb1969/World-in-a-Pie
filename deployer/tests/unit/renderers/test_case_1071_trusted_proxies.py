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


# The services' rate limit skips the X-Forwarded-For entries of the proxies
# wip-caddy trusts, to find the client: the rightmost entry outside those
# ranges. Left of them sits whatever a client sent, so keying on the first
# entry let every client pick its own bucket behind the ingress. Each
# service limiting requests must therefore be handed the same ranges
# wip-caddy trusts (CASE-1271, finding 4a21011f R3).


def _rate_limited_components() -> set[str]:
    """The components whose service installs wip-auth's rate limit, read from
    their source, so a service that adopts the limit without the ranges
    fails here."""
    found = set()
    for main in (REPO_ROOT / "components").glob("*/src/**/*.py"):
        if "setup_rate_limiting(" in main.read_text(encoding="utf-8"):
            found.add(main.relative_to(REPO_ROOT / "components").parts[0])
    return found


def test_the_rate_limited_services_are_found():
    assert {"registry", "document-store", "auth-gateway", "mcp-server"} <= _rate_limited_components()


def test_every_rate_limited_service_trusts_the_ranges_caddy_trusts_on_k8s(discovery, tmp_path):
    d = _k8s_deployment(apps=["wip-kb"])
    assert d.spec.platform.k8s is not None
    d.spec.platform.k8s.cluster_ranges = K8sClusterRanges(
        pods=["10.42.0.0/16", "fd42::/56"], services=["10.43.0.0/16"],
    )
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))
    checked = []
    for name in sorted(_rate_limited_components()):
        path = Path(f"services/{name}.yaml")
        if path not in tree.files:
            continue
        docs = yaml.safe_load_all(tree.files[path].content)
        deployment = next(x for x in docs if x and x["kind"] == "Deployment")
        env = {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert env.get("WIP_TRUSTED_PROXIES", {}).get("value") == "10.42.0.0/16,fd42::/56", name
        checked.append(name)
    assert "registry" in checked and "auth-gateway" in checked, checked


def test_off_k8s_the_services_trust_no_one(discovery, tmp_path):
    d = _minimal_compose(apps=["wip-kb"])
    tree = render_compose(d, discovery.components, discovery.apps, _compose_secrets(tmp_path, d, discovery))
    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    checked = []
    for name in sorted(_rate_limited_components()):
        svc = compose["services"].get(name)
        if svc is None:
            continue
        assert not svc.get("environment", {}).get("WIP_TRUSTED_PROXIES"), name
        checked.append(name)
    assert "registry" in checked, checked
