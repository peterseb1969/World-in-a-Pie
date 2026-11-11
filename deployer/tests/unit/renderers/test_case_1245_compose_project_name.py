"""CASE-1245 item 2 — the rendered compose file names its project after the install.

Without a top-level ``name:``, compose derives the project from the *directory*
the file sits in, while the deployer keys everything else — container names, the
network, and the ``com.docker.compose.project`` label its own container queries
filter on — on ``deployment.metadata.name``. An install written with
``--install-dir`` (basename free to differ from ``--name``), or moved on disk
afterwards, then had its containers invisible to those filters: the post-up check
read an empty list and reported a healthy `compose up` as a failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.naming import compose_project_name
from wip_deploy.renderers import render_compose, render_dev_simple
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
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

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _compose_deployment(name: str) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name=name),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _dev_deployment(name: str) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name=name),
        spec=DeploymentSpec(
            target="dev",
            modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(tag="dev"),
            platform=PlatformSpec(dev=DevPlatform(mode="simple")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(
    tmp_path: Path, deployment: Deployment, discovery: Discovery
) -> ResolvedSecrets:
    return ensure_secrets(
        deployment,
        discovery.components,
        discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )


def _rendered_compose_doc(
    tmp_path: Path, discovery: Discovery, renderer: str, name: str
) -> dict:
    d = _compose_deployment(name) if renderer == "compose" else _dev_deployment(name)
    secrets = _secrets(tmp_path, d, discovery)
    if renderer == "compose":
        tree = render_compose(d, discovery.components, discovery.apps, secrets)
    else:
        tree = render_dev_simple(
            d, discovery.components, discovery.apps, secrets, repo_root=REPO_ROOT
        )
    return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)  # type: ignore[no-any-return]


class TestProjectNamePinned:
    @pytest.mark.parametrize("renderer", ["compose", "dev"])
    def test_names_the_project_after_the_install(
        self, tmp_path: Path, real_discovery: Discovery, renderer: str
    ) -> None:
        doc = _rendered_compose_doc(tmp_path, real_discovery, renderer, "prod-east")
        assert doc["name"] == "prod-east"

    @pytest.mark.parametrize("renderer", ["compose", "dev"])
    def test_the_pinned_project_is_what_the_label_filters_match(
        self, tmp_path: Path, real_discovery: Discovery, renderer: str
    ) -> None:
        """The file's project and the install name the container queries filter
        on are the same value, whatever the install directory is called."""
        d = (
            _compose_deployment("prod-east")
            if renderer == "compose"
            else _dev_deployment("prod-east")
        )
        doc = _rendered_compose_doc(tmp_path, real_discovery, renderer, d.metadata.name)
        assert doc["name"] == d.metadata.name
        assert doc["name"] == compose_project_name(d.metadata.name)

    @pytest.mark.parametrize("renderer", ["compose", "dev"])
    def test_an_install_at_the_default_directory_is_not_renamed(
        self, tmp_path: Path, real_discovery: Discovery, renderer: str
    ) -> None:
        """An install under ``~/.wip-deploy/<name>/`` already derived this project
        name from its directory, so pinning it must resolve to the same value —
        a different one would rename the install's volumes."""
        doc = _rendered_compose_doc(tmp_path, real_discovery, renderer, "default")
        assert doc["name"] == "default"

    def test_project_name_is_the_first_top_level_key(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        doc = _rendered_compose_doc(tmp_path, real_discovery, "compose", "prod-east")
        assert list(doc) == ["name", "services", "networks", "volumes"]
