"""An app's key lives as long as the app is on the install.

Provenance: CASE-512. remove-app left the app's key secret in the store, so a
later add-app brought the same key back. Now:

- remove-app deletes the app's key secret once the spec that drops the app is
  saved (the same moment its container goes): after a successful apply, and
  after a failed one that had already changed the install. A removal that saved
  nothing keeps it, and the app keeps running with its key.
- add-app of an app the install does not run generates a new key, never one an
  earlier enablement left in the store; on k8s the live Secret's stale value of
  it does not stop the apply as drift.
- Every other apply keeps the key; the master key and the other apps' keys are
  never touched; an app that holds the master key has no key secret to delete.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import wip_deploy.apply as apply_mod
import wip_deploy.cli as cli
from wip_deploy.cli import app
from wip_deploy.secrets import secret_digest
from wip_deploy.spec.deployment import AppRef

from . import test_case_1228_drift_guard_and_state as t1228

runner = CliRunner()

AA_KEY = "app-wip-aa-api-key"
KB_KEY = "app-wip-kb-api-key"
MASTER = "api-key"


class _Harness:
    """Drives the app verbs through the real secret resolution, drift check
    and state persistence; validation, rendering and the apply are stubbed."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str = "compose"):
        self.install_dir = tmp_path / "install"
        self.store = tmp_path / "secrets"
        self.monkeypatch = monkeypatch
        self.target = target
        self.apply_effect: object = None
        self.order: list[str] = []
        state = t1228._deployment(users=[], target=target, secrets_location=str(self.store))
        t1228._persist(self.install_dir, state)
        for fn in (
            "_validate_or_exit",
            "_select_cluster_or_exit",
            "_refuse_runtime_reservation_collisions",
            "_refuse_attachment_collisions",
        ):
            monkeypatch.setattr(cli, fn, lambda *a, **k: None)
        monkeypatch.setattr(cli, "_render_tree", lambda *a, **k: cli.FileTree())
        monkeypatch.setattr(cli, "changed_outside_scope", lambda *a, **k: [])
        monkeypatch.setattr(cli, "_k8s_render_predates_front_door", lambda *a, **k: False)
        monkeypatch.setattr(
            apply_mod, "stop_and_remove_container",
            lambda install, name: self.order.append(f"stop {name}"),
        )
        self.live: dict[str, str] | None = None
        monkeypatch.setattr(cli, "read_live_secrets", lambda ns: self.live)
        # None = the cluster accepts the patch; an exception = it refuses.
        self.patch_effect: BaseException | None = None

        def remove_live(ns: str, name: str) -> bool:
            if self.patch_effect is not None:
                raise self.patch_effect
            if self.live is None or name not in self.live:
                return False
            del self.live[name]
            return True

        monkeypatch.setattr(cli, "remove_live_secret_value", remove_live)

        def apply(**_kw: object) -> object:
            self.order.append("apply")
            if isinstance(self.apply_effect, BaseException):
                raise self.apply_effect
            return apply_mod.ApplyResult(
                install_dir=self.install_dir, services_up=0, healthy=True,
            )

        monkeypatch.setattr(cli, "apply_compose", apply)
        monkeypatch.setattr(cli, "apply_k8s", apply)

    def run(self, *args: str):
        return runner.invoke(app, [*args, "--install-dir", str(self.install_dir)])

    def value(self, name: str) -> str | None:
        path = self.store / name
        return path.read_text() if path.exists() else None

    def recorded(self) -> dict[str, str]:
        return cli._load_state_secret_digests(self.install_dir)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(tmp_path, monkeypatch)


def test_remove_then_add_gives_the_app_a_new_key(harness: _Harness) -> None:
    result = harness.run("add-app", "wip-aa")
    assert result.exit_code == 0, result.output
    first = harness.value(AA_KEY)
    master = harness.value(MASTER)
    assert first and master

    result = harness.run("remove-app", "wip-aa")
    assert result.exit_code == 0, result.output
    assert harness.value(AA_KEY) is None
    assert harness.value(MASTER) == master
    # The record no longer names the key, so the next apply cannot call its
    # absence drift.
    assert AA_KEY not in harness.recorded()
    assert f"Deleted wip-aa's key ({AA_KEY})" in result.output

    result = harness.run("add-app", "wip-aa")
    assert result.exit_code == 0, result.output
    second = harness.value(AA_KEY)
    assert second and second != first
    assert harness.recorded()[AA_KEY] == secret_digest(second)
    assert harness.value(MASTER) == master


def test_every_other_apply_keeps_the_key(harness: _Harness) -> None:
    assert harness.run("add-app", "wip-aa").exit_code == 0
    key = harness.value(AA_KEY)
    result = harness.run("add-app", "wip-kb")
    assert result.exit_code == 0, result.output
    assert harness.value(AA_KEY) == key
    kb = harness.value(KB_KEY)
    # Removing one app leaves the other's key alone.
    assert harness.run("remove-app", "wip-kb").exit_code == 0
    assert harness.value(AA_KEY) == key
    assert harness.value(KB_KEY) is None
    # Re-adding an app that is already enabled is a no-op, key included.
    assert harness.run("add-app", "wip-aa").exit_code == 0
    assert harness.value(AA_KEY) == key
    assert kb is not None


def test_a_failure_after_the_change_landed_deletes_the_key(harness: _Harness) -> None:
    assert harness.run("add-app", "wip-aa").exit_code == 0
    harness.order.clear()
    harness.apply_effect = apply_mod.ApplyError("post-install hook failed", mutated=True)
    result = harness.run("remove-app", "wip-aa")
    assert result.exit_code == 1
    assert harness.order == ["apply", "stop wip-aa"]
    assert harness.value(AA_KEY) is None
    assert AA_KEY not in harness.recorded()


