"""Regression coverage for CASE-443 — scoped compose apply for app verbs.

`add-app`/`remove-app` promised (CASE-313) to preserve the rest of the
install untouched, but on compose the apply path ran an unscoped
`compose up -d`, recreating the whole stack (and on dev target, the
CASE-282 stale-wipe `rm -f`'d every wip-* container first).

Contract under test, all via mocked subprocess / patched helpers:

  - `apply_compose(services_scope=[svc])` passes the scope to `_run_up`,
    skips the dev-target stale wipe, and scopes the health wait.
  - `services_scope=[]` (config-only mutation, e.g. remove-app) runs NO
    `compose up` at all — an empty service list reaching `up -d` would
    be the full-stack up the scoping exists to prevent.
  - `services_scope=None` keeps install semantics: full up + dev wipe.
  - Proxy reload fires only for a proxy whose mounted Caddyfile content
    actually changed across the tree write.
  - `_reload_proxy` execs `caddy reload` and falls back to a container
    restart on failure.
  - `_wait_healthy(only=...)` intersects the expected set.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wip_deploy.apply import _reload_proxy, apply_compose
from wip_deploy.renderers.base import FileTree
from wip_deploy.spec import Deployment


def _deployment(target: str = "compose") -> Deployment:
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


def _tree(files: dict[str, str] | None = None) -> FileTree:
    tree = FileTree()
    tree.add("docker-compose.yaml", "services: {}\n")
    for rel, content in (files or {}).items():
        tree.add(rel, content)
    return tree


def _apply(tmp_path: Path, *, deployment, tree, services_scope):
    """Run apply_compose with every subprocess-touching helper patched."""
    with (
        patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
        patch("wip_deploy.apply._run_up") as run_up,
        patch("wip_deploy.apply._remove_stale_wip_containers") as wipe,
        patch("wip_deploy.apply._reload_proxy") as reload_proxy,
        patch("wip_deploy.apply._run_post_install"),
    ):
        result = apply_compose(
            deployment=deployment,
            components=[],
            apps=[],
            tree=tree,
            install_dir=tmp_path,
            services_scope=services_scope,
        )
    return result, run_up, wipe, reload_proxy


class TestScopedUp:
    def test_scope_passes_services_to_run_up(self, tmp_path: Path) -> None:
        _, run_up, _, _ = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree(),
            services_scope=["wip-aa"],
        )
        assert run_up.call_count == 1
        assert run_up.call_args.kwargs["services"] == ["wip-aa"]

    def test_empty_scope_skips_up_entirely(self, tmp_path: Path) -> None:
        result, run_up, _, _ = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree(),
            services_scope=[],
        )
        run_up.assert_not_called()
        assert result.services_up == 0

    def test_none_scope_keeps_full_up(self, tmp_path: Path) -> None:
        _, run_up, _, _ = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree(),
            services_scope=None,
        )
        assert run_up.call_count == 1
        assert "services" not in run_up.call_args.kwargs

    def test_scoped_up_reports_scope_size(self, tmp_path: Path) -> None:
        result, _, _, _ = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree(),
            services_scope=["wip-aa"],
        )
        assert result.services_up == 1


class TestDevWipeGate:
    def test_dev_full_install_still_wipes(self, tmp_path: Path) -> None:
        _, _, wipe, _ = _apply(
            tmp_path,
            deployment=_deployment(target="dev"),
            tree=_tree(),
            services_scope=None,
        )
        wipe.assert_called_once()

    def test_dev_scoped_mutation_never_wipes(self, tmp_path: Path) -> None:
        _, _, wipe, _ = _apply(
            tmp_path,
            deployment=_deployment(target="dev"),
            tree=_tree(),
            services_scope=["wip-aa"],
        )
        wipe.assert_not_called()


class TestProxyReload:
    CADDY = "config/caddy/Caddyfile"
    ROUTER = "config/router/Caddyfile"

    def test_changed_router_config_reloads_router_only(self, tmp_path: Path) -> None:
        (tmp_path / self.CADDY).parent.mkdir(parents=True)
        (tmp_path / self.CADDY).write_text("edge-v1")
        (tmp_path / self.ROUTER).parent.mkdir(parents=True)
        (tmp_path / self.ROUTER).write_text("router-v1")

        _, _, _, reload_proxy = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree({self.CADDY: "edge-v1", self.ROUTER: "router-v2"}),
            services_scope=["wip-aa"],
        )
        assert reload_proxy.call_args_list == [(("wip-router",),)]

    def test_unchanged_configs_reload_nothing(self, tmp_path: Path) -> None:
        (tmp_path / self.CADDY).parent.mkdir(parents=True)
        (tmp_path / self.CADDY).write_text("edge-v1")

        _, _, _, reload_proxy = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree({self.CADDY: "edge-v1"}),
            services_scope=["wip-aa"],
        )
        reload_proxy.assert_not_called()

    def test_unscoped_install_never_reloads(self, tmp_path: Path) -> None:
        # Full install recreates everything anyway; the reload path is
        # scoped-mutation-only.
        _, _, _, reload_proxy = _apply(
            tmp_path,
            deployment=_deployment(),
            tree=_tree({self.CADDY: "edge-v1"}),
            services_scope=None,
        )
        reload_proxy.assert_not_called()


class TestReloadProxyHelper:
    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman")
    def test_exec_reload_happy_path(self, _which: MagicMock, run: MagicMock) -> None:
        run.return_value = MagicMock(returncode=0)
        _reload_proxy("wip-router")
        assert run.call_count == 1
        cmd = run.call_args_list[0].args[0]
        assert cmd[:3] == ["podman", "exec", "wip-router"]
        assert "reload" in cmd

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman")
    def test_restart_fallback_on_failure(self, _which: MagicMock, run: MagicMock) -> None:
        run.side_effect = [MagicMock(returncode=1), MagicMock(returncode=0)]
        _reload_proxy("wip-caddy")
        assert run.call_count == 2
        assert run.call_args_list[1].args[0] == ["podman", "restart", "wip-caddy"]

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", return_value=None)
    def test_no_runtime_is_silent(self, _which: MagicMock, run: MagicMock) -> None:
        _reload_proxy("wip-caddy")
        run.assert_not_called()


class TestWaitHealthyOnly:
    def test_only_filters_expected_set(self, tmp_path: Path) -> None:
        from wip_deploy.apply import _wait_healthy

        with (
            patch(
                "wip_deploy.apply._services_with_healthchecks",
                return_value={"wip-a", "wip-b"},
            ),
            patch(
                "wip_deploy.apply._compose_ps",
                return_value={"wip-a": "healthy", "wip-b": "starting"},
            ),
        ):
            deployment = _deployment()
            deployment.spec.apply.wait = True
            # Unscoped would block on wip-b; scoped to wip-a returns True.
            assert _wait_healthy(
                install_dir=tmp_path,
                compose_cmd=["podman-compose"],
                components=[],
                apps=[],
                deployment=deployment,
                only={"wip-a"},
            )

    def test_only_empty_set_short_circuits(self, tmp_path: Path) -> None:
        from wip_deploy.apply import _wait_healthy

        with patch(
            "wip_deploy.apply._services_with_healthchecks",
            return_value={"wip-a"},
        ):
            assert _wait_healthy(
                install_dir=tmp_path,
                compose_cmd=["podman-compose"],
                components=[],
                apps=[],
                deployment=_deployment(),
                only=set(),
            )
