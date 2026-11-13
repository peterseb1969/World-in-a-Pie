"""Forward-review findings on the CASE-1228 remove-app fix.

- A failure after the change landed (`mutated=True`) is reported as a change
  that is applied and saved with a later step failing, not as a bare "failed":
  the removal did happen.
- `remove-app` for an app the install no longer lists sweeps the container an
  earlier removal left running (compose and dev), never a component's
  container, and never on k8s.
- The verb's help says how the app goes on k8s.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import wip_deploy.apply as apply_mod
import wip_deploy.cli as cli
from wip_deploy.cli import app

from . import test_case_1228_drift_guard_and_state as t1228

runner = CliRunner()


class TestAFailureAfterTheChangeSaysTheChangeHappened:
    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effect: object):
        harness = t1228.TestRemoveAppContainerFollowsTheSavedSpec()
        return harness._run(tmp_path, monkeypatch, effect)

    def test_a_mutated_failure_says_applied_and_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, _ = self._run(
            tmp_path, monkeypatch,
            apply_mod.ApplyError("post-install hook 'x' on 'y' failed (exit 1)", mutated=True),
        )
        assert result.exit_code == 1
        assert "the change is applied and saved, but a later step" in result.output
        assert " from default failed" not in result.output

    def test_a_failure_that_changed_nothing_still_reads_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, _ = self._run(
            tmp_path, monkeypatch,
            apply_mod.ApplyError("compose up refused", mutated=False),
        )
        assert result.exit_code == 1
        assert "Removed app 'react-console' from" in result.output
        assert "failed: compose up refused" in result.output
        assert "applied and saved" not in result.output


class TestRemoveAppSweepsALeftoverContainer:
    def _run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_name: str, *, target: str = "compose",
    ) -> tuple[object, list[str]]:
        state = t1228._deployment(users=[], target=target)
        t1228._persist(tmp_path, state)
        stopped: list[str] = []
        monkeypatch.setattr(
            apply_mod, "stop_and_remove_container",
            lambda install, name: stopped.append(f"{install} {name}"),
        )
        # Nothing may be applied: the spec does not change.
        monkeypatch.setattr(cli, "_apply_and_persist_mutation", lambda *a, **k: pytest.fail("applied"))
        result = runner.invoke(app, ["remove-app", app_name, "--install-dir", str(tmp_path)])
        return result, stopped

    def test_a_known_app_the_spec_no_longer_lists_is_swept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, stopped = self._run(tmp_path, monkeypatch, "react-console")
        assert result.exit_code == 0, result.output
        assert stopped == ["guard react-console"]
        assert "any container it left behind was removed" in result.output

    def test_a_component_name_is_never_swept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, stopped = self._run(tmp_path, monkeypatch, "registry")
        assert result.exit_code == 0, result.output
        assert stopped == []
        assert "no change." in result.output

    def test_an_unknown_name_is_never_swept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, stopped = self._run(tmp_path, monkeypatch, "no-such-app")
        assert result.exit_code == 0, result.output
        assert stopped == []

    def test_k8s_has_no_container_to_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_select_cluster_or_exit", lambda *a, **k: None)
        result, stopped = self._run(tmp_path, monkeypatch, "react-console", target="k8s")
        assert result.exit_code == 0, result.output
        assert stopped == []


def test_the_help_says_how_the_app_goes_on_k8s() -> None:
    result = runner.invoke(app, ["remove-app", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "whole-install apply with prune" in " ".join(result.output.split())
