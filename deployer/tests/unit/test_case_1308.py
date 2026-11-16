"""`add-app` reports an app ready only once the Registry accepts its key.

Provenance: CASE-1308. An app added to a k8s install started with a key the
Registry did not know yet: the apply writes api-keys.json, but the Registry
learns it only by re-importing the file it mounts, and on k8s that file changes
when the kubelet refreshes the Secret. The app's own first requests fall in that
window and come back 401, which the app reads as "WIP is unreachable" — while
`add-app` had already reported success, because all it waited for was the pod.
Since CASE-512 every remove-app + add-app mints a new key, so this hit the
operator on the re-add of a working app too.

Contract under test: `add-app` of an app that gets a new key names the window
before it waits, waits for the Registry to accept that key, and fails if the key
is still refused once the window has passed (the app is applied and saved, but
it is not reachable). A privileged app, which has no key of its own, and a
change that declares no new key are not probed. A probe that could not ask — no
trust anchor, or a store with nothing readable to send — is reported as not run
and does not fail the command, as with rotate-key.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import wip_deploy.cli as cli
import wip_deploy.verify_security as verify_security
from wip_deploy.apply import ApplyResult
from wip_deploy.cli import app
from wip_deploy.tls_trust import Untrusted

from . import test_case_1228_drift_guard_and_state as t1228

runner = CliRunner()
AA_KEY = "app-wip-aa"
AA_SECRET = f"{AA_KEY}-api-key"

# The window `_key_arrival_note` names for k8s: about three minutes, 89 sleeps
# of two seconds between 90 attempts.
FULL_WINDOW = 90


class _Probe(NamedTuple):
    """One recorded answer to the Registry probe."""

    new_key: str
    old_key: str | None
    attempts: int


class _Harness:
    """Drives add-app through the real secret generation and state persistence;
    validation, rendering and the apply are stubbed, and the Registry probe is
    answered from `problems` — no unit test reaches an install."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str = "compose"):
        self.install_dir = tmp_path / "install"
        self.store = tmp_path / "secrets"
        self.probes: list[_Probe] = []
        self.problems: list[str] = []
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

        def apply(**_kw: object) -> ApplyResult:
            return ApplyResult(install_dir=self.install_dir, services_up=0, healthy=True)

        monkeypatch.setattr(cli, "apply_compose", apply)
        monkeypatch.setattr(cli, "apply_k8s", apply)

        def probe(
            _deployment: object, new_key: str, old_key: str | None,
            *, attempts: int = 30, verify: object = True,
        ) -> list[str]:
            self.probes.append(_Probe(new_key, old_key, attempts))
            return self.problems

        monkeypatch.setattr(verify_security, "check_key_rotation", probe)

    def run(self, *args: str):
        return runner.invoke(app, [*args, "--install-dir", str(self.install_dir)])

    def value(self, secret_name: str) -> str | None:
        path = self.store / secret_name
        return path.read_text() if path.exists() else None


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(tmp_path, monkeypatch)


class TestTheWindowIsNamed:
    """Even where the probe cannot run, the operator is told what to expect: the
    key needs up to three minutes on k8s, and 401 inside that window is the key
    not having arrived, not an outage."""

    def test_adding_an_app_names_the_window_before_waiting(self, harness: _Harness) -> None:
        result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 0, result.output
        out = result.output
        assert "api-keys.json" in out
        assert "about three minutes on k8s" in out
        assert f"every request {AA_KEY} makes is refused with 401" in out
        assert out.index("three minutes") < out.index("Waiting for the Registry")

    def test_the_rotation_wording_keeps_its_sentence(self) -> None:
        """The two verbs share one sentence: extracting it for add-app must not
        reword what rotate-key has been saying."""
        line = cli._rotation_restart_lines("compose", [], declared_key=True)[0]
        assert line == (
            "The Registry re-imports api-keys.json while running and every service "
            "learns the change by key sync: the new key is accepted, and the old "
            "one refused, within about 40 s on compose and dev (10 s import, 30 s "
            "key sync), and within about three minutes on k8s, where the kubelet "
            "first refreshes the mounted file. Nothing restarts for it."
        )
        assert "and the old one refused" not in cli._key_arrival_note(old_key_refused=False)


