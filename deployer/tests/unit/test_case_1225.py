"""CASE-1225 — every install's runtime is scoped to its name.

Two installs on one host must not share a container name or a network:
container names are host-global (a second install's would collide), and on a
shared network one stack's services reach the other's databases. Each
install therefore gets

- containers ``<install>_wip-<component>``;
- its own network ``<install>_wip-network`` with netavark's ``isolate:
  strict`` (netavark 1.x routes between plain bridge networks, so without it
  any container on the host reaches the install by IP);
- the hostname ``wip-<component>`` as a network alias, so every peer URL
  (``http://wip-registry:8001`` and so on) is unchanged.

And because podman-compose exits 0 when podman refuses to create a container,
the apply checks after ``up`` that every rendered container exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from wip_deploy.apply import ApplyError, _check_containers_created
from wip_deploy.config_gen.attach_policy import attach_policy_json
from wip_deploy.discovery import Discovery, discover
from wip_deploy.naming import container_name, network_name
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(name: str, target: str) -> Deployment:
    platform = (
        PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d"))
        if target == "compose"
        else PlatformSpec(dev=DevPlatform(mode="simple"))
    )
    return Deployment(
        metadata=DeploymentMetadata(name=name),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["reporting-sync", "mcp-server"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _compose(name: str, target: str, tmp_path: Path, disc: Discovery) -> dict[str, Any]:
    d = _deployment(name, target)
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / name))
    tree = (
        render_compose(d, disc.components, disc.apps, s)
        if target == "compose"
        else render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT)
    )
    return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)


@pytest.mark.parametrize("target", ["compose", "dev"])
class TestPerInstallNames:
    def test_two_installs_share_no_container_or_network_name(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        a = _compose("pi-a", target, tmp_path, discovery)
        b = _compose("pi-b", target, tmp_path, discovery)
        names_a = {svc["container_name"] for svc in a["services"].values()}
        names_b = {svc["container_name"] for svc in b["services"].values()}
        assert names_a and not names_a & names_b
        assert all(n.startswith("pi-a_wip-") for n in names_a)
        nets_a = {net["name"] for net in a["networks"].values()}
        nets_b = {net["name"] for net in b["networks"].values()}
        assert nets_a == {"pi-a_wip-network"} and nets_b == {"pi-b_wip-network"}

    def test_the_network_is_strictly_isolated(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        doc = _compose("pi-a", target, tmp_path, discovery)
        (net,) = doc["networks"].values()
        assert net["driver_opts"] == {"isolate": "strict"}

    def test_every_service_keeps_its_wip_hostname(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        """Peers address each other as wip-<component>; each install's
        network answers that name with its own service."""
        doc = _compose("pi-a", target, tmp_path, discovery)
        for svc_name, svc in doc["services"].items():
            assert svc["networks"] == {"wip-network": {"aliases": [f"wip-{svc_name}"]}}
            assert svc["container_name"] == f"pi-a_wip-{svc_name}"
        # Peer URLs are rendered against the alias, not the container name.
        env = doc["services"]["mcp-server"]["environment"]
        assert any("http://wip-" in str(v) for v in env.values())
        assert not any("pi-a_wip-" in str(v) for v in env.values())


def test_names_cannot_collide_across_installs() -> None:
    """Install names never contain '_', so <install>_wip-<x> is injective:
    install 'a' with 'b-registry' and install 'a-b' with 'registry' differ."""
    assert container_name("a", "b-registry") != container_name("a-b", "registry")
    assert network_name("a") != network_name("a-b")


def test_the_attach_policy_blocks_the_container_names(discovery: Discovery) -> None:
    """On its network a service also answers to its container name: an
    attachment upstream may not name it either."""
    d = _deployment("pi-a", "compose")
    blocked = set(json.loads(attach_policy_json(d, discovery.components, discovery.apps))["blocked_hosts"])
    assert {"wip-registry", "registry", "pi-a_wip-registry", "pi-a_wip-caddy"} <= blocked


class TestContainersCreated:
    def _compose_file(self, tmp_path: Path) -> Path:
        install = tmp_path / "pi-a"
        install.mkdir()
        f = install / "docker-compose.yaml"
        f.write_text(yaml.safe_dump({"services": {
            "registry": {"container_name": "pi-a_wip-registry"},
            "caddy": {"container_name": "pi-a_wip-caddy"},
        }}))
        return f

    def _ps(self, stdout: str):  # type: ignore[no-untyped-def]
        return (
            patch("wip_deploy.apply.shutil.which",
                  side_effect=lambda n: "/usr/bin/podman" if n == "podman" else None),
            patch("wip_deploy.apply.subprocess.run",
                  return_value=MagicMock(returncode=0, stdout=stdout, stderr="")),
        )

    def test_a_container_compose_did_not_create_fails_the_apply(self, tmp_path: Path) -> None:
        f = self._compose_file(tmp_path)
        which, run = self._ps("pi-a_wip-caddy\n")
        with which, run as mock_run, pytest.raises(ApplyError, match="pi-a_wip-registry"):
            _check_containers_created("pi-a", f, services=None)
        assert "label=com.docker.compose.project=pi-a" in mock_run.call_args.args[0]

    def test_all_present_passes(self, tmp_path: Path) -> None:
        f = self._compose_file(tmp_path)
        which, run = self._ps("pi-a_wip-caddy\npi-a_wip-registry\n")
        with which, run:
            _check_containers_created("pi-a", f, services=None)

    def test_a_scoped_up_checks_only_its_services(self, tmp_path: Path) -> None:
        f = self._compose_file(tmp_path)
        which, run = self._ps("pi-a_wip-caddy\n")
        with which, run:
            _check_containers_created("pi-a", f, services=["caddy"])

    def test_no_runtime_skips(self, tmp_path: Path) -> None:
        f = self._compose_file(tmp_path)
        with patch("wip_deploy.apply.shutil.which", return_value=None):
            _check_containers_created("pi-a", f, services=None)


def test_dev_images_are_tagged_per_install(tmp_path: Path, discovery: Discovery) -> None:
    """Installs built from different checkouts must not share an image: a
    rebuild in one clone would change what the others run on recreate."""
    a = _compose("pi-a", "dev", tmp_path, discovery)
    b = _compose("pi-b", "dev", tmp_path, discovery)
    built_a = {svc["image"] for svc in a["services"].values() if "build" in svc}
    built_b = {svc["image"] for svc in b["services"].values() if "build" in svc}
    assert built_a and all(img.endswith(":dev-pi-a") for img in built_a)
    assert not built_a & built_b
