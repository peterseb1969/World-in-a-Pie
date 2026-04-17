"""Tests for render_k8s — the Kubernetes renderer.

Same pattern as test_compose: real manifests + synthetic Deployment,
verify the shape of the rendered output via yaml.safe_load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
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

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _k8s_deployment(
    *,
    namespace: str = "wip-test",
    modules: list[str] | None = None,
    apps: list[str] | None = None,
) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="k8s-test"),
        spec=DeploymentSpec(
            target="k8s",
            modules={"optional": modules or ["console"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in (apps or [])],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip-kubi.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v1.1.0"),
            platform=PlatformSpec(k8s=K8sPlatform(namespace=namespace)),
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


# ────────────────────────────────────────────────────────────────────
# Tree shape
# ────────────────────────────────────────────────────────────────────


class TestTreeShape:
    def test_standard_emits_expected_files(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        paths = {str(p) for p in tree.paths()}
        assert "namespace.yaml" in paths
        assert "secrets.yaml" in paths
        assert "configmaps.yaml" in paths
        assert "ingress.yaml" in paths
        assert "services/registry.yaml" in paths

    def test_secrets_have_0600_mode(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        assert tree.files[Path("secrets.yaml")].mode == 0o600


# ────────────────────────────────────────────────────────────────────
# Namespace
# ────────────────────────────────────────────────────────────────────


class TestNamespace:
    def test_uses_spec_namespace(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment(namespace="wip-stable")
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        ns = yaml.safe_load(tree.files[Path("namespace.yaml")].content)
        assert ns["metadata"]["name"] == "wip-stable"


# ────────────────────────────────────────────────────────────────────
# Services
# ────────────────────────────────────────────────────────────────────


class TestComponents:
    def _parse(
        self, tmp_path: Path, discovery: Discovery, path: str, **overrides: object
    ) -> list[dict]:  # type: ignore[type-arg]
        d = _k8s_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_k8s(d, discovery.components, discovery.apps, s)
        content = tree.files[Path(path)].content
        return list(yaml.safe_load_all(content))

    def test_registry_is_deployment_not_statefulset(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        kinds = {d["kind"] for d in docs}
        assert "Deployment" in kinds
        assert "StatefulSet" not in kinds
        assert "Service" in kinds

    def test_mongodb_is_statefulset_with_pvc(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(
            tmp_path, real_discovery, "infrastructure/mongodb.yaml"
        )
        kinds = {d["kind"] for d in docs}
        assert "StatefulSet" in kinds
        assert "PersistentVolumeClaim" in kinds

    def test_image_ref_uses_registry_and_tag(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        assert image == "ghcr.io/peterseb1969/registry:v1.1.0"

    def test_env_secrets_use_secretkeyref(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        api_key = next(e for e in env if e["name"] == "MASTER_API_KEY")
        assert api_key["valueFrom"]["secretKeyRef"]["name"] == "wip-secrets"
        assert api_key["valueFrom"]["secretKeyRef"]["key"] == "api-key"

    def test_healthcheck_renders_readiness_and_liveness(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert "readinessProbe" in container
        assert "livenessProbe" in container
        assert container["readinessProbe"]["httpGet"]["path"] == "/health"

    def test_explicit_command_rendered(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        cmd = deployment["spec"]["template"]["spec"]["containers"][0]["command"]
        assert cmd[0] == "uvicorn"
        assert "registry.main:app" in cmd

    def test_namespace_applied_to_all_resources(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(
            tmp_path, real_discovery, "services/registry.yaml",
            namespace="wip-stable",
        )
        for doc in docs:
            assert doc["metadata"]["namespace"] == "wip-stable"


# ────────────────────────────────────────────────────────────────────
# Ingress
# ────────────────────────────────────────────────────────────────────


class TestIngress:
    def _render_ingress(
        self, tmp_path: Path, discovery: Discovery, **overrides: object
    ) -> list[dict]:  # type: ignore[type-arg]
        d = _k8s_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_k8s(d, discovery.components, discovery.apps, s)
        return list(yaml.safe_load_all(
            tree.files[Path("ingress.yaml")].content
        ))

    def test_api_routes_no_auth_url(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._render_ingress(tmp_path, real_discovery)
        main_ingress = next(
            d for d in docs if d["metadata"]["name"] == "wip-ingress"
        )
        annotations = main_ingress["metadata"].get("annotations", {})
        assert "nginx.ingress.kubernetes.io/auth-url" not in annotations

    def test_app_ingress_has_auth_url(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._render_ingress(
            tmp_path, real_discovery, apps=["react-console"]
        )
        rc_ingress = next(
            d for d in docs if d["metadata"]["name"] == "react-console-ingress"
        )
        annotations = rc_ingress["metadata"]["annotations"]
        assert "nginx.ingress.kubernetes.io/auth-url" in annotations
        assert "wip-auth-gateway" in annotations[
            "nginx.ingress.kubernetes.io/auth-url"
        ]

    def test_tls_configured(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._render_ingress(tmp_path, real_discovery)
        main_ingress = docs[0]
        tls = main_ingress["spec"]["tls"]
        assert tls[0]["hosts"] == ["wip-kubi.local"]
        assert tls[0]["secretName"] == "wip-tls"


# ────────────────────────────────────────────────────────────────────
# Inactive components
# ────────────────────────────────────────────────────────────────────


class TestActivation:
    def test_inactive_components_not_rendered(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        paths = {str(p) for p in tree.paths()}
        # reporting-sync and its deps (postgres, nats) not in standard
        assert not any("reporting-sync" in p for p in paths)
        assert not any("postgres" in p for p in paths)