def test_a_failure_that_changed_nothing_keeps_the_key(harness: _Harness) -> None:
    assert harness.run("add-app", "wip-aa").exit_code == 0
    key = harness.value(AA_KEY)
    recorded = harness.recorded()
    harness.order.clear()
    harness.apply_effect = apply_mod.ApplyError("compose up refused", mutated=False)
    result = harness.run("remove-app", "wip-aa")
    assert result.exit_code == 1
    assert harness.order == ["apply"]
    # The app still runs, on the saved spec that lists it, with its key.
    assert harness.value(AA_KEY) == key
    assert harness.recorded() == recorded


def test_a_privileged_app_has_no_key_to_delete(harness: _Harness) -> None:
    assert harness.run("add-app", "react-console", "--privileged").exit_code == 0
    master = harness.value(MASTER)
    assert master
    result = harness.run("remove-app", "react-console")
    assert result.exit_code == 0, result.output
    assert harness.value(MASTER) == master
    assert "Deleted" not in result.output


def test_a_key_an_earlier_removal_left_in_the_store_is_not_reused(
    harness: _Harness,
) -> None:
    """A removal before this change kept the secret; adding the app again
    must still make a new key."""
    harness.store.mkdir(parents=True, exist_ok=True)
    (harness.store / AA_KEY).write_text("left-by-an-earlier-removal")
    result = harness.run("add-app", "wip-aa")
    assert result.exit_code == 0, result.output
    assert harness.value(AA_KEY) not in (None, "left-by-an-earlier-removal")


def test_k8s_removal_takes_the_key_out_of_the_live_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On k8s an apply cannot drop a key from the live Secret's stringData,
    so remove-app removes the removed app's value from it, and only that."""
    harness = _Harness(tmp_path, monkeypatch, target="k8s")
    assert harness.run("add-app", "wip-aa").exit_code == 0
    harness.live = {p.name: p.read_text() for p in harness.store.iterdir() if p.is_file()}
    others = {k: v for k, v in harness.live.items() if k != AA_KEY}
    result = harness.run("remove-app", "wip-aa")
    assert result.exit_code == 0, result.output
    assert harness.live == others
    assert "Removed app-wip-aa-api-key from the live wip-secrets" in result.output


def test_k8s_a_stale_live_value_is_replaced_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the cluster refuses the patch, the removed app's old value stays
    in the live Secret; the removal still succeeds with a warning, and adding
    the app again must not read the stale value as drift of the store."""
    harness = _Harness(tmp_path, monkeypatch, target="k8s")
    assert harness.run("add-app", "wip-aa").exit_code == 0
    old = harness.value(AA_KEY)
    live = {p.name: p.read_text() for p in harness.store.iterdir() if p.is_file()}
    harness.live = live
    harness.patch_effect = cli.LiveSecretReadUnavailable("forbidden")
    removed = harness.run("remove-app", "wip-aa")
    assert removed.exit_code == 0, removed.output
    assert "is still in the live wip-secrets" in removed.output
    assert harness.value(AA_KEY) is None
    # The live Secret still carries the old value (and every other one).
    assert harness.live[AA_KEY] == old
    harness.patch_effect = None
    result = harness.run("add-app", "wip-aa")
    assert result.exit_code == 0, result.output
    new = harness.value(AA_KEY)
    assert new and new != old


def test_a_disabled_app_reference_is_enabled_with_a_new_key(harness: _Harness) -> None:
    state = cli._load_deployment(harness.install_dir)
    state.spec.apps = [AppRef(name="wip-aa", enabled=False)]
    t1228._persist(harness.install_dir, state)
    harness.store.mkdir(parents=True, exist_ok=True)
    (harness.store / AA_KEY).write_text("from-before-it-was-disabled")
    result = harness.run("add-app", "wip-aa")
    assert result.exit_code == 0, result.output
    assert harness.value(AA_KEY) not in (None, "from-before-it-was-disabled")


class _Proc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


def test_the_live_patch_removes_only_that_value(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    calls: list[list[str]] = []
    monkeypatch.setattr(apply_mod, "read_live_secrets", lambda ns: {AA_KEY: "v", "api-key": "m"})
    monkeypatch.setattr(apply_mod, "kubectl", lambda *a: ["kubectl", *a])
    monkeypatch.setattr(
        apply_mod.subprocess, "run", lambda argv, **kw: calls.append(argv) or _Proc(),
    )
    assert apply_mod.remove_live_secret_value("wip", AA_KEY) is True
    [argv] = calls
    assert argv[:7] == ["kubectl", "patch", "secret", "wip-secrets", "-n", "wip", "--type=json"]
    assert json.loads(argv[-1]) == [{"op": "remove", "path": f"/data/{AA_KEY}"}]


def test_the_live_patch_is_not_sent_for_a_value_the_secret_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apply_mod, "read_live_secrets", lambda ns: {"api-key": "m"})
    monkeypatch.setattr(apply_mod.subprocess, "run", lambda *a, **k: pytest.fail("patched"))
    assert apply_mod.remove_live_secret_value("wip", AA_KEY) is False


def test_a_refused_live_patch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_mod, "read_live_secrets", lambda ns: {AA_KEY: "v"})
    monkeypatch.setattr(apply_mod, "kubectl", lambda *a: ["kubectl", *a])
    monkeypatch.setattr(apply_mod.subprocess, "run", lambda *a, **k: _Proc(1, "forbidden"))
    with pytest.raises(apply_mod.LiveSecretReadUnavailable, match="forbidden"):
        apply_mod.remove_live_secret_value("wip", AA_KEY)
