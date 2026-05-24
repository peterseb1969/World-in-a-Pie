"""Tests for nuke.py.

Covers the dry-runnable logic (resource classification) and the
orchestration shape (how it invokes podman). Uses `monkeypatch` to stub
subprocess so tests don't need a real podman install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wip_deploy.nuke import (
    NukeError,
    _images_in_compose,
    _looks_like_wip_image,
    _looks_like_wip_pod,
    _looks_like_wip_volume,
    _networks_in_compose,
    has_k8s_install,
    nuke_install_dir,
    nuke_k8s_install_dir,
    nuke_purge_all,
)

# ────────────────────────────────────────────────────────────────────
# Resource classification
# ────────────────────────────────────────────────────────────────────


class TestPodClassification:
    @pytest.mark.parametrize(
        "name",
        [
            "pod_wip-demo",
            "pod_wip-deploy",
            "pod_wip-foo-bar",
            "pod_docker-compose",
            "pod_registry",
            "pod_def-store",
            "pod_document-store",
            "pod_auth-gateway",
        ],
    )
    def test_matches(self, name: str) -> None:
        assert _looks_like_wip_pod(name) is True

    @pytest.mark.parametrize(
        "name",
        ["pod_unrelated", "pod_redis", "random-pod", "", "wip-only-no-prefix"],
    )
    def test_does_not_match(self, name: str) -> None:
        assert _looks_like_wip_pod(name) is False


class TestVolumeClassification:
    @pytest.mark.parametrize(
        "name",
        [
            "wip-mongo-data",
            "wip-postgres-data",
            "wip-demo_wip-mongo-data",
            "default_wip-caddy-data",
            "wip-deploy_wip-dex-data",
        ],
    )
    def test_matches(self, name: str) -> None:
        assert _looks_like_wip_volume(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "redis-data",
            "690c8d3431d01aa777356304af8ead3229a14087c6fb36b4e97294a5ac02babd",
            "myapp_data",
        ],
    )
    def test_does_not_match(self, name: str) -> None:
        assert _looks_like_wip_volume(name) is False


# ────────────────────────────────────────────────────────────────────
# nuke_install_dir — error paths
# ────────────────────────────────────────────────────────────────────


class TestNukeInstallDirErrors:
    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(NukeError, match="does not exist"):
            nuke_install_dir(tmp_path / "nosuch")

    def test_missing_compose_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        with pytest.raises(NukeError, match="no docker-compose"):
            nuke_install_dir(tmp_path / "empty")


# ────────────────────────────────────────────────────────────────────
# nuke_install_dir — success path with stubbed subprocess
# ────────────────────────────────────────────────────────────────────


class TestNukeInstallDirSuccess:
    def _make_install_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "install"
        d.mkdir()
        (d / "docker-compose.yaml").write_text("services: {}\n")
        return d

    def test_basic_down_runs_compose_without_v(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, check):  # type: ignore[no-untyped-def]
            calls.append(cmd)
            import subprocess

            return subprocess.CompletedProcess(cmd, returncode=0)

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: "/usr/bin/podman-compose")

        install = self._make_install_dir(tmp_path)
        report = nuke_install_dir(install, remove_data=False)

        assert report.compose_down_ran is True
        # No -v flag
        assert "-v" not in calls[0]

    def test_remove_data_adds_minus_v(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, check):  # type: ignore[no-untyped-def]
            calls.append(cmd)
            import subprocess

            return subprocess.CompletedProcess(cmd, returncode=0)

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: "/usr/bin/podman-compose")

        install = self._make_install_dir(tmp_path)
        nuke_install_dir(install, remove_data=True)
        assert "-v" in calls[0]

    def test_remove_secrets_removes_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "wip_deploy.nuke.subprocess.run",
            lambda *a, **kw: __import__("subprocess").CompletedProcess(a, returncode=0),
        )
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: "/usr/bin/podman-compose")

        install = self._make_install_dir(tmp_path)
        secrets = tmp_path / "secrets"
        secrets.mkdir()
        (secrets / "api-key").write_text("value")

        report = nuke_install_dir(
            install, remove_secrets=True, secrets_location=secrets
        )
        assert not secrets.exists()
        assert report.secrets_dir_removed == secrets

    def test_prefers_production_compose_file_when_primary_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v1 installs have docker-compose.production.yml, not
        docker-compose.yaml."""
        captured: list[list[str]] = []

        def fake_run(cmd, cwd, check):  # type: ignore[no-untyped-def]
            captured.append(cmd)
            import subprocess

            return subprocess.CompletedProcess(cmd, returncode=0)

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: "/usr/bin/podman-compose")

        install = tmp_path / "v1install"
        install.mkdir()
        (install / "docker-compose.production.yml").write_text("services: {}\n")

        nuke_install_dir(install)
        # -f flag is followed by the filename
        cmd = captured[0]
        idx = cmd.index("-f")
        assert cmd[idx + 1] == "docker-compose.production.yml"


