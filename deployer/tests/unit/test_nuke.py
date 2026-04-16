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
    _looks_like_wip_pod,
    _looks_like_wip_volume,
    nuke_install_dir,
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
            "pod_wip-console",
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

        # ps, pod scanned; volume not
        assert "ps" in queried
        assert "pod" in queried
        assert "volume" not in queried
