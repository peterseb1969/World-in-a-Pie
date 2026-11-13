"""Forward-review findings on the CASE-1229 deployer fixes.

- Proxy reload: a Caddyfile is compared with the one the proxy is known to
  serve, not with the file the render overwrote, so a config that reached
  disk but not the proxy is reloaded by the next apply; a proxy the apply
  started is not reloaded; a failed reload is reported.
- The v3→v4 state step drops `on_timeout` whatever it held and announces what
  it changes; no step crashes on a non-mapping block.
- `up`/`rebuild` wait the install's saved timeout unless told otherwise.
- The compose project of a relative install dir is the directory's real name.
- A health timeout says when a container's status was never observed.
- Garage's readiness wait survives a probe that hangs.
- A Registry that refuses a pre-apply check is reported as that, not under a
  collision header.
- A k8s rollout wait asks every workload; its error claims no time window.
- A hook deadline says the hook may still be running.
- rotate-key counts an app as a reader of its own key.
- On k8s the empty scope applies the shared files alone; a removal says it
  applies the whole install.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import wip_deploy.cli as cli
from wip_deploy.apply import (
    ApplyError,
    _await_healthy,
    _compose_project,
    _provision_garage_if_active,
    _run_hook,
    _wait_k8s_rollout,
    apply_compose,
    apply_k8s,
)
from wip_deploy.cli import _upgrade_state_payload, app
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.base import FileTree

from . import test_case_443 as t443
from . import test_case_1228_drift_guard_and_state as t1228
from . import test_case_1229 as t1229

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()
CADDY = "config/caddy/Caddyfile"


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


# ────────────────────────────────────────────────────────────────────
# Proxy reload follows what the proxy serves
# ────────────────────────────────────────────────────────────────────


def _apply_compose(
    tmp_path: Path,
    content: str,
    *,
    reload_ok: bool = True,
    started: list[str] | None = None,
    check_raises: bool = False,
) -> MagicMock:
    """One compose apply of a tree whose edge Caddyfile is `content`. With
    `started`, the runtime reports those containers as created by the up."""
    before: frozenset[tuple[str, str]] = frozenset()
    after = frozenset((name, "new-id") for name in (started or []))
    with (
        patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
        patch("wip_deploy.apply._run_build"),
        patch("wip_deploy.apply._run_up"),
        patch("wip_deploy.apply._remove_install_containers"),
        patch(
            "wip_deploy.apply._check_containers_created",
            side_effect=ApplyError("compose up left wip-x without a container") if check_raises else None,
        ),
        patch("wip_deploy.apply._container_cli", return_value="podman" if started is not None else None),
        patch("wip_deploy.apply._project_container_state", side_effect=[before, after]),
        patch("wip_deploy.apply._reload_proxy", return_value=reload_ok) as reload_proxy,
        patch("wip_deploy.apply._run_post_install"),
    ):
        try:
            apply_compose(
                deployment=t443._deployment(), components=[], apps=[],
                tree=t443._tree({CADDY: content}), install_dir=tmp_path, services_scope=None,
            )
        except ApplyError:
            if not check_raises:
                raise
    return reload_proxy


class TestProxyReloadFollowsWhatTheProxyServes:
    def test_a_reload_that_failed_is_retried_by_the_next_apply(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        first = _apply_compose(tmp_path, "edge-v2", reload_ok=False)
        assert first.call_count == 1
        assert "test-install_wip-caddy is still serving its previous Caddyfile" in capsys.readouterr().err
        # The file on disk is already v2; the proxy is not.
        second = _apply_compose(tmp_path, "edge-v2")
        assert second.call_args_list == [(("test-install_wip-caddy",),)]
        # Delivered now: a third apply leaves it alone.
        assert _apply_compose(tmp_path, "edge-v2").call_count == 0

    def test_an_apply_that_stopped_before_the_reload_is_caught_up(self, tmp_path: Path) -> None:
        _apply_compose(tmp_path, "edge-v1")
        stopped = _apply_compose(tmp_path, "edge-v2", check_raises=True)
        assert stopped.call_count == 0
        assert _apply_compose(tmp_path, "edge-v2").call_args_list == [(("test-install_wip-caddy",),)]

    def test_a_proxy_the_apply_started_is_not_reloaded(self, tmp_path: Path) -> None:
        # A first install: compose created the proxy on the rendered file.
        reload_proxy = _apply_compose(tmp_path, "edge-v1", started=["test-install_wip-caddy"])
        assert reload_proxy.call_count == 0
        assert _apply_compose(tmp_path, "edge-v1").call_count == 0


# ────────────────────────────────────────────────────────────────────
# State steps: loud, and never a traceback
# ────────────────────────────────────────────────────────────────────


class TestStateV3ToV4IsLoud:
    @pytest.mark.parametrize("value", ["warn", "continue"])
    def test_a_hand_set_on_timeout_is_dropped_and_named(
        self, value: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = _upgrade_state_payload(
            t1229._v3_state({"wait": True, "timeout_seconds": 300, "on_timeout": value})
        )
        assert "on_timeout" not in out["deployment"]["spec"]["apply"]
        assert f"apply.on_timeout {value!r}" in capsys.readouterr().err

    def test_a_saved_no_wait_is_announced(self, capsys: pytest.CaptureFixture[str]) -> None:
        out = _upgrade_state_payload(
            t1229._v3_state({"wait": False, "timeout_seconds": 300, "on_timeout": "fail"})
        )
        assert out["deployment"]["spec"]["apply"]["wait"] is True
        assert "install --no-wait" in capsys.readouterr().err

    def test_the_saved_default_says_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        _upgrade_state_payload(
            t1229._v3_state({"wait": True, "timeout_seconds": 300, "on_timeout": "fail"})
        )
        assert capsys.readouterr().err == ""


@pytest.mark.parametrize("version,block", [(3, "apply"), (2, "network"), (5, "network"), (4, "network")])
def test_a_string_block_is_left_for_validation_not_a_traceback(version: int, block: str) -> None:
    payload = t1229._v3_state({"wait": True, "timeout_seconds": 300})
    payload["wip_deploy_format_version"] = version
    payload["deployment"]["spec"][block] = "wait"
    _upgrade_state_payload(payload)  # no ValueError


# ────────────────────────────────────────────────────────────────────
# up / rebuild wait the install's own pace
# ────────────────────────────────────────────────────────────────────


class TestUpAndRebuildReadTheSavedTimeout:
    def _install(self, tmp_path: Path, timeout: int) -> None:
        d = t1228._deployment(users=[])
        d.spec.apply.timeout_seconds = timeout
        t1228._persist(tmp_path, d)

    def test_up_waits_the_saved_timeout(self, tmp_path: Path) -> None:
        self._install(tmp_path, 600)
        with patch("wip_deploy.apply.up_compose_install") as up:
            result = runner.invoke(app, ["up", "--install-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert up.call_args.kwargs["timeout_seconds"] == 600

    def test_rebuild_waits_the_saved_timeout(self, tmp_path: Path) -> None:
        self._install(tmp_path, 450)
        with patch("wip_deploy.apply.rebuild_compose_services") as rebuild:
            result = runner.invoke(app, ["rebuild", "registry", "--install-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert rebuild.call_args.kwargs["timeout_seconds"] == 450

    def test_the_flag_still_wins(self, tmp_path: Path) -> None:
        self._install(tmp_path, 600)
        with patch("wip_deploy.apply.up_compose_install") as up:
            runner.invoke(app, ["up", "--install-dir", str(tmp_path), "--wait-timeout", "30"])
        assert up.call_args.kwargs["timeout_seconds"] == 30

    def test_no_state_waits_the_spec_default(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply.up_compose_install") as up:
            runner.invoke(app, ["up", "--install-dir", str(tmp_path)])
        assert up.call_args.kwargs["timeout_seconds"] == 300


def test_a_relative_install_dir_names_its_real_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "wip"
    install.mkdir()
    (install / "docker-compose.yaml").write_text("services: {}\n")
    monkeypatch.chdir(install)
    assert _compose_project(Path(".") / "docker-compose.yaml") == "wip"


class TestHealthTimeoutSaysWhatWasNotObserved:
    def test_a_failed_compose_ps_is_not_called_unhealthy(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply._compose_ps", return_value=None):
            out = _await_healthy(tmp_path, ["podman-compose"], {"a"}, 0)
        assert out == ["a (compose ps failed)"]

    def test_an_unlisted_container_says_so(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply._compose_ps", return_value={"a": "starting"}):
            out = _await_healthy(tmp_path, ["podman-compose"], {"a", "b"}, 0)
        assert out == ["a", "b (not listed by compose ps)"]


# ────────────────────────────────────────────────────────────────────
# Garage's readiness wait keeps its window
# ────────────────────────────────────────────────────────────────────


def test_a_hanging_garage_probe_is_one_failed_attempt(tmp_path: Path, discovery: Discovery) -> None:
    from . import test_case_864_garage_module as t864

    d = t864.TestProvisioning()._deployment_with_secrets(tmp_path)
    probes: list[int | None] = []

    def fake_run(argv, cwd=None, input=None, capture_output=True, text=True, check=True, timeout=None):
        tail = argv[argv.index("/garage") + 1:]
        if tail[:2] == ["json-api", "GetClusterHealth"]:
            probes.append(timeout)
            if len(probes) == 1:
                raise subprocess.TimeoutExpired(argv, timeout or 0)
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
        if tail[:2] == ["json-api", "GetClusterLayout"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"roles": [1]}), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"id": "b"}), stderr="")

    with (
        patch("wip_deploy.apply.subprocess.run", side_effect=fake_run),
        patch("wip_deploy.apply.time.sleep"),
    ):
        _provision_garage_if_active(d, discovery.components, exec_prefix=["x"], cwd=None)
    assert len(probes) == 2
    assert probes[0] is not None and probes[0] <= 10


# ────────────────────────────────────────────────────────────────────
# Failures name their cause
# ────────────────────────────────────────────────────────────────────


def test_a_registry_refusal_is_not_reported_as_a_collision(
    discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    import typer

    from wip_deploy.attachment_check import AttachmentCheck
    from wip_deploy.runtime_reservations import RuntimeReservationCheck
    from wip_deploy.secrets_backend import ResolvedSecrets

    d = t1229._deployment()
    secrets = ResolvedSecrets(values={"api-key": "k"})
    refusal = "the Registry refused the install's admin key (401) when asked for …"
    with (
        patch("wip_deploy.cli._install_trust") as trust,
        patch("wip_deploy.runtime_reservations.check_runtime_reservations",
              return_value=RuntimeReservationCheck(refused=refusal)),
        pytest.raises(typer.Exit) as exc,
    ):
        trust.return_value.__enter__.return_value = True
        cli._refuse_runtime_reservation_collisions(d, [], secrets)
    assert exc.value.exit_code == 2
    err = capsys.readouterr().err
    assert f"error: {refusal}" in err and "claim ID prefixes" not in err

    with (
        patch("wip_deploy.cli._install_trust") as trust,
        patch("wip_deploy.attachment_check.check_attachment_collisions",
              return_value=AttachmentCheck(refused=refusal)),
        pytest.raises(typer.Exit),
    ):
        trust.return_value.__enter__.return_value = True
        cli._refuse_attachment_collisions(d, [], secrets)
    err = capsys.readouterr().err
    assert f"error: {refusal}" in err and "would take routes" not in err


class TestRolloutAsksEveryWorkload:
    def test_a_refused_workload_does_not_end_the_wait(self) -> None:
        asked: list[str] = []

        def run(cmd, *a, **k):
            asked.append(cmd[5])
            if cmd[5] == "deployment/wip-registry":
                raise subprocess.CalledProcessError(1, cmd, stderr="Error from server (Forbidden): no")
            return MagicMock(returncode=0)

        d = t1229._deployment()
        with (
            patch("wip_deploy.apply._expected_workloads",
                  return_value=[("Deployment", "wip-registry"), ("Deployment", "wip-caddy")]),
            patch("wip_deploy.apply.kubectl", return_value=["kubectl"]),
            patch("wip_deploy.apply.subprocess.run", side_effect=run),
        ):
            out = _wait_k8s_rollout(install_dir=Path("/x"), ns="wip", components=[], apps=[], deployment=d)
        assert asked == ["deployment/wip-registry", "deployment/wip-caddy"]
        assert out == ["deployment/wip-registry: Error from server (Forbidden): no"]

    def test_the_error_claims_no_time_window(self, tmp_path: Path) -> None:
        from . import test_scoped_k8s_apply as tk8s

        deployment = tk8s._k8s_deployment(apps=[])
        deployment.spec.apply.wait = True
        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
            patch("wip_deploy.apply._kubectl_run"),
            patch("wip_deploy.apply._wait_k8s_rollout",
                  return_value=["deployment/wip-registry: Error from server (Forbidden): no"]),
            pytest.raises(ApplyError) as exc,
        ):
            apply_k8s(deployment=deployment, components=[], apps=[],
                      tree=tk8s._synthetic_tree({}), install_dir=tmp_path)
        assert str(exc.value) == "rollout did not complete: deployment/wip-registry: Error from server (Forbidden): no"


def test_a_hook_deadline_says_the_hook_may_still_run() -> None:
    with (
        patch("wip_deploy.apply.subprocess.run", side_effect=subprocess.TimeoutExpired(["sh"], 5)),
        pytest.raises(ApplyError, match="the hook may still be running in its container"),
    ):
        _run_hook(["sh"], cwd=None, label="'init' on 'registry'", timeout_seconds=5)


# ────────────────────────────────────────────────────────────────────
# rotate-key: an app reads its own key
# ────────────────────────────────────────────────────────────────────


def test_an_app_reads_its_own_key(discovery: Discovery) -> None:
    from wip_deploy.config_gen.api_keys import app_api_key
    from wip_deploy.spec import AppRef

    d = t1228._deployment(users=[])
    d.spec.apps = [AppRef(name="wip-kb")]
    kb = next(a for a in discovery.apps if a.metadata.name == "wip-kb")
    key = app_api_key(kb)
    assert key is not None
    readers = cli._key_readers(key.secret_name, d, discovery.components, discovery.apps)
    assert readers == ["wip-kb"]
    lines = cli._rotation_restart_lines("compose", readers, declared_key=True)
    assert not any("Nothing restarts" in line for line in lines)


# ────────────────────────────────────────────────────────────────────
# k8s: config-only verbs stay narrow; a removal says it is not
# ────────────────────────────────────────────────────────────────────


def test_the_empty_k8s_scope_applies_the_shared_files_alone(tmp_path: Path) -> None:
    from . import test_scoped_k8s_apply as tk8s

    deployment = tk8s._k8s_deployment(apps=["wip-kb"])
    tree: FileTree = tk8s._synthetic_tree({"wip-kb": "services", "mongodb": "infrastructure"})
    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
        patch("wip_deploy.apply._kubectl_run") as run,
    ):
        apply_k8s(deployment=deployment, components=[], apps=[tk8s._fake_app("wip-kb")],
                  tree=tree, install_dir=tmp_path, services_scope=[])
    for call in run.call_args_list:
        assert "--prune" not in call.args[0] and "-R" not in call.args[0]
    applied = tk8s._applied_files(run)
    assert "wip-kb.yaml" not in applied and "mongodb.yaml" not in applied


def test_a_k8s_removal_says_it_applies_the_whole_install(capsys: pytest.CaptureFixture[str]) -> None:
    t1229.TestRemovalPrunesOnK8s()._run("k8s", removes=True, widened=["caddy"])
    assert "Applying the whole install (with prune)" in capsys.readouterr().out


# ────────────────────────────────────────────────────────────────────
# Decided follow-ups: a refused reload keeps the old config serving; a
# removal never prunes a volume claim
# ────────────────────────────────────────────────────────────────────


@patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman")
def test_a_refused_reload_keeps_the_old_config_and_is_retried(
    _which: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`caddy reload` refuses the new Caddyfile: no restart (that would boot
    Caddy on the refused file), a warning naming the proxy, and no recorded
    digest, so the next apply tries again."""
    from wip_deploy.apply import _PROXY_DELIVERED_FILE, _deliver_proxy_configs

    (tmp_path / CADDY).parent.mkdir(parents=True)
    (tmp_path / CADDY).write_text("edge-bad")
    with patch("wip_deploy.apply.subprocess.run", return_value=MagicMock(returncode=1)) as run:
        _deliver_proxy_configs(tmp_path, "tc01", started=set(), reload=True)
    commands = [c.args[0] for c in run.call_args_list]
    assert commands == [[
        "podman", "exec", "tc01_wip-caddy", "caddy", "reload",
        "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile",
    ]]
    err = capsys.readouterr().err
    assert "tc01_wip-caddy is still serving its previous Caddyfile" in err
    assert "was not restarted" in err
    assert not (tmp_path / _PROXY_DELIVERED_FILE).exists()


