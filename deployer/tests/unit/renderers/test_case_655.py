"""Regression coverage for CASE-655 — spec-declared config-file API keys.

`auth.api_keys` renders into wip-auth's WIP_AUTH_API_KEYS_FILE mechanism:
one secret per key in the secret backend (generated on first apply,
stable thereafter), an `api-keys.json` in the rendered tree (0600),
mounted into exactly the components whose manifest declares the env var.
Spec-declared keys therefore survive a MongoDB wipe/restore — the loss
mode that killed the out-of-band `web-yac` runtime key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from wip_deploy.config_gen.api_keys import (
    API_KEYS_CONTAINER_PATH,
    API_KEYS_RENDER_PATH,
    generate_api_keys_json,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple, render_k8s
from wip_deploy.secrets import collect_required_secrets, ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.spec.deployment import SpecAPIKey

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()

WEB_YAC = SpecAPIKey(
    name="web-yac",
    namespaces=["library", "kb"],
    grants={"kb": "write"},
    owner="system:web-yac",
)

# Manifests that declare WIP_AUTH_API_KEYS_FILE today — the wip-auth
# importers. Kept in lockstep with the component manifests; the
# consumer-set test below verifies against the real manifests.
EXPECTED_CONSUMERS = {
    "registry",
    "def-store",
    "template-store",
    "document-store",
    "reporting-sync",
    "auth-gateway",
    "mcp-server",
}


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _compose_deployment(*, api_keys: list[SpecAPIKey] | None = None) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": ["mcp-server", "reporting-sync"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True, api_keys=api_keys or []),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/test", tag="test"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
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
# Spec validation
# ────────────────────────────────────────────────────────────────────


class TestSpecValidation:
    def test_grants_outside_namespaces_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside its"):
            SpecAPIKey(name="k", namespaces=["library"], grants={"kb": "write"})

    def test_duplicate_key_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            AuthSpec(
                mode="oidc",
                gateway=True,
                api_keys=[
                    SpecAPIKey(name="k", namespaces=["a"]),
                    SpecAPIKey(name="k", namespaces=["b"]),
                ],
            )

    def test_name_pattern_enforced(self) -> None:
        with pytest.raises(ValueError):
            SpecAPIKey(name="Bad_Name", namespaces=["a"])

    def test_secret_name_convention(self) -> None:
        assert WEB_YAC.secret_name == "web-yac-api-key"


# ────────────────────────────────────────────────────────────────────
# Secrets collection
# ────────────────────────────────────────────────────────────────────


class TestSecretsCollection:
    def test_key_secret_collected_and_generated(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment(api_keys=[WEB_YAC])
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert "web-yac-api-key" in names
        secrets = _secrets(tmp_path, d, real_discovery)
        assert len(secrets.get("web-yac-api-key")) >= 16

    def test_no_keys_no_extra_secret(
        self, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        names = collect_required_secrets(
            d, real_discovery.components, real_discovery.apps
        )
        assert not any(n.startswith("web-yac") for n in names)


# ────────────────────────────────────────────────────────────────────
# Rendered JSON content
# ────────────────────────────────────────────────────────────────────


class TestGeneratedJson:
    def test_entry_carries_key_scope_and_grants(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment(api_keys=[WEB_YAC])
        secrets = _secrets(tmp_path, d, real_discovery)
        content = generate_api_keys_json(d, secrets)
        assert content is not None
        doc = json.loads(content)
        [entry] = doc["keys"]
        assert entry["name"] == "web-yac"
        assert entry["key"] == secrets.get("web-yac-api-key")
        assert entry["namespaces"] == ["library", "kb"]
        assert entry["grants"] == {"kb": "write"}

    def test_none_when_no_keys(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        secrets = _secrets(tmp_path, d, real_discovery)
        assert generate_api_keys_json(d, secrets) is None


# ────────────────────────────────────────────────────────────────────
# Compose render
# ────────────────────────────────────────────────────────────────────


class TestComposeRender:
    def test_file_rendered_0600_and_mounted_on_consumers(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment(api_keys=[WEB_YAC])
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(
            d, real_discovery.components, real_discovery.apps, secrets
        )

        entry = tree.files[Path(API_KEYS_RENDER_PATH)]
        assert entry.mode == 0o600
        assert "web-yac" in entry.content

        compose = yaml.safe_load(
            tree.files[Path("docker-compose.yaml")].content
        )
        services = compose["services"]
        mount = f"./{API_KEYS_RENDER_PATH}:{API_KEYS_CONTAINER_PATH}:ro"
        active_consumers = EXPECTED_CONSUMERS & set(services)
        assert active_consumers  # the fixture activates several
        for name in active_consumers:
            svc = services[name]
            assert mount in svc.get("volumes", []), name
            assert (
                svc["environment"]["WIP_AUTH_API_KEYS_FILE"]
                == API_KEYS_CONTAINER_PATH
            ), name
        # A non-consumer infra service gets neither.
        assert mount not in services["mongodb"].get("volumes", [])

    def test_no_keys_no_file_no_mount_empty_env(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(
            d, real_discovery.components, real_discovery.apps, secrets
        )
        assert Path(API_KEYS_RENDER_PATH) not in tree.files
        compose = yaml.safe_load(
            tree.files[Path("docker-compose.yaml")].content
        )
        registry = compose["services"]["registry"]
        assert registry["environment"]["WIP_AUTH_API_KEYS_FILE"] == ""
        assert not any(
            "api-keys.json" in v for v in registry.get("volumes", [])
        )

    def test_consumer_set_matches_wip_auth_importers(
        self, real_discovery: Discovery
    ) -> None:
        """The manifests that declare the env var are exactly the
        components whose source imports wip_auth — the declaration must
        not drift from the actual consumer set."""
        from wip_deploy.config_gen.api_keys import declares_api_keys_file

        declaring = {
            c.metadata.name
            for c in real_discovery.components
            if declares_api_keys_file(c)
        }
        assert declaring == EXPECTED_CONSUMERS
        importers = set()
        for c in real_discovery.components:
            src = REPO_ROOT / "components" / c.metadata.name / "src"
            if not src.is_dir():
                continue
            if any(
                "wip_auth" in p.read_text()
                for p in src.rglob("*.py")
            ):
                importers.add(c.metadata.name)
        assert declaring == importers


# ────────────────────────────────────────────────────────────────────
# Dev render
# ────────────────────────────────────────────────────────────────────


class TestDevRender:
    def test_file_and_mount_in_dev(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = Deployment(
            metadata=DeploymentMetadata(name="dev-t"),
            spec=DeploymentSpec(
                target="dev",
                modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
                auth=AuthSpec(
                    mode="oidc", gateway=True, api_keys=[WEB_YAC]
                ),
                network=NetworkSpec(hostname="localhost"),
                images=ImagesSpec(tag="dev"),
                platform=PlatformSpec(
                    dev=DevPlatform(mode="simple", source_mount=True)
                ),
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
            ),
        )
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_dev_simple(
            d,
            real_discovery.components,
            real_discovery.apps,
            secrets,
            repo_root=REPO_ROOT,
        )
        assert tree.files[Path(API_KEYS_RENDER_PATH)].mode == 0o600
        compose = yaml.safe_load(
            tree.files[Path("docker-compose.yaml")].content
        )
        registry = compose["services"]["registry"]
        mount = f"./{API_KEYS_RENDER_PATH}:{API_KEYS_CONTAINER_PATH}:ro"
        assert mount in registry["volumes"]


# ────────────────────────────────────────────────────────────────────
# K8s render
# ────────────────────────────────────────────────────────────────────


class TestK8sRender:
    def _k8s_deployment(self, api_keys: list[SpecAPIKey]) -> Deployment:
        return Deployment(
            metadata=DeploymentMetadata(name="t"),
            spec=DeploymentSpec(
                target="k8s",
                modules={"optional": ["mcp-server"]},  # type: ignore[arg-type]
                auth=AuthSpec(
                    mode="oidc", gateway=True, api_keys=api_keys
                ),
                network=NetworkSpec(
                    hostname="wip-kubi.local", https_port=443, http_port=80
                ),
                images=ImagesSpec(registry="ghcr.io/test", tag="test"),
                platform=PlatformSpec(k8s=K8sPlatform()),
                secrets=SecretsSpec(backend="k8s-secret"),
            ),
        )

    def test_secret_manifest_and_subpath_mount(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = self._k8s_deployment([WEB_YAC])
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(
            d, real_discovery.components, real_discovery.apps, secrets
        )

        secret_doc = yaml.safe_load(
            tree.files[Path("api-keys-secret.yaml")].content
        )
        assert secret_doc["metadata"]["name"] == "wip-api-keys-config"
        assert "web-yac" in secret_doc["stringData"]["api-keys.json"]

        registry_docs = list(
            yaml.safe_load_all(tree.files[Path("services/registry.yaml")].content)
        )
        workload = next(
            doc for doc in registry_docs if doc["kind"] in ("Deployment", "StatefulSet")
        )
        container = workload["spec"]["template"]["spec"]["containers"][0]
        mounts = container.get("volumeMounts", [])
        assert any(
            m["mountPath"] == API_KEYS_CONTAINER_PATH
            and m.get("subPath") == "api-keys.json"
            for m in mounts
        )

    def test_no_keys_no_secret_manifest(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = self._k8s_deployment([])
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(
            d, real_discovery.components, real_discovery.apps, secrets
        )
        assert Path("api-keys-secret.yaml") not in tree.files