# ────────────────────────────────────────────────────────────────────
# nuke_purge_all — with stubbed podman
# ────────────────────────────────────────────────────────────────────


class TestPurgeAll:
    def test_dry_run_does_not_mutate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run reports what would be removed but never invokes a
        destructive podman command."""
        call_log: list[tuple[str, ...]] = []

        def fake_ls_names(args):  # type: ignore[no-untyped-def]
            if args[0] == "ps":
                return ["wip-mongodb", "wip-registry"]
            if args[0] == "pod":
                return ["pod_docker-compose", "pod_other", "pod_wip-demo"]
            if args[0] == "volume":
                return ["wip-mongo-data", "some-anonymous"]
            return []

        def fake_podman(args):  # type: ignore[no-untyped-def]
            # Anything that's not ls would be a mutation — record.
            call_log.append(tuple(args))

        monkeypatch.setattr("wip_deploy.nuke._podman_ls_names", fake_ls_names)
        monkeypatch.setattr("wip_deploy.nuke._podman", fake_podman)

        report = nuke_purge_all(remove_data=True, dry_run=True)

        # Report: containers + pods (wip-ish) + volumes (wip-ish)
        assert report.containers_removed == ["wip-mongodb", "wip-registry"]
        assert set(report.pods_removed) == {"pod_docker-compose", "pod_wip-demo"}
        assert report.volumes_removed == ["wip-mongo-data"]
        # No mutating calls happened
        assert call_log == []

    def test_live_run_invokes_destructive_commands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_log: list[tuple[str, ...]] = []

        def fake_ls_names(args):  # type: ignore[no-untyped-def]
            if args[0] == "ps":
                return ["wip-x"]
            if args[0] == "pod":
                return ["pod_wip-y"]
            return []

        def fake_podman(args):  # type: ignore[no-untyped-def]
            call_log.append(tuple(args))

        monkeypatch.setattr("wip_deploy.nuke._podman_ls_names", fake_ls_names)
        monkeypatch.setattr("wip_deploy.nuke._podman", fake_podman)

        nuke_purge_all(remove_data=False)

        # Exactly two destructive calls: rm containers, pod rm
        cmds = [c[0] for c in call_log]
        assert cmds == ["rm", "pod"]

    def test_no_remove_data_skips_volume_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queried: list[str] = []

        def fake_ls_names(args):  # type: ignore[no-untyped-def]
            queried.append(args[0])
            return []

        monkeypatch.setattr("wip_deploy.nuke._podman_ls_names", fake_ls_names)
        monkeypatch.setattr("wip_deploy.nuke._podman", lambda args: None)

        nuke_purge_all(remove_data=False)

        # ps, pod, network scanned; volume not (no --remove-data)
        assert "ps" in queried
        assert "pod" in queried
        assert "network" in queried
        assert "volume" not in queried


# ────────────────────────────────────────────────────────────────────
# purge-all + --remove-secrets (CASE-387)
#
# The bug: `nuke --purge-all --remove-secrets` silently dropped the
# secrets removal, so a surviving secrets/api-key made the next install
# reuse the same master key. purge-all is host-wide, so its secrets
# removal sweeps every install's secrets/ dir under the install root.
# ────────────────────────────────────────────────────────────────────


class TestPurgeAllRemoveSecrets:
    @staticmethod
    def _stub_podman(monkeypatch: pytest.MonkeyPatch) -> None:
        """No containers/pods/volumes/images on the host — isolate the
        secrets-removal behaviour from the resource sweep."""
        monkeypatch.setattr("wip_deploy.nuke._podman_ls_names", lambda args: [])
        monkeypatch.setattr("wip_deploy.nuke._podman", lambda args: None)

    @staticmethod
    def _make_install_root(tmp_path: Path, names: list[str]) -> Path:
        """Build a fake ~/.wip-deploy/ with one install dir per name, each
        carrying a secrets/api-key."""
        root = tmp_path / ".wip-deploy"
        root.mkdir()
        for name in names:
            secrets = root / name / "secrets"
            secrets.mkdir(parents=True)
            (secrets / "api-key").write_text("master-key-value")
        return root

    def test_removes_secrets_dir_for_every_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_podman(monkeypatch)
        root = self._make_install_root(tmp_path, ["default", "wip-dev-local"])

        report = nuke_purge_all(remove_secrets=True, install_root=root)

        assert not (root / "default" / "secrets").exists()
        assert not (root / "wip-dev-local" / "secrets").exists()
        assert sorted(report.secrets_dirs_removed) == [
            root / "default" / "secrets",
            root / "wip-dev-local" / "secrets",
        ]

    def test_without_remove_secrets_leaves_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_podman(monkeypatch)
        root = self._make_install_root(tmp_path, ["default"])

        report = nuke_purge_all(remove_secrets=False, install_root=root)

        assert (root / "default" / "secrets").exists()
        assert report.secrets_dirs_removed == []

    def test_dry_run_reports_but_preserves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_podman(monkeypatch)
        root = self._make_install_root(tmp_path, ["default"])

        report = nuke_purge_all(
            remove_secrets=True, install_root=root, dry_run=True
        )

        # Reported as would-remove, but the dir survives the dry run.
        assert (root / "default" / "secrets").exists()
        assert report.secrets_dirs_removed == [root / "default" / "secrets"]

    def test_install_without_secrets_dir_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_podman(monkeypatch)
        root = tmp_path / ".wip-deploy"
        root.mkdir()
        # An install dir with no secrets/ subdir (e.g. a k8s-secrets install).
        (root / "no-secrets-install").mkdir()
        secrets = root / "has-secrets" / "secrets"
        secrets.mkdir(parents=True)
        (secrets / "api-key").write_text("v")

        report = nuke_purge_all(remove_secrets=True, install_root=root)

        assert report.secrets_dirs_removed == [root / "has-secrets" / "secrets"]

    def test_missing_install_root_is_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub_podman(monkeypatch)
        report = nuke_purge_all(
            remove_secrets=True, install_root=tmp_path / "nonexistent"
        )
        assert report.secrets_dirs_removed == []


# ────────────────────────────────────────────────────────────────────
# Image classification + parsing
# ────────────────────────────────────────────────────────────────────


class TestImageClassification:
    @pytest.mark.parametrize(
        "image",
        [
            "wip-registry:dev",
            "registry:dev",
            "def-store:latest",
            "mcp-server:dev",
            "auth-gateway:1.2.3",
            "ghcr.io/peterseb1969/wip-registry:v1.1.0",
            "ghcr.io/peterseb1969/registry:v1.1.0",
            "localhost/mcp-server:dev",
            "docker.io/some-org/wip-foo:latest",
        ],
    )
    def test_matches(self, image: str) -> None:
        assert _looks_like_wip_image(image) is True

    @pytest.mark.parametrize(
        "image",
        [
            "mongo:7.0",
            "postgres:16",
            "minio/minio:latest",
            "ghcr.io/random/totally-unrelated:1.0",
            "alpine:3.19",
        ],
    )
    def test_does_not_match(self, image: str) -> None:
        assert _looks_like_wip_image(image) is False


class TestImagesInCompose:
    def test_collects_unique_image_values(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yaml"
        compose.write_text(
            """
