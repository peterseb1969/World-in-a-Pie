"""Regression coverage for CASE-864 — the `garage` object-storage module.

Garage replaces MinIO (whose images are no longer publicly pullable).
Everything here was shaped by live findings against Garage v2.1.0:
the SigV4 region is enforced (config pins us-east-1), presigned URLs
are SigV4-with-signed-Host (route carries upstream_host_header), the
image is shell-less (provisioning is deployer-driven via `garage
json-api`, secrets on stdin), and credentials are imported from
deployer-generated secrets with format constraints (GK + 12 hex bytes /
32 hex bytes).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from wip_deploy.apply import ApplyError, _provision_garage_if_active
from wip_deploy.config_gen.garage import (
    GARAGE_SECRET_NAMES,
    SECRET_KEY_ID,
    SECRET_KEY_SECRET,
    SECRET_RPC,
)
from wip_deploy.config_gen.spec_context import active_storage_module
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose
from wip_deploy.secrets import collect_required_secrets, ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(modules: list[str], secrets_dir: str = "/tmp/s") -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": modules},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in []],
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/x", tag="v1"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location=secrets_dir),
        ),
    )


def _secrets(tmp_path: Path, d: Deployment, disc: Discovery) -> ResolvedSecrets:
    return ensure_secrets(
        d, disc.components, disc.apps, FileSecretBackend(tmp_path / "secrets")
    )


class TestStorageModuleResolution:
    def test_garage_active_resolves(self, real_discovery: Discovery) -> None:
        # An unknown module name in the list is ignored by activation —
        # the retired minio entry in an old saved spec must not break
        # resolution (garage still wins; it is the only storage module).
        d = _deployment(["garage", "minio"])
        assert active_storage_module(d, real_discovery.components) == "garage"

    def test_neither_means_files_off(self, real_discovery: Discovery) -> None:
        d = _deployment(["mcp-server"])
        assert active_storage_module(d, real_discovery.components) is None


class TestSecrets:
    def test_garage_names_collected_only_when_active(
        self, real_discovery: Discovery
    ) -> None:
        on = collect_required_secrets(
            _deployment(["garage"]), real_discovery.components, real_discovery.apps
        )
        off = collect_required_secrets(
            _deployment(["mcp-server"]), real_discovery.components, real_discovery.apps
        )
        assert set(GARAGE_SECRET_NAMES) <= on
        assert not (set(GARAGE_SECRET_NAMES) & off)

    def test_format_constrained_generators(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        s = _secrets(tmp_path, _deployment(["garage"]), real_discovery)
        key_id = s.get(SECRET_KEY_ID)
        assert key_id.startswith("GK") and len(key_id) == 26
        int(key_id[2:], 16)  # 12 hex-encoded bytes
        assert len(s.get(SECRET_KEY_SECRET)) == 64
        int(s.get(SECRET_KEY_SECRET), 16)
        assert len(s.get(SECRET_RPC)) == 64


class TestComposeRender:
    def _tree(self, tmp_path: Path, disc: Discovery, modules: list[str]):
        d = _deployment(modules)
        return render_compose(d, disc.components, disc.apps, _secrets(tmp_path, d, disc))

    def test_garage_service_config_and_mount(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        tree = self._tree(tmp_path, real_discovery, ["garage"])
        files = {str(p): f for p, f in tree.files.items()}
        toml = files["config/garage/garage.toml"]
        assert toml.mode == 0o600
        assert 's3_region = "us-east-1"' in toml.content

        compose = yaml.safe_load(files["docker-compose.yaml"].content)
        svc = compose["services"]["garage"]
        assert "./config/garage/garage.toml:/etc/garage.toml:ro" in svc["volumes"]
        # Deliberately NO healthcheck: compose healthchecks are CMD-SHELL
        # and the image is shell-less — the provisioning pass does its
        # own readiness wait instead.
        assert "healthcheck" not in svc

    def test_document_store_env_follows_garage(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        tree = self._tree(tmp_path, real_discovery, ["garage"])
        compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        env = compose["services"]["document-store"]["environment"]
        assert env["WIP_FILE_STORAGE_ENDPOINT"] == "http://wip-garage:3900"
        assert env["WIP_FILE_STORAGE_ACCESS_KEY"] == "${GARAGE_KEY_ID}"
        assert env["WIP_FILE_STORAGE_SECRET_KEY"] == "${GARAGE_KEY_SECRET}"
        assert env["WIP_FILE_STORAGE_PUBLIC_ENDPOINT"].endswith("/garage")

    def test_caddy_route_rewrites_upstream_host(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        tree = self._tree(tmp_path, real_discovery, ["garage"])
        caddy = tree.files[Path("config/caddy/Caddyfile")].content
        block = caddy[caddy.index("handle_path /garage/*"):]
        block = block[: block.index("\n    }") + 6]
        assert "header_up Host {upstream_hostport}" in block


class TestProvisioning:
    """The apply-time pass, with subprocess mocked — the real-container
    behavior (fresh init, idempotent re-run, KeyAlreadyExists tolerance)
    was verified live against Garage v2.1.0 before this was written."""

    def _deployment_with_secrets(self, tmp_path: Path) -> Deployment:
        (tmp_path / SECRET_KEY_ID).write_text("GKaabbccddeeff001122334455")
        (tmp_path / SECRET_KEY_SECRET).write_text("a" * 64)
        return _deployment(["garage"], secrets_dir=str(tmp_path))

    def test_fresh_node_full_provisioning(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = self._deployment_with_secrets(tmp_path)
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(argv, cwd=None, input=None, capture_output=True, text=True, check=True):
            calls.append((argv, input))
            tail = argv[argv.index("/garage") + 1:]
            out, code = "{}", 0
            if tail[:2] == ["json-api", "GetClusterHealth"]:
                pass
            elif tail[:2] == ["json-api", "GetClusterLayout"]:
                out = json.dumps({"roles": [], "version": 0})
            elif tail[:2] == ["json-api", "GetBucketInfo"]:
                # First lookup misses; after CreateBucket it resolves.
                created = any(
                    a[0][a[0].index("/garage") + 2] == "CreateBucket"
                    for a in calls[:-1]
                    if "/garage" in a[0] and len(a[0]) > a[0].index("/garage") + 2
                )
                if created:
                    out = json.dumps({"id": "bkt1"})
                else:
                    code = 1
            import subprocess as sp
            if check and code != 0:
                raise sp.CalledProcessError(code, argv)
            return sp.CompletedProcess(argv, code, stdout=out, stderr="")

        with patch("wip_deploy.apply.subprocess.run", side_effect=fake_run):
            _provision_garage_if_active(
                d, real_discovery.components, exec_prefix=["x"], cwd=None
            )

        flat = [" ".join(a) for a, _ in calls]
        assert any("layout assign" in f for f in flat)
        assert any("layout apply --version 1" in f for f in flat)
        assert any("ImportKey" in f for f in flat)
        assert any("CreateBucket" in f for f in flat)
        assert any("AllowBucketKey" in f for f in flat)
        # Secrets travel on stdin, never argv.
        for argv, _ in calls:
            assert "a" * 64 not in " ".join(argv)

    def test_existing_layout_and_key_are_tolerated(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = self._deployment_with_secrets(tmp_path)
        calls: list[list[str]] = []

        def fake_run(argv, cwd=None, input=None, capture_output=True, text=True, check=True):
            calls.append(argv)
            tail = argv[argv.index("/garage") + 1:]
            import subprocess as sp
            if tail[:2] == ["json-api", "GetClusterLayout"]:
                return sp.CompletedProcess(
                    argv, 0, stdout=json.dumps({"roles": [{"id": "n1"}], "version": 1}),
                    stderr="",
                )
            if tail[:2] == ["json-api", "ImportKey"]:
                if check:
                    raise AssertionError("ImportKey must run with check=False")
                return sp.CompletedProcess(
                    argv, 1, stdout="", stderr="Error: KeyAlreadyExists (409)",
                )
            if tail[:2] == ["json-api", "GetBucketInfo"]:
                return sp.CompletedProcess(
                    argv, 0, stdout=json.dumps({"id": "bkt1"}), stderr="",
                )
            return sp.CompletedProcess(argv, 0, stdout="{}", stderr="")

        with patch("wip_deploy.apply.subprocess.run", side_effect=fake_run):
            _provision_garage_if_active(
                d, real_discovery.components, exec_prefix=["x"], cwd=None
            )
        flat = [" ".join(a) for a in calls]
        assert not any("layout assign" in f for f in flat)

    def test_unknown_import_error_raises(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = self._deployment_with_secrets(tmp_path)

        def fake_run(argv, cwd=None, input=None, capture_output=True, text=True, check=True):
            import subprocess as sp
            tail = argv[argv.index("/garage") + 1:]
            if tail[:2] == ["json-api", "GetClusterLayout"]:
                return sp.CompletedProcess(
                    argv, 0, stdout=json.dumps({"roles": [{"id": "n1"}], "version": 1}),
                    stderr="",
                )
            if tail[:2] == ["json-api", "ImportKey"]:
                return sp.CompletedProcess(argv, 1, stdout="", stderr="boom")
            return sp.CompletedProcess(argv, 0, stdout="{}", stderr="")

        with (
            patch("wip_deploy.apply.subprocess.run", side_effect=fake_run),
            pytest.raises(ApplyError, match="key import failed"),
        ):
            _provision_garage_if_active(
                d, real_discovery.components, exec_prefix=["x"], cwd=None
            )

    def test_inactive_garage_is_a_noop(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _deployment(["minio"], secrets_dir=str(tmp_path))
        with patch("wip_deploy.apply.subprocess.run") as run:
            _provision_garage_if_active(
                d, real_discovery.components, exec_prefix=["x"], cwd=None
            )
        assert run.call_count == 0
