"""A dev install reloads every mounted service it can, and says which it can't.

The dev render mounts a component's source whenever it has a build context,
but reloads only a service whose resolved command is uvicorn. auth-gateway
pinned no command, so its source was mounted and edits never ran; mcp-server
and app-edge have no watcher at all. An `--app-source` app without a
Dockerfile.dev was silently built from its production Dockerfile. Now
auth-gateway pins its uvicorn command, every uvicorn image's component does,
and the render notes each mounted service that needs a restart and each
Dockerfile.dev fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_dev_simple
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def disc() -> Discovery:
    return discover(REPO_ROOT)


def _render(tmp_path: Path, disc: Discovery, *, app_sources: dict[str, Path] | None = None):
    apps = [AppRef(name=n) for n in (app_sources or {})]
    d = Deployment(
        metadata=DeploymentMetadata(name="t1242"),
        spec=DeploymentSpec(
            target="dev",
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            apps=apps,
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.localhost"),
            images=ImagesSpec(),
            platform=PlatformSpec(dev=DevPlatform(mode="simple", app_sources=app_sources or {})),
            secrets=SecretsSpec(backend="file", location=str(tmp_path / "s")),
        ),
    )
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))
    return render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT)


def test_auth_gateway_hot_reloads(tmp_path: Path, disc: Discovery) -> None:
    tree = _render(tmp_path, disc)
    svc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]["auth-gateway"]
    assert "--reload" in svc["command"]
    assert svc["environment"]["WATCHFILES_FORCE_POLLING"] == "1"
    assert not any("auth-gateway" in n for n in tree.notes)


def test_a_mounted_service_without_a_watcher_is_noted(tmp_path: Path, disc: Discovery) -> None:
    notes = _render(tmp_path, disc).notes
    assert any(
        n.startswith("mcp-server: its source is mounted but it does not hot-reload")
        and "podman restart t1242_wip-mcp-server" in n
        for n in notes
    ), notes


def test_an_app_without_dockerfile_dev_is_noted(tmp_path: Path, disc: Discovery) -> None:
    checkout = tmp_path / "wip-kb"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM node:20\n")
    notes = _render(tmp_path, disc, app_sources={"wip-kb": checkout}).notes
    assert any(n.startswith(f"wip-kb: {checkout} has no Dockerfile.dev") for n in notes), notes

    (checkout / "Dockerfile.dev").write_text("FROM node:20\n")
    notes = _render(tmp_path, disc, app_sources={"wip-kb": checkout}).notes
    assert not any(n.startswith("wip-kb:") for n in notes), notes


def test_every_uvicorn_image_pins_its_command(disc: Discovery) -> None:
    """The class guard: a component whose image starts uvicorn but whose
    manifest pins no command is mounted in dev and never reloads."""
    unpinned = []
    for c in disc.components:
        if c.spec.image.build_context is None:
            continue
        dockerfile = REPO_ROOT / "components" / c.metadata.name / "Dockerfile"
        if not dockerfile.is_file():
            continue
        cmds = re.findall(r"^CMD\s+(\[.*\])\s*$", dockerfile.read_text(), re.MULTILINE)
        if cmds and json.loads(cmds[-1])[0] == "uvicorn" and not c.spec.command:
            unpinned.append(c.metadata.name)
    assert unpinned == []
