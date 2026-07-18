"""CASE-697 — k8s imagePullPolicy covers WIP apps, not just build_context.

The `Always` trigger must be "is this a WIP-owned image" (short name →
served from the deployment registry: backend services AND apps built in
their own repos), not "does it have a build_context" (which apps lack).
`spec.images.pull_policy: always` escalates to Always for every image.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import (
    AuthSpec,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.spec.deployment import AppRef

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _k8s_deployment(pull_policy: str = "if-not-present") -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="k8s",
            apps=[AppRef(name="react-console", enabled=True)],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(
                hostname="wip-kubi.local", https_port=443, http_port=80
            ),
            images=ImagesSpec(
                registry="ghcr.io/test", tag="test", pull_policy=pull_policy
            ),
            platform=PlatformSpec(k8s=K8sPlatform()),
            secrets=SecretsSpec(backend="k8s-secret"),
        ),
    )


def _pull_policy_of(tree, container_name: str) -> str | None | str:
    """imagePullPolicy of the named container across the rendered tree,
    or the sentinel 'NOT_FOUND' if no such container was rendered."""
    for f in tree.files.values():
        for doc in yaml.safe_load_all(f.content):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") not in ("Deployment", "StatefulSet"):
                continue
            for c in doc["spec"]["template"]["spec"]["containers"]:
                if c["name"] == container_name:
                    return c.get("imagePullPolicy")
    return "NOT_FOUND"


def _render(tmp_path: Path, discovery: Discovery, pull_policy: str = "if-not-present"):
    d = _k8s_deployment(pull_policy)
    secrets = ensure_secrets(
        d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s")
    )
    return render_k8s(d, discovery.components, discovery.apps, secrets)


class TestPullPolicy:
    def test_app_gets_always(self, tmp_path: Path, real_discovery: Discovery) -> None:
        """The regression: a WIP app (short image name, build_context=None)
        must get Always, not the k8s default."""
        tree = _render(tmp_path, real_discovery)
        assert _pull_policy_of(tree, "react-console") == "Always"

    def test_backend_service_still_always(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        tree = _render(tmp_path, real_discovery)
        assert _pull_policy_of(tree, "registry") == "Always"

    def test_external_image_stays_default(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """Fully-qualified external images (name contains '/') keep the k8s
        default — no imagePullPolicy key."""
        tree = _render(tmp_path, real_discovery)
        assert _pull_policy_of(tree, "mongodb") is None

    def test_pull_policy_always_escalates_external(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """spec.images.pull_policy: always forces Always even for external
        images — the field is now wired (was inert)."""
        tree = _render(tmp_path, real_discovery, pull_policy="always")
        assert _pull_policy_of(tree, "mongodb") == "Always"
        assert _pull_policy_of(tree, "react-console") == "Always"