class TestTheWait:
    def test_waits_for_the_key_the_app_holds(self, harness: _Harness) -> None:
        result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 0, result.output
        [probe] = harness.probes
        # The value the app starts with is the one asked about, and it is asked
        # for the whole k8s window. There is no old key: nothing is revoked.
        assert probe.new_key == harness.value(AA_SECRET)
        assert probe.old_key is None
        assert probe.attempts == FULL_WINDOW
        assert f"✓ the Registry accepts {AA_KEY}." in result.output

    def test_a_key_the_registry_never_accepts_fails_the_command(
        self, harness: _Harness
    ) -> None:
        harness.problems = ["the new key is not accepted at https://wip.example (last answer: 401)"]
        result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 1
        assert f"✗ the Registry did not accept {AA_KEY}" in result.output
        assert "applied and saved" in result.output
        assert "401" in result.output
        # The verb still did its work: the install has the app, and waiting for
        # the Registry is what failed, not the apply.
        assert [a.name for a in cli._load_deployment(harness.install_dir).spec.apps] == ["wip-aa"]

    def test_an_install_that_never_answered_is_not_called_a_refusal(
        self, harness: _Harness
    ) -> None:
        # No answer still fails add-app (exit 0 means ready), but the operator
        # is told it may be the install, not the key.
        harness.problems = [
            "the new key is not accepted at https://wip.example (last answer: no response)"
        ]
        result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 1
        assert "refuses" not in result.output
        assert "no response" in result.output
        assert "check that it is reachable" in result.output

    def test_a_readded_app_is_asked_about_its_new_key(
        self, harness: _Harness
    ) -> None:
        """remove-app revokes the key and add-app mints another, so every
        removal and re-add runs the app through the window again."""
        assert harness.run("add-app", "wip-aa").exit_code == 0
        first = harness.value(AA_SECRET)
        assert harness.run("remove-app", "wip-aa").exit_code == 0
        harness.probes.clear()
        assert harness.run("add-app", "wip-aa").exit_code == 0
        [probe] = harness.probes
        assert probe.new_key == harness.value(AA_SECRET)
        assert probe.new_key != first

    def test_no_key_is_sent_without_a_trust_anchor(self, harness: _Harness) -> None:
        with patch("wip_deploy.tls_trust.trust_anchor", return_value=Untrusted("no CA yet")):
            result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 0, result.output
        assert "key check not run: no CA yet" in result.output
        assert "three minutes" in result.output
        assert harness.probes == []

    def test_a_probe_that_cannot_verify_is_not_run_rather_than_refused(
        self, harness: _Harness
    ) -> None:
        def unverified(*_a: object, **_k: object) -> list[str]:
            raise verify_security.RotationNotChecked("the certificate did not verify")

        with patch("wip_deploy.verify_security.check_key_rotation", side_effect=unverified):
            result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 0, result.output
        assert "key check not run: the certificate did not verify" in result.output


class TestNothingToWaitFor:
    def test_a_privileged_app_has_no_key_of_its_own(self, harness: _Harness) -> None:
        result = harness.run("add-app", "react-console", "--privileged")
        assert result.exit_code == 0, result.output
        assert harness.probes == []
        assert "three minutes" not in result.output

    def test_a_change_that_declares_no_new_key_is_not_probed(
        self, harness: _Harness
    ) -> None:
        """An app the install already runs keeps its key, which the Registry
        accepted when it arrived; a tag roll has nothing new to wait for."""
        assert harness.run("add-app", "wip-aa").exit_code == 0
        harness.probes.clear()
        result = harness.run("add-app", "wip-aa", "--tag", "20261113d")
        assert result.exit_code == 0, result.output
        assert harness.probes == []
        assert "three minutes" not in result.output

    def test_a_store_holding_nothing_readable_says_the_check_was_not_run(
        self, harness: _Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_read_file_secret", lambda *_a: None)
        result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 0, result.output
        assert f"key check not run: the install's secret store holds no readable {AA_SECRET}" in (
            result.output
        )
        assert harness.probes == []

    def test_an_already_enabled_app_says_no_change_at_all(self, harness: _Harness) -> None:
        assert harness.run("add-app", "wip-aa").exit_code == 0
        harness.probes.clear()
        result = harness.run("add-app", "wip-aa")
        assert result.exit_code == 0, result.output
        assert "no change" in result.output
        assert harness.probes == []


class TestProbe:
    """`_probe_key_at_registry` is the one place the CLI asks the install about
    a key; the trust and outcome mapping both verbs rely on lives here."""

    def test_forwards_the_key_the_window_and_the_anchor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        def probe(deployment: object, new_key: str, old_key: str | None, **kw: object) -> list[str]:
            seen.update({"new": new_key, "old": old_key, **kw})
            return []

        monkeypatch.setattr(verify_security, "check_key_rotation", probe)
        acceptance = cli._probe_key_at_registry(
            t1228._deployment(), "new-key", "old-key", attempts=9,
        )
        assert acceptance.problems == () and acceptance.not_run is None
        assert seen["new"] == "new-key" and seen["old"] == "old-key"
        assert seen["attempts"] == 9 and seen["verify"] is True

    def test_problems_are_carried_as_the_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            verify_security, "check_key_rotation",
            lambda *_a, **_k: ["the new key is not accepted (last answer: 401)"],
        )
        acceptance = cli._probe_key_at_registry(t1228._deployment(), "k", None, attempts=2)
        assert acceptance.not_run is None
        assert acceptance.problems == ("the new key is not accepted (last answer: 401)",)

    def test_no_anchor_and_an_unverified_certificate_are_both_not_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []
        monkeypatch.setattr(
            verify_security, "check_key_rotation",
            lambda *_a, **_k: called.append("dialed") or [],
        )
        unreachable = Untrusted("the cluster is not reachable")
        monkeypatch.setattr("wip_deploy.tls_trust.trust_anchor", lambda _d: unreachable)
        assert cli._probe_key_at_registry(
            t1228._deployment(), "k", None, attempts=2
        ).not_run == "the cluster is not reachable"

        def unverified(*_a: object, **_k: object) -> list[str]:
            raise verify_security.RotationNotChecked("the certificate did not verify")

        monkeypatch.setattr("wip_deploy.tls_trust.trust_anchor", lambda _d: True)
        monkeypatch.setattr(verify_security, "check_key_rotation", unverified)
        assert cli._probe_key_at_registry(
            t1228._deployment(), "k", None, attempts=2
        ).not_run == "the certificate did not verify"
        assert called == []


def test_the_window_is_the_three_minutes_the_note_names() -> None:
    # The probe sleeps between attempts, not after the last one.
    assert (cli._KEY_ARRIVAL_ATTEMPTS - 1) * 2 >= 175