def test_a_k8s_removal_keeps_volume_claims_and_names_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from . import test_scoped_k8s_apply as tk8s

    deployment = tk8s._k8s_deployment(apps=[])
    tree = tk8s._synthetic_tree({})
    tree.add(
        "infrastructure/mongodb.yaml",
        "kind: PersistentVolumeClaim\nmetadata:\n  name: wip-mongodb-data\n---\nkind: StatefulSet\n",
    )
    listing = MagicMock(returncode=0, stdout="wip-mongodb-data wip-wip-foo-data")
    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
        patch("wip_deploy.apply.kubectl", return_value=["kubectl"]),
        patch("wip_deploy.apply.subprocess.run", return_value=listing),
        patch("wip_deploy.apply._kubectl_run") as kubectl_run,
    ):
        apply_k8s(deployment=deployment, components=[], apps=[], tree=tree,
                  install_dir=tmp_path, prune_volume_claims=False)
    full = kubectl_run.call_args_list[-1].args[0]
    assert "--prune" in full
    assert "core/v1/PersistentVolumeClaim" not in full
    err = capsys.readouterr().err
    assert "kept volume claim 'wip-wip-foo-data'" in err
    assert "wip-mongodb-data" not in err


def test_remove_app_on_k8s_asks_to_keep_claims() -> None:
    kwargs = t1229.TestRemovalPrunesOnK8s()._run("k8s", removes=True, widened=["caddy"])
    assert kwargs["prune_volume_claims"] is False
    # A config-only k8s verb and a compose removal do not ask.
    assert "prune_volume_claims" not in t1229.TestRemovalPrunesOnK8s()._run("k8s", removes=False, widened=[])
    assert "prune_volume_claims" not in t1229.TestRemovalPrunesOnK8s()._run("compose", removes=True, widened=[])


def test_install_still_prunes_volume_claims(tmp_path: Path) -> None:
    from . import test_scoped_k8s_apply as tk8s

    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/kubectl"),
        patch("wip_deploy.apply._kubectl_run") as kubectl_run,
    ):
        apply_k8s(deployment=tk8s._k8s_deployment(apps=[]), components=[], apps=[],
                  tree=tk8s._synthetic_tree({}), install_dir=tmp_path)
    assert "core/v1/PersistentVolumeClaim" in kubectl_run.call_args_list[-1].args[0]
