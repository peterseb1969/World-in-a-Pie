"""Regression coverage for CASE-655 — spec-declared config-file API keys.

`auth.api_keys` renders into the install's key file: one secret per key in
the secret backend (generated on first apply, stable thereafter), an
`api-keys.json` in the rendered tree, mounted (through a directory of its
own) into exactly the components whose manifest declares it — the Registry,
as WIP_REGISTRY_KEY_FILE. The Registry imports the keys into its own records
and re-imports them at every start, so spec-declared keys survive a MongoDB
wipe/restore — the loss mode that killed the out-of-band `web-yac` runtime
key — and no other container holds the plaintext.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from wip_deploy.config_gen.api_keys import (
    API_KEYS_CONTAINER_DIR,
    API_KEYS_CONTAINER_PATH,
    API_KEYS_RENDER_DIR,
    API_KEYS_RENDER_PATH,
    CANARY_NAMESPACE,
    KEY_FILE_CONTAINER_DIR,
    KEY_FILE_RENDER_DIR,
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
from wip_deploy.spec.deployment import CANARY_KEY_NAME, SpecAPIKey

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()

WEB_YAC = SpecAPIKey(
    name="web-yac",
    namespaces=["library", "kb"],
    grants={"kb": "write"},
    owner="system:web-yac",
)

# The only manifest that reads the key file: the Registry, which imports it.
# Every other wip-auth importer resolves keys through the Registry. The
# consumer-set test below verifies against the real manifests.
EXPECTED_CONSUMERS = {"registry"}


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _compose_deployment(*, api_keys: list[SpecAPIKey] | None = None) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": ["mcp-server", "reporting-sync", "app-edge"]},  # type: ignore[arg-type]
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
        doc = json.loads(content)
        # With the app edge enabled, its key comes before the canary.
        entry, edge, canary = doc["keys"]
        assert edge["name"] == "wip-app-edge" and edge["groups"] == ["wip-app-edge"]
        assert canary["name"] == CANARY_KEY_NAME
        assert entry["name"] == "web-yac"
        assert entry["key"] == secrets.get("web-yac-api-key")
        assert entry["namespaces"] == ["library", "kb"]
        assert entry["grants"] == {"kb": "write"}

    def test_only_the_deployers_own_keys_when_none_declared(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        secrets = _secrets(tmp_path, d, real_discovery)
        edge, canary = json.loads(generate_api_keys_json(d, secrets))["keys"]
        # The app edge's key: reads attachments, sees no namespace.
        assert edge["name"] == "wip-app-edge"
        assert edge["key"] == secrets.get("wip-app-edge-api-key")
        assert edge["groups"] == ["wip-app-edge"]
        assert edge["namespaces"] == [CANARY_NAMESPACE]
        assert edge["grants"] == {}
        assert canary["name"] == CANARY_KEY_NAME
        assert canary["key"] == secrets.get(f"{CANARY_KEY_NAME}-api-key")
        assert canary["groups"] == []
        assert canary["grants"] == {}
        assert canary["namespaces"] == [CANARY_NAMESPACE]


# ────────────────────────────────────────────────────────────────────
# Compose render
# ────────────────────────────────────────────────────────────────────


class TestComposeRender:
    def test_file_rendered_container_readable_and_mounted_on_consumers(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment(api_keys=[WEB_YAC])
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(
            d, real_discovery.components, real_discovery.apps, secrets
        )

        entry = tree.files[Path(API_KEYS_RENDER_PATH)]
        # Readable by registry's non-root user; the 0700 install directory
        # keeps other host users out (test_install_dir_permissions.py).
        assert entry.mode == 0o644
        assert "web-yac" in entry.content

        compose = yaml.safe_load(
            tree.files[Path("docker-compose.yaml")].content
        )
        services = compose["services"]
        mount = f"./{KEY_FILE_RENDER_DIR}:{KEY_FILE_CONTAINER_DIR}:ro"
        registry = services["registry"]
        assert mount in registry.get("volumes", [])
        assert registry["environment"]["WIP_REGISTRY_KEY_FILE"] == API_KEYS_CONTAINER_PATH
        assert "WIP_AUTH_API_KEYS_FILE" not in registry["environment"]
        # No other service holds the key file, though several still mount
        # the auth config directory (federated groups, reservations).
        for name, svc in services.items():
            if name == "registry":
                continue
            assert mount not in svc.get("volumes", []), name
            assert "WIP_AUTH_API_KEYS_FILE" not in (svc.get("environment") or {}), name
        assert any(
            f"./{API_KEYS_RENDER_DIR}:{API_KEYS_CONTAINER_DIR}:ro" in svc.get("volumes", [])
            for name, svc in services.items() if name != "registry"
        )
        assert not API_KEYS_RENDER_PATH.startswith(API_KEYS_RENDER_DIR + "/")

    def test_no_declared_keys_still_mounts_the_canary(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _compose_deployment()
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_compose(
            d, real_discovery.components, real_discovery.apps, secrets
        )
        assert Path(API_KEYS_RENDER_PATH) in tree.files
        compose = yaml.safe_load(
            tree.files[Path("docker-compose.yaml")].content
        )
        registry = compose["services"]["registry"]
        assert registry["environment"]["WIP_REGISTRY_KEY_FILE"] == API_KEYS_CONTAINER_PATH
        assert f"./{KEY_FILE_RENDER_DIR}:{KEY_FILE_CONTAINER_DIR}:ro" in registry.get("volumes", [])

    def test_consumer_set_matches_wip_auth_importers(
        self, real_discovery: Discovery
    ) -> None:
        """Exactly the Registry declares the key file. Every other component
        whose source imports wip_auth resolves keys through the Registry and
        must not declare it: the plaintext reaches no other container."""
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
            # Real imports only: auth-gateway and mcp-server mention wip_auth
            # in comments, which once kept them declared for a file they
            # never read (and restarted them on every declared-key change).
            if any(
                re.search(r"^\s*(from|import) wip_auth\b", p.read_text(), re.M)
                for p in src.rglob("*.py")
            ):
                importers.add(c.metadata.name)
        assert declaring <= importers
        assert len(importers) > 1  # the scan still finds the other services


    def test_an_upgrade_removes_the_key_file_from_the_auth_directory(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """A render from before the key file moved wrote it into the auth
        config directory, which every federated-groups reader mounts.
        Writing a current render over such an install removes it, so no
        other container keeps the old plaintexts."""
        d = _compose_deployment(api_keys=[WEB_YAC])
        secrets = _secrets(tmp_path, d, real_discovery)
        install = tmp_path / "install"
        stale = install / "config" / "auth" / "api-keys.json"
        stale.parent.mkdir(parents=True)
        stale.write_text('{"keys": [{"name": "old", "key": "old-plaintext"}]}')

        render_compose(d, real_discovery.components, real_discovery.apps, secrets).write(install)

        assert not stale.exists()
        assert (install / API_KEYS_RENDER_PATH).exists()
        assert (install / "config" / "auth" / "federated-groups.json").exists()


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
        # Readable by registry's non-root user; the 0700 install directory
        # keeps other host users out (test_install_dir_permissions.py).
        assert tree.files[Path(API_KEYS_RENDER_PATH)].mode == 0o644
        compose = yaml.safe_load(
            tree.files[Path("docker-compose.yaml")].content
        )
        registry = compose["services"]["registry"]
        assert f"./{KEY_FILE_RENDER_DIR}:{KEY_FILE_CONTAINER_DIR}:ro" in registry["volumes"]


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

        docs = {
            d["metadata"]["name"]: d
            for d in yaml.safe_load_all(tree.files[Path("api-keys-secret.yaml")].content)
        }
        # The key file in a Secret of its own; the auth config Secret holds none.
        assert "web-yac" in docs["wip-key-file"]["stringData"]["api-keys.json"]
        assert "api-keys.json" not in docs["wip-auth-config"]["stringData"]
        # The old Secret, which carried the key file, is left out of the
        # render, so the apply's prune deletes it.
        assert "wip-api-keys-config" not in docs

        registry_docs = list(
            yaml.safe_load_all(tree.files[Path("services/registry.yaml")].content)
        )
        workload = next(
            doc for doc in registry_docs if doc["kind"] in ("Deployment", "StatefulSet")
        )
        container = workload["spec"]["template"]["spec"]["containers"][0]
        mounts = container.get("volumeMounts", [])
        # Mounted through its directory, never subPath: a running pod must see
        # the file change so the Registry can re-import it (CASE-884).
        assert any(
            m["mountPath"] == KEY_FILE_CONTAINER_DIR and "subPath" not in m
            for m in mounts
        )
        # No other workload mounts the key file's Secret.
        for path, entry in tree.files.items():
            if str(path).startswith("services/") and path.name != "registry.yaml":
                assert "wip-key-file" not in entry.content, path

    def test_no_declared_keys_still_renders_the_canary_secret(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = self._k8s_deployment([])
        secrets = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(
            d, real_discovery.components, real_discovery.apps, secrets
        )
        assert Path("api-keys-secret.yaml") in tree.files
