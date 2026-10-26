"""Rendered secrets are protected by the install directory, not by file modes
that containers cannot read.

On Linux, a host file bind-mounted into a container whose process runs as a
non-root user is read through the file's "other" permission bits: under
rootless podman the file appears owned by root inside the container, and
under a root daemon the container uid differs from the host owner. A 0600 or
0640 file is therefore unreadable there. Dex (uid 1001) and registry /
mcp-server (appuser) are such containers. The API-keys file used to be
rendered 0600, so those services could not read it on Linux compose hosts.

Contract under test: `FileTree.write` makes the tree's root 0700 (new and
pre-existing roots), and every file bind-mounted into a non-root container
carries the other-read bit. Secrets stay private to the operator via the
directory.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.renderers.base import FileTree
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
from wip_deploy.spec.deployment import SpecAPIKey

REPO_ROOT = Path(__file__).resolve().parents[3]


def _non_root_services() -> set[str]:
    """Components whose image runs as a non-root user: every Dockerfile with
    a non-root USER, plus Dex (upstream image, USER dex)."""
    names = {"dex"}
    for dockerfile in REPO_ROOT.glob("components/*/Dockerfile"):
        users = re.findall(r"^USER\s+(\S+)", dockerfile.read_text(), re.MULTILINE)
        if users and users[-1].split(":")[0] not in ("root", "0"):
            names.add(dockerfile.parent.name)
    return names


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str) -> Deployment:
    platform = {
        "compose": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "dev": PlatformSpec(dev=DevPlatform(mode="simple")),
    }[target]
    return Deployment(
        metadata=DeploymentMetadata(name="perms"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            auth=AuthSpec(
                mode="hybrid", gateway=True,
                api_keys=[SpecAPIKey(name="probe", namespaces=["wip"])],
            ),
            network=NetworkSpec(hostname="wip.example.com"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def test_non_root_set_is_derived_from_the_images() -> None:
    assert {"dex", "registry", "mcp-server"} <= _non_root_services()


@pytest.mark.parametrize("target", ["compose", "dev"])
def test_files_mounted_into_non_root_containers_are_readable(
    target: str, tmp_path: Path, discovery: Discovery,
) -> None:
    d = _deployment(target)
    secrets = ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"))
    if target == "dev":
        tree = render_dev_simple(d, discovery.components, discovery.apps, secrets, repo_root=REPO_ROOT)
    else:
        tree = render_compose(d, discovery.components, discovery.apps, secrets)
    services = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]

    checked = []
    for name in _non_root_services() & set(services):
        for volume in services[name].get("volumes") or []:
            source = volume.split(":", 1)[0]
            if not source.startswith("./"):
                continue
            mounted = Path(source[2:])
            # A file mount, or every rendered file under a directory mount
            # (the declared-key file is mounted through its directory).
            for path, entry in tree.files.items():
                if path != mounted and mounted not in path.parents:
                    continue
                checked.append(f"{name}:./{path}")
                assert entry.mode & stat.S_IROTH, f"{name} cannot read {path} (mode {oct(entry.mode)})"
    # The two files that motivated this must be among those checked.
    assert any(c.startswith("dex:") and c.endswith("config/dex/config.yaml") for c in checked), checked
    assert any(c.startswith("registry:") and "api-keys" in c for c in checked), checked


def test_write_makes_the_root_private(tmp_path: Path) -> None:
    tree = FileTree()
    tree.add("config/dex/config.yaml", "x")
    tree.add(".env", "y", mode=0o600)

    fresh = tmp_path / "fresh"
    tree.write(fresh)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700

    existing = tmp_path / "existing"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)
    tree.write(existing)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o700
    assert stat.S_IMODE((existing / "config/dex/config.yaml").stat().st_mode) == 0o644
    assert stat.S_IMODE((existing / ".env").stat().st_mode) == 0o600
