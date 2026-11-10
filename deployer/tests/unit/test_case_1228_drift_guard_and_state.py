"""CASE-1228 — items 1, 2, 3, 5a, 5c and 5d: silent failure around one install.

Item 1. `install` re-specifies its whole spec from CLI flags and never loads the
saved one, so any field the operator does not repeat reverts to its flag
default. The guard compared a hand-picked seven, which meant everything else —
`variant` (what arms the production guards), the k8s namespace, the auth lists,
`data_dir`, `secrets.location` — reset in silence. The guard now labels the
fields it names and structurally compares the rest, so a field added to the spec
later is guarded by default rather than by someone remembering.

Item 2. A state file that exists but cannot be understood made the loader return
None, which the install flow reads as "no previous install" — skipping both
guards and saying nothing about it.

Item 3. `deployment.json` — the pre-CASE-294 state file — was unlinked early in
install. It does have to go, because `kubectl apply -R -f <install_dir>` reads
the json in that directory, but the removal ran before cluster selection, secret
drift, the collision refusals and render, so an install whose only record was
that file could be left with no record at all, and the next re-run then had
nothing to compare against either.

Item 5c. `status --diff` called the secret resolver at its default `persist=True`
(a preview that mints passwords into the store) and passed no `install_dir`, so
the store was never checked against what the install actually runs — the one
command for reporting differences hid a drifted secret store.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

import wip_deploy.apply as apply_mod
import wip_deploy.cli as cli
from wip_deploy.cli import (
    _DEPLOYMENT_JSON_VERSION,
    _diff_spec_for_identity_drift,
    _drift_show,
    _previous_deployment_or_unreadable,
    _retire_legacy_state,
    _try_load_previous_deployment,
    app,
)
from wip_deploy.spec.deployment import (
    ApplySpec,
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    K8sPlatform,
    ModulesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

runner = CliRunner()

STATE = "deployment.deployer-state"
DATA_DIR = "/tmp/wip-test-data"


def _deployment(
    *,
    target: str = "compose",
    hostname: str = "wip.localhost",
    registry: str | None = None,
    auth_mode: str = "api-key-only",
    variant: str = "dev",
    users: list | None = None,
    data_dir: str = DATA_DIR,
    k8s_namespace: str | None = None,
    platform_variant: str = "default",
    secrets_location: str = "/tmp/wip-test-secrets",
) -> Deployment:
    """A compose- or k8s-target Deployment with the fields the guard weighs.

    `users=None` keeps the model default (three stock Dex accounts); an
    api-key-only install provisions none, so pass `users=[]` to match what
    `install` really produces.
    """
    network = NetworkSpec(hostname=hostname, tls="internal", https_port=8443, http_port=8080)
    return Deployment(
        metadata=DeploymentMetadata(name="guard"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            variant=variant,  # type: ignore[arg-type]
            apps=[],
            modules=ModulesSpec(optional=[]),
            auth=AuthSpec(
                mode=auth_mode,  # type: ignore[arg-type]
                gateway=False,
                **({} if users is None else {"users": users}),
            ),
            network=network,
            images=ImagesSpec(registry=registry),
            platform=PlatformSpec(
                compose=(
                    ComposePlatform(data_dir=Path(data_dir), platform_variant=platform_variant)  # type: ignore[arg-type]
                    if target != "k8s"
                    else None
                ),
                k8s=(
                    K8sPlatform(namespace=k8s_namespace or "wip")
                    if target == "k8s"
                    else None
                ),
            ),
            secrets=SecretsSpec(backend="file", location=secrets_location),
            apply=ApplySpec(),
        ),
    )


def _persist(install_dir: Path, deployment: Deployment) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
        "deployment": deployment.model_dump(mode="json"),
    }
    (install_dir / STATE).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _install(tmp_path: Path, *extra: str) -> SimpleNamespace:
    return runner.invoke(
        app,
        [
            "install",
            "--name", "guard",
            "--install-dir", str(tmp_path),
            "--hostname", "wip.localhost",
            "--auth-mode", "api-key-only",
            "--no-auth-gateway",
            "--data-dir", DATA_DIR,
            *extra,
        ],
    )


# ────────────────────────────────────────────────────────────────────
# Item 2 — an unreadable state file is not "no previous install"
# ────────────────────────────────────────────────────────────────────


class TestUnreadableStateIsClassified:
    def test_absent_state_is_not_a_problem(self, tmp_path: Path) -> None:
        assert _previous_deployment_or_unreadable(tmp_path) == (None, None)

    def test_valid_state_reads(self, tmp_path: Path) -> None:
        _persist(tmp_path, _deployment(users=[]))
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert reason is None and dep is not None

    def test_corrupt_json_is_reported_with_its_cause(self, tmp_path: Path) -> None:
        (tmp_path / STATE).write_text("{ not json at all")
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert dep is None and "JSONDecodeError" in reason

    def test_foreign_format_version_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / STATE).write_text(
            json.dumps({"wip_deploy_format_version": 99, "deployment": {}})
        )
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert dep is None and "format version 99" in reason

    def test_unparseable_model_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / STATE).write_text(
            json.dumps(
                {
                    "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
                    "deployment": {"nonsense": True},
                }
            )
        )
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert dep is None and "failed to deserialize" in reason

    def test_directory_in_place_of_file_does_not_traceback(
        self, tmp_path: Path
    ) -> None:
        # An unreadable file used to escape as a raw OSError: the loader caught
        # only JSONDecodeError, so EACCES/EISDIR skipped both documented paths.
        (tmp_path / STATE).mkdir()
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert dep is None and "IsADirectoryError" in reason

    def test_legacy_fallback_still_resolves(self, tmp_path: Path) -> None:
        """`deployment.json` is read when the canonical name is absent."""
        _persist(tmp_path, _deployment(users=[]))
        (tmp_path / STATE).rename(tmp_path / "deployment.json")
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert reason is None and dep is not None


class TestUnreadableStateIsAnnounced:
    def test_tolerant_loader_warns_instead_of_silence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / STATE).write_text("{ broken")
        assert _try_load_previous_deployment(tmp_path) is None
        err = capsys.readouterr().err
        assert "warning:" in err and "cannot compare" in err

    def test_absent_state_stays_quiet(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _try_load_previous_deployment(tmp_path) is None
        assert capsys.readouterr().err == ""

    def test_install_refuses_to_skip_both_guards_unannounced(
        self, tmp_path: Path
    ) -> None:
        _persist(tmp_path, _deployment(users=[]))
        (tmp_path / STATE).write_text("{ broken")
        result = _install(tmp_path)
        assert result.exit_code == 2
        assert "cannot read" in result.output
        assert "--reconcile" in result.output

    def test_reconcile_proceeds_but_says_the_guards_could_not_run(
        self, tmp_path: Path
    ) -> None:
        _persist(tmp_path, _deployment(users=[]))
        (tmp_path / STATE).write_text("{ broken")
        result = _install(tmp_path, "--reconcile")
        # Not 2: the acknowledgement was given, so the run continues to the next
        # gate (this invocation has no registry, so validation stops it).
        assert result.exit_code == 1
        assert "cannot run against this install" in result.output


# ────────────────────────────────────────────────────────────────────
# Item 1 — the guard covers what a flag default can overwrite
# ────────────────────────────────────────────────────────────────────


class TestDriftGuardCoverage:
    def test_identical_specs_are_silent(self) -> None:
        assert _diff_spec_for_identity_drift(_deployment(), _deployment()) == []

    def test_variant_reverting_is_drift(self) -> None:
        """`variant` arms the production guards; silently dropping it does not."""
        drift = _diff_spec_for_identity_drift(
            _deployment(variant="prod"), _deployment(variant="dev")
        )
        assert ("variant", "prod", "dev") in drift

    def test_k8s_namespace_reverting_is_drift(self) -> None:
        prev = _deployment(target="k8s", k8s_namespace="wip-frontend")
        curr = _deployment(target="k8s", k8s_namespace="wip")
        drift = _diff_spec_for_identity_drift(prev, curr)
        assert ("platform.k8s.namespace", "wip-frontend", "wip") in drift

    def test_field_outside_the_named_list_is_still_guarded(self) -> None:
        """The point of the structural pass: nothing depends on someone adding
        the next field to the enumeration."""
        drift = _diff_spec_for_identity_drift(
            _deployment(platform_variant="pi4"), _deployment(platform_variant="default")
        )
        assert (
            "platform.compose.platform_variant",
            "pi4",
            "default",
        ) in drift

    def test_secrets_location_change_is_drift(self) -> None:
        drift = _diff_spec_for_identity_drift(
            _deployment(secrets_location="/srv/wip/secrets"),
            _deployment(secrets_location="/tmp/wip-test-secrets"),
        )
        assert ("secrets.location", "/srv/wip/secrets", "/tmp/wip-test-secrets") in drift

    def test_a_list_that_changed_size_is_reported_once(self) -> None:
        """Element-wise reporting would call every entry different after the
        shift; the change is in the list, not in each of its members."""
        prev = _deployment(users=[{"email": "a@b.co", "username": "a", "groups": ["wip-admins"]}])
        drift = _diff_spec_for_identity_drift(prev, _deployment())
        assert ("auth.users", "1 item(s)", "3 item(s)") in drift
        assert not any(field.startswith("auth.users[") for field, _, _ in drift)

    def test_tag_rolls_are_not_drift(self) -> None:
        """Rolling images is what --tag means; it must not look like a reshape.
        `tag_overrides` is a mapping, so its entries are skipped with it."""
        prev = _deployment()
        curr = _deployment()
        curr.spec.images.tag = "20261110a"
        curr.spec.images.tag_overrides = {"registry": "20261111b"}
        assert _diff_spec_for_identity_drift(prev, curr) == []

    def test_apply_knobs_and_apps_are_left_to_their_own_handling(self) -> None:
        prev = _deployment()
        curr = _deployment()
        curr.spec.apply.wait = False
        curr.spec.apply.timeout_seconds = 30
        assert _diff_spec_for_identity_drift(prev, curr) == []

    def test_variant_reverting_stops_a_real_rerun(self, tmp_path: Path) -> None:
        """The security-relevant shape of item 1.

        `--variant prod` is what injects WIP_VARIANT=prod, and every backend
        service's production-safety guard returns early unless it reads exactly
        that (wip_auth/security.py, auth_gateway/config.py; the render side is
        pinned by config_gen/test_case_615.py). A re-run that omits --variant
        therefore does not drop a preference, it disarms the posture of the whole
        install — which is why it has to abort rather than proceed.
        """
        _persist(tmp_path, _deployment(users=[], variant="prod"))
        result = runner.invoke(
            app,
            [
                "install",
                "--name", "guard",
                "--install-dir", str(tmp_path),
                "--hostname", "wip.localhost",
                "--auth-mode", "api-key-only",
                "--no-auth-gateway",
                "--data-dir", DATA_DIR,
                # --variant omitted: that omission is the whole test
            ],
        )
        assert result.exit_code == 2
        assert "variant" in result.output
        assert "prod → dev" in result.output

    def test_data_dir_reverting_stops_a_real_rerun(self, tmp_path: Path) -> None:
        """The production shape of item 1: a saved install with a custom data
        directory, re-run without --data-dir."""
        _persist(tmp_path, _deployment(users=[], data_dir="/srv/wip-data"))
        result = runner.invoke(
            app,
            [
                "install",
                "--name", "guard",
                "--install-dir", str(tmp_path),
                "--hostname", "wip.localhost",
                "--auth-mode", "api-key-only",
                "--no-auth-gateway",
            ],
        )
        assert result.exit_code == 2
        assert "platform.compose.data_dir" in result.output

    def test_credential_shaped_paths_are_never_printed(self) -> None:
        assert _drift_show("hunter2", "auth.users[0].password") == (
            "(not printed — credential material)"
        )
        # Sensitive names can sit below the field the model declares: a
        # connector's config is provider-named free-form.
        assert _drift_show("x", "auth.connectors[0].config.clientSecret") == (
            "(not printed — credential material)"
        )
        # And the guard must not over-redact the fields an operator needs.
        assert _drift_show("wip-tls", "platform.k8s.tls_secret_name") == "wip-tls"
        assert _drift_show("/srv/s", "secrets.location") == "/srv/s"


# ────────────────────────────────────────────────────────────────────
# Item 5c — `status --diff` must not write, and must check what it reports on
# ────────────────────────────────────────────────────────────────────


class TestStatusDiffIsReadOnly:
    class _Stop(Exception):
        """Ends the walk once the secrets call has been observed."""

    def test_diff_resolves_secrets_read_only_and_checks_the_store(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[dict] = []

        def fake_ensure(deployment, components, apps_list, **kwargs):
            calls.append(kwargs)
            raise self._Stop

        monkeypatch.setattr(cli, "_load_deployment", lambda _dir: _deployment(target="k8s"))
        monkeypatch.setattr(cli, "_select_cluster_or_exit", lambda *a, **k: None)
        monkeypatch.setattr(cli, "_discover_or_exit", lambda _root: SimpleNamespace(components=[], apps=[]))
        monkeypatch.setattr(cli, "_ensure_secrets_via_spec", fake_ensure)
        monkeypatch.setattr(cli, "_load_state_repo_root", lambda _dir: tmp_path)

        with pytest.raises(self._Stop):
            cli._do_status_diff(tmp_path, None)

        assert len(calls) == 1
        # A preview that mints passwords leaves the store holding values no
        # install runs, and without install_dir the known-digests check never
        # fires — so a drifted store looked clean to the diff command.
        assert calls[0].get("persist") is False
        assert calls[0].get("install_dir") == tmp_path


# ────────────────────────────────────────────────────────────────────
# Item 3 — retiring the legacy state file must not destroy the record
# ────────────────────────────────────────────────────────────────────


def _write_legacy(
    install_dir: Path,
    deployment: Deployment | None = None,
    *,
    raw: str | None = None,
) -> Path:
    """Put a `deployment.json` in the install dir, valid unless `raw` is given."""
    install_dir.mkdir(parents=True, exist_ok=True)
    legacy = install_dir / "deployment.json"
    if raw is not None:
        legacy.write_text(raw)
        return legacy
    payload = {
        "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
        "deployment": (deployment or _deployment(users=[])).model_dump(mode="json"),
    }
    legacy.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    return legacy


class TestLegacyStateRetirement:
    def test_absent_legacy_file_is_a_no_op(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _retire_legacy_state(tmp_path)
        assert list(tmp_path.iterdir()) == []
        assert capsys.readouterr().err == ""

    def test_readable_legacy_is_carried_forward_before_removal(self, tmp_path: Path) -> None:
        legacy = _write_legacy(
            tmp_path, _deployment(users=[], hostname="old.example.com")
        )
        _retire_legacy_state(tmp_path)
        assert (tmp_path / STATE).exists(), "the spec must survive, not just move"
        assert not legacy.exists()
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert reason is None
        assert dep.spec.network.hostname == "old.example.com"

    def test_unreadable_legacy_is_parked_and_its_bytes_kept(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        legacy = _write_legacy(tmp_path, raw="{ broken beyond use")
        _retire_legacy_state(tmp_path)
        assert not (tmp_path / STATE).exists()
        assert not legacy.exists()
        parked = list(tmp_path.glob("deployment.json.retired*"))
        assert len(parked) == 1
        assert parked[0].read_text() == "{ broken beyond use"
        assert "warning:" in capsys.readouterr().err

    def test_parked_file_is_invisible_to_the_recursive_apply(
        self, tmp_path: Path
    ) -> None:
        """kubectl -R -f reads .json/.yaml/.yml in the install dir. If the
        parked name matched one of them, removing the old deletion would
        reintroduce the failure the deletion existed to prevent."""
        _write_legacy(tmp_path, raw="{ broken")
        _retire_legacy_state(tmp_path)
        for leftover in tmp_path.iterdir():
            assert leftover.suffix not in (".json", ".yaml", ".yml"), leftover

    def test_canonical_state_wins_over_a_conflicting_legacy(
        self, tmp_path: Path
    ) -> None:
        _persist(tmp_path, _deployment(users=[], hostname="current.example.com"))
        legacy = _write_legacy(
            tmp_path, _deployment(users=[], hostname="ancient.example.com")
        )
        _retire_legacy_state(tmp_path)
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert reason is None and dep.spec.network.hostname == "current.example.com"
        assert not legacy.exists()
        assert list(tmp_path.glob("deployment.json.retired*"))

    def test_a_second_unreadable_legacy_does_not_overwrite_the_first(
        self, tmp_path: Path
    ) -> None:
        _write_legacy(tmp_path, raw="{ first")
        _retire_legacy_state(tmp_path)
        _write_legacy(tmp_path, raw="{ second")
        _retire_legacy_state(tmp_path)
        parked = list(tmp_path.glob("deployment.json.retired*"))
        assert len(parked) == 2
        assert {p.read_text() for p in parked} == {"{ first", "{ second"}


# ────────────────────────────────────────────────────────────────────
# Item 5a — a dev tag roll cannot take effect, so it must not be claimed
# ────────────────────────────────────────────────────────────────────


def _persist_dev_install(
    install_dir: Path, *, apps_from_registry: list[str] | None = None
) -> None:
    """A dev-target install with one enabled app, which the dev renderer
    builds locally as `<image>:dev-<install>` unless the app opted into the
    registry."""
    deployment = Deployment(
        metadata=DeploymentMetadata(name="devguard"),
        spec=DeploymentSpec(
            target="dev",
            variant="dev",
            apps=[AppRef(name="cookbook", enabled=True)],
            modules=ModulesSpec(optional=[]),
            auth=AuthSpec(mode="api-key-only", gateway=False, users=[]),
            network=NetworkSpec(hostname="wip.localhost", tls="internal"),
            images=ImagesSpec(),
            platform=PlatformSpec(
                dev=DevPlatform(apps_from_registry=list(apps_from_registry or []))
            ),
            secrets=SecretsSpec(backend="file", location="/tmp/wip-test-secrets"),
            apply=ApplySpec(),
        ),
    )
    _persist(install_dir, deployment)


class TestDevTagRollIsRefused:
    def test_source_built_app_cannot_be_rolled_to_a_registry_tag(
        self, tmp_path: Path
    ) -> None:
        _persist_dev_install(tmp_path)
        result = runner.invoke(
            app,
            ["app-deploy", "cookbook", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "built from source" in result.output
        assert "--app-from-registry" in result.output

    def test_the_refused_roll_leaves_no_override_recorded(
        self, tmp_path: Path
    ) -> None:
        """The bug was not the missing container — it was the persisted intent,
        which later resurfaces and applies a tag nobody asked for."""
        _persist_dev_install(tmp_path)
        runner.invoke(
            app,
            ["app-deploy", "cookbook", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
        )
        dep, reason = _previous_deployment_or_unreadable(tmp_path)
        assert reason is None
        assert "cookbook" not in dep.spec.images.tag_overrides

    def test_a_registry_opted_app_still_rolls(self, tmp_path: Path) -> None:
        """--app-from-registry is the opt-in that makes the tag real; refusing
        that too would break the documented escape hatch."""
        _persist_dev_install(tmp_path, apps_from_registry=["cookbook"])
        applied: list[object] = []
        monkeypatch_marker = SimpleNamespace(applied=applied)

        def fake_apply(*args, **kwargs):
            monkeypatch_marker.applied.append(kwargs.get("services_scope"))

        import wip_deploy.cli as cli_module

        original = cli_module._apply_and_persist_mutation
        cli_module._apply_and_persist_mutation = fake_apply
        try:
            result = runner.invoke(
                app,
                ["app-deploy", "cookbook", "--tag", "sha-abc", "--install-dir", str(tmp_path)],
            )
        finally:
            cli_module._apply_and_persist_mutation = original
        assert result.exit_code == 0, result.output
        assert applied, "the roll must proceed for a registry-sourced app"


# ────────────────────────────────────────────────────────────────────
# Item 5d — "no Secret yet" and "could not look" are different answers
# ────────────────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestLiveSecretReadDistinguishes:
    def test_absent_secret_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The cluster's own wording for a miss, confirmed against a live
        # namespace that holds no wip-secrets.
        monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/kubectl")
        monkeypatch.setattr(
            apply_mod.subprocess,
            "run",
            lambda *a, **k: _FakeProc(
                1,
                stderr='Error from server (NotFound): secrets "wip-secrets" not found\n',
            ),
        )
        assert apply_mod.read_live_secrets("wip") is None

    def test_missing_kubectl_is_not_an_absence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _x: None)
        with pytest.raises(apply_mod.LiveSecretReadUnavailable, match="not on PATH"):
            apply_mod.read_live_secrets("wip")

    def test_refusal_is_not_an_absence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/kubectl")
        monkeypatch.setattr(
            apply_mod.subprocess,
            "run",
            lambda *a, **k: _FakeProc(
                1,
                stderr='Error from server (Forbidden): cannot get resource "secrets"',
            ),
        )
        with pytest.raises(apply_mod.LiveSecretReadUnavailable, match="Forbidden"):
            apply_mod.read_live_secrets("wip")

    def test_unreachable_cluster_is_not_an_absence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/kubectl")
        monkeypatch.setattr(
            apply_mod.subprocess,
            "run",
            lambda *a, **k: _FakeProc(
                1, stderr="The connection to the server 10.0.0.1:6443 was refused"
            ),
        )
        with pytest.raises(apply_mod.LiveSecretReadUnavailable, match="refused"):
            apply_mod.read_live_secrets("wip")

    def test_undecodable_payload_is_not_an_absence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/kubectl")
        monkeypatch.setattr(
            apply_mod.subprocess, "run", lambda *a, **k: _FakeProc(0, stdout="{not json")
        )
        with pytest.raises(apply_mod.LiveSecretReadUnavailable, match="could not be decoded"):
            apply_mod.read_live_secrets("wip")

    def test_a_real_secret_still_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import base64

        monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/kubectl")
        payload = json.dumps({"data": {"api-key": base64.b64encode(b"v").decode()}})
        monkeypatch.setattr(
            apply_mod.subprocess, "run", lambda *a, **k: _FakeProc(0, stdout=payload)
        )
        assert apply_mod.read_live_secrets("wip") == {"api-key": "v"}


class TestDriftCheckStopsWhenTheClusterCannotBeAsked:
    def test_known_digests_do_not_fall_back_to_the_host_record_silently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect: the guard read None from a failed call and compared
        against the host record alone, certifying an install against a cluster it
        never looked at."""
        _persist(tmp_path, _deployment(target="k8s", k8s_namespace="wip-prod"))

        def boom(_ns: str):
            raise apply_mod.LiveSecretReadUnavailable(
                "kubectl could not read secret wip-secrets in namespace 'wip-prod': refused"
            )

        monkeypatch.setattr(cli, "read_live_secrets", boom)
        deployment, _reason = _previous_deployment_or_unreadable(tmp_path)
        with pytest.raises(typer.Exit) as exc:
            cli._known_secret_digests(deployment, tmp_path)
        assert exc.value.exit_code == 2