services:
  registry:
    image: registry:dev
  def-store:
    image: def-store:dev
  mcp-server:
    image: mcp-server:dev
  also-mcp:
    image: mcp-server:dev
"""
        )
        images = _images_in_compose(compose)
        assert images == ["registry:dev", "def-store:dev", "mcp-server:dev"]

    def test_skips_services_without_image(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yaml"
        compose.write_text(
            """
services:
  built-locally:
    build: ./components/foo
  with-image:
    image: registry:dev
"""
        )
        assert _images_in_compose(compose) == ["registry:dev"]

    def test_returns_empty_for_malformed(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yaml"
        compose.write_text("not: [valid yaml structure")
        assert _images_in_compose(compose) == []


class TestNetworksInCompose:
    def test_uses_explicit_name_override(self, tmp_path: Path) -> None:
        compose = tmp_path / "docker-compose.yaml"
        compose.write_text(
            """
services: {}
networks:
  default-key:
    name: wip-network
  other:
    driver: bridge
"""
        )
        networks = _networks_in_compose(compose)
        # default-key has a name override → wip-network. other has no
        # override → uses the key.
        assert "wip-network" in networks
        assert "other" in networks


# ────────────────────────────────────────────────────────────────────
# remove_images integration via nuke_install_dir
# ────────────────────────────────────────────────────────────────────


class TestNukeInstallDirRemoveImages:
    def _make_install_dir_with_images(
        self, tmp_path: Path, images: list[str]
    ) -> Path:
        d = tmp_path / "install"
        d.mkdir()
        services = "\n".join(
            f"  svc{i}:\n    image: {img}" for i, img in enumerate(images)
        )
        (d / "docker-compose.yaml").write_text(
            f"services:\n{services}\nnetworks:\n  wip-network:\n    name: wip-network\n"
        )
        return d

    def test_remove_images_calls_rmi_for_each(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rmi_calls: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False):  # type: ignore[no-untyped-def]
            import subprocess
            if "rmi" in cmd:
                rmi_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, returncode=0)

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir_with_images(
            tmp_path, ["registry:dev", "mcp-server:dev"]
        )
        report = nuke_install_dir(install, remove_images=True)

        assert sorted(report.images_removed) == ["mcp-server:dev", "registry:dev"]
        # Exactly one rmi call per image.
        assert len(rmi_calls) == 2

    def test_remove_images_off_skips_rmi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rmi_calls: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False):  # type: ignore[no-untyped-def]
            import subprocess
            if "rmi" in cmd:
                rmi_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir_with_images(tmp_path, ["registry:dev"])
        report = nuke_install_dir(install, remove_images=False)

        assert report.images_removed == []
        assert rmi_calls == []

    def test_network_sweep_attempts_each_declared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        net_calls: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False):  # type: ignore[no-untyped-def]
            import subprocess
            if "network" in cmd and "rm" in cmd:
                net_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir_with_images(tmp_path, ["registry:dev"])
        report = nuke_install_dir(install)

        # The compose declares one network (wip-network) — sweep tries to
        # remove it. Whether it succeeds depends on returncode=0 above.
        assert net_calls and net_calls[0][-1] == "wip-network"
        assert report.networks_removed == ["wip-network"]


# ────────────────────────────────────────────────────────────────────
# purge-all with images + networks
# ────────────────────────────────────────────────────────────────────


class TestPurgeAllImagesNetworks:
    def test_remove_images_collects_wip_images(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_ls_names(args):  # type: ignore[no-untyped-def]
            if args[0] == "ps":
                return []
            if args[0] == "pod":
                return []
            if args[0] == "network":
                return ["wip-network", "bridge", "host"]
            if args[0] == "images":
                return [
                    "registry:dev",
                    "mcp-server:dev",
                    "ghcr.io/peterseb1969/wip-registry:v1",
                    "mongo:7.0",
                    "postgres:16",
                ]
            return []

        rmi_calls: list[list[str]] = []
        net_calls: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False):  # type: ignore[no-untyped-def]
            import subprocess
            if "rmi" in cmd:
                rmi_calls.append(cmd)
            if "network" in cmd and "rm" in cmd:
                net_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("wip_deploy.nuke._podman_ls_names", fake_ls_names)
        monkeypatch.setattr("wip_deploy.nuke._podman", lambda args: None)
        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        report = nuke_purge_all(remove_images=True)

        # All three wip-* images filtered in, mongo/postgres filtered out.
        assert sorted(report.images_removed) == [
            "ghcr.io/peterseb1969/wip-registry:v1",
            "mcp-server:dev",
            "registry:dev",
        ]
        assert len(rmi_calls) == 3
        # Networks: only wip-network filtered in (bridge + host filtered out).
        assert report.networks_removed == ["wip-network"]
        assert len(net_calls) == 1

    def test_dry_run_does_not_invoke_rmi(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_ls_names(args):  # type: ignore[no-untyped-def]
            if args[0] == "images":
                return ["registry:dev", "mongo:7.0"]
            if args[0] == "network":
                return ["wip-network"]
            return []

        rmi_calls: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False):  # type: ignore[no-untyped-def]
            import subprocess
            if "rmi" in cmd:
                rmi_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("wip_deploy.nuke._podman_ls_names", fake_ls_names)
        monkeypatch.setattr("wip_deploy.nuke._podman", lambda args: None)
        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        report = nuke_purge_all(remove_images=True, dry_run=True)

        # Reported but not actually removed.
        assert report.images_removed == ["registry:dev"]
        assert report.networks_removed == ["wip-network"]
        assert rmi_calls == []


# ────────────────────────────────────────────────────────────────────
# k8s teardown (CASE-362)
# ────────────────────────────────────────────────────────────────────


class TestNukeK8sInstallDir:
    """`wip-deploy nuke --name <k8s-install>` teardown path. Mocks
    `kubectl` so tests don't need a real cluster."""

    def _make_install_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "install"
        d.mkdir()
        # k8s installs render manifests, not docker-compose.yaml. The
        # exact files don't matter for nuke_k8s_install_dir — it works
        # off the namespace, not the files.
        (d / "namespace.yaml").write_text("kind: Namespace\n")
        return d

    def test_basic_deletes_namespace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir(tmp_path)
        report = nuke_k8s_install_dir(install, namespace="wip-kb")

        # Single kubectl invocation for the namespace delete.
        assert any(
            cmd[:4] == ["kubectl", "delete", "namespace", "wip-kb"]
            for cmd in calls
        )
        assert report.k8s_namespace_removed == "wip-kb"
        # No PV deletion in the default path.
        assert report.k8s_pvs_removed == []
        assert report.compose_down_ran is False

    def test_remove_data_deletes_pvs_bound_to_namespace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(cmd)
            import subprocess
            stdout = ""
            # The PV-list query returns the data shape jsonpath produces.
            if cmd[:3] == ["kubectl", "get", "pv"]:
                stdout = (
                    "wip-kb\tpvc-mongo-001\n"
                    "wip-kb\tpvc-postgres-002\n"
                    "other-ns\tpvc-unrelated-003\n"
                )
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=stdout, stderr=""
            )

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir(tmp_path)
        report = nuke_k8s_install_dir(
            install, namespace="wip-kb", remove_data=True
        )

        # PVs in the target namespace got deleted (other-ns one didn't).
        assert report.k8s_pvs_removed == ["pvc-mongo-001", "pvc-postgres-002"]
        # The namespace delete still ran.
        assert report.k8s_namespace_removed == "wip-kb"
        # The unrelated PV was never targeted.
        assert not any(
            cmd[:4] == ["kubectl", "delete", "pv", "pvc-unrelated-003"]
            for cmd in calls
        )

    def test_remove_secrets_removes_file_backend_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess
        monkeypatch.setattr(
            "wip_deploy.nuke.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a, returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir(tmp_path)
        secrets = tmp_path / "secrets"
        secrets.mkdir()
        (secrets / "api-key").write_text("value")

        report = nuke_k8s_install_dir(
            install,
            namespace="wip-kb",
            remove_secrets=True,
            secrets_location=secrets,
        )
        assert not secrets.exists()
        assert report.secrets_dir_removed == secrets

    def test_missing_install_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(NukeError, match="install dir does not exist"):
            nuke_k8s_install_dir(
                tmp_path / "nope", namespace="wip-kb"
            )

    def test_missing_kubectl_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # shutil.which returns None for kubectl → loud refusal.
        monkeypatch.setattr(
            "wip_deploy.nuke.shutil.which",
            lambda cmd: None if cmd == "kubectl" else f"/usr/bin/{cmd}",
        )
        install = self._make_install_dir(tmp_path)
        with pytest.raises(NukeError, match="kubectl is not available"):
            nuke_k8s_install_dir(install, namespace="wip-kb")

    def test_kubectl_delete_failure_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            import subprocess
            if cmd[:3] == ["kubectl", "delete", "namespace"]:
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("wip_deploy.nuke.subprocess.run", fake_run)
        monkeypatch.setattr("wip_deploy.nuke.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

        install = self._make_install_dir(tmp_path)
        with pytest.raises(NukeError, match="kubectl delete namespace"):
            nuke_k8s_install_dir(install, namespace="wip-kb")


class TestHasK8sInstall:
    """Detect any k8s install under `~/.wip-deploy/` (CASE-362's
    --purge-all guard)."""

    def _write_state(
        self, install_dir: Path, target: str
    ) -> None:
        import json
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "deployment.deployer-state").write_text(
            json.dumps(
                {
                    "wip_deploy_format_version": 1,
                    "deployment": {
                        "metadata": {"name": install_dir.name},
                        "spec": {"target": target},
                    },
                }
            )
        )

    def test_empty_root_returns_false(self, tmp_path: Path) -> None:
        assert has_k8s_install(tmp_path / "nope") is False

    def test_only_compose_installs_returns_false(self, tmp_path: Path) -> None:
        self._write_state(tmp_path / "wip-local", target="compose")
        self._write_state(tmp_path / "wip-dev", target="dev")
        assert has_k8s_install(tmp_path) is False

    def test_one_k8s_install_returns_true(self, tmp_path: Path) -> None:
        self._write_state(tmp_path / "wip-kb", target="k8s")
        assert has_k8s_install(tmp_path) is True

    def test_mixed_installs_returns_true(self, tmp_path: Path) -> None:
        self._write_state(tmp_path / "wip-local", target="compose")
        self._write_state(tmp_path / "wip-kb", target="k8s")
        assert has_k8s_install(tmp_path) is True

    def test_malformed_state_file_skipped(self, tmp_path: Path) -> None:
        # Junk JSON in one dir shouldn't crash the scan.
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "deployment.deployer-state").write_text("not json")
        self._write_state(tmp_path / "wip-kb", target="k8s")
        assert has_k8s_install(tmp_path) is True

    def test_missing_state_file_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "no-state").mkdir()
        assert has_k8s_install(tmp_path) is False
