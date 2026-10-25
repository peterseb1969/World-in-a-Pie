"""A dev apply builds before it tears anything down.

The dev target rebuilds every image on each full apply and, before `compose
up`, removes every `wip-*` container. With the build inside `up --build`, a
failed build ran after that removal and left no containers at all. The apply
now builds first, so a failed build leaves the running stack as it was;
`up` only recreates onto the fresh images.

podman-compose also runs a step's podman calls all at once. On macOS each is
an SSH session into the podman machine, and its sshd refuses sessions beyond
MaxStartups (10): a dozen parallel builds lost two to "ssh: handshake failed".
Every podman-compose invocation therefore carries `--parallel`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.apply import (
    PODMAN_COMPOSE_PARALLEL,
    ApplyError,
    _detect_compose_cmd,
    _run_build,
    _run_up,
    apply_compose,
)
from wip_deploy.renderers.base import FileTree
from wip_deploy.spec import Deployment

PODMAN = ["podman-compose", "--parallel", str(PODMAN_COMPOSE_PARALLEL)]


def _deployment(target: str) -> Deployment:
    platform: dict = {"compose": {"data_dir": "/tmp/wip-test-data"}}
    if target == "dev":
        platform["dev"] = {}
    return Deployment.model_validate(
        {
            "metadata": {"name": "test-install"},
            "spec": {
                "target": target,
                "apps": [],
                "modules": {"optional": []},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "localhost"},
                "images": {},
                "platform": platform,
                "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
                "apply": {"wait": False},
            },
        }
    )


def _apply(tmp_path: Path, target: str, *, scope: list[str] | None = None,
           build_error: Exception | None = None) -> tuple[MagicMock, Exception | None]:
    calls = MagicMock()
    tree = FileTree()
    tree.add("docker-compose.yaml", "services: {}\n")
    raised: Exception | None = None
    with (
        patch("wip_deploy.apply._detect_compose_cmd", return_value=PODMAN),
        patch("wip_deploy.apply._run_build", side_effect=build_error) as build,
        patch("wip_deploy.apply._remove_stale_wip_containers") as wipe,
        patch("wip_deploy.apply._run_up") as up,
        patch("wip_deploy.apply._reload_proxy"),
        patch("wip_deploy.apply._run_post_install"),
    ):
        calls.attach_mock(build, "build")
        calls.attach_mock(wipe, "wipe")
        calls.attach_mock(up, "up")
        try:
            apply_compose(
                deployment=_deployment(target), components=[], apps=[],
                tree=tree, install_dir=tmp_path, services_scope=scope,
            )
        except ApplyError as e:
            raised = e
    return calls, raised


class TestOrder:
    def test_dev_builds_then_wipes_then_recreates(self, tmp_path: Path) -> None:
        calls, raised = _apply(tmp_path, "dev")
        assert raised is None
        assert [c[0] for c in calls.mock_calls] == ["build", "wipe", "up"]
        assert calls.up.call_args.kwargs == {"force_recreate": True}

    def test_failed_build_leaves_the_stack_alone(self, tmp_path: Path) -> None:
        calls, raised = _apply(
            tmp_path, "dev", build_error=ApplyError("podman-compose build failed (exit 125)"),
        )
        assert raised is not None
        assert raised.mutated is False  # the CLI keeps the last-known-good spec
        calls.wipe.assert_not_called()
        calls.up.assert_not_called()

    def test_scoped_dev_apply_builds_only_the_scope(self, tmp_path: Path) -> None:
        calls, _ = _apply(tmp_path, "dev", scope=["dex"])
        assert calls.build.call_args.kwargs == {"services": ["dex"]}
        calls.wipe.assert_not_called()

    def test_empty_scope_builds_nothing(self, tmp_path: Path) -> None:
        calls, _ = _apply(tmp_path, "dev", scope=[])
        calls.build.assert_not_called()
        calls.up.assert_not_called()

    def test_compose_target_does_not_build(self, tmp_path: Path) -> None:
        calls, _ = _apply(tmp_path, "compose")
        calls.build.assert_not_called()
        calls.wipe.assert_not_called()
        assert calls.up.call_args.kwargs == {"force_recreate": False}


class TestCommands:
    def test_podman_compose_is_capped(self) -> None:
        with patch(
            "wip_deploy.apply.shutil.which",
            side_effect=lambda b: "/usr/bin/podman-compose" if b == "podman-compose" else None,
        ):
            assert _detect_compose_cmd() == PODMAN

    def test_docker_compose_is_not_capped(self) -> None:
        with patch(
            "wip_deploy.apply.shutil.which",
            side_effect=lambda b: "/usr/bin/docker" if b == "docker" else None,
        ):
            assert _detect_compose_cmd() == ["docker", "compose"]

    def test_build_command(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply.subprocess.run") as run:
            _run_build(tmp_path, PODMAN, services=["registry"])
        cmd = run.call_args.args[0]
        assert cmd[:3] == PODMAN
        assert cmd[-2:] == ["build", "registry"]

    def test_build_failure_names_the_untouched_stack(self, tmp_path: Path) -> None:
        with (
            patch(
                "wip_deploy.apply.subprocess.run",
                side_effect=subprocess.CalledProcessError(125, "podman-compose"),
            ),
            pytest.raises(ApplyError, match="left untouched"),
        ):
            _run_build(tmp_path, PODMAN)

    def test_recreate_without_build(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply.subprocess.run") as run:
            _run_up(tmp_path, PODMAN, force_recreate=True)
        cmd = run.call_args.args[0]
        assert "--force-recreate" in cmd
        assert "--build" not in cmd
