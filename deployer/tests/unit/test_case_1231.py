"""The deployer CLI's smaller defects: operator files, connector values,
certificate fingerprints, bundle import, apps-only auth, purge-all pods,
per-install secrets, the --no-wait outcome line and discovery's contract."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import (
    _connector_value,
    _install_secrets_location,
    _load_operator_yaml,
    _pem_fingerprint,
    _read_operator_file,
    app,
)
from wip_deploy.discovery import discover
from wip_deploy.nuke import nuke_purge_all

runner = CliRunner()

# A real (tiny) DER body is not needed: the fingerprint hashes whatever the
# base64 decodes to. Two distinct bodies stand in for a chain.
_CERT_A = b"-----BEGIN CERTIFICATE-----\nQUFBQQ==\n-----END CERTIFICATE-----\n"
_CERT_B = b"-----BEGIN CERTIFICATE-----\nQkJCQg==\n-----END CERTIFICATE-----\n"


class TestOperatorFiles:
    def test_a_missing_file_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        with pytest.raises(typer.Exit) as e:
            _read_operator_file(tmp_path / "nope.yaml")
        assert e.value.exit_code == 2

    def test_malformed_yaml_is_an_error(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text("a: [unclosed\n")
        with pytest.raises(typer.Exit) as e:
            _load_operator_yaml(f)
        assert e.value.exit_code == 2


class TestConnectorValues:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("[openid, email]", ["openid", "email"]),
            ("20250101", "20250101"),
            ("1.5", "1.5"),
            ("null", "null"),
            ("plain", "plain"),
            ("", ""),
        ],
    )
    def test_numbers_and_null_stay_strings(self, raw: str, expected: object) -> None:
        assert _connector_value(raw) == expected


class TestFingerprint:
    def test_crlf_pem_is_read(self) -> None:
        crlf = _CERT_A.replace(b"\n", b"\r\n")
        assert _pem_fingerprint(crlf) == _pem_fingerprint(_CERT_A) != "<malformed>"

    def test_a_chain_lists_every_certificate(self) -> None:
        out = _pem_fingerprint(_CERT_A + _CERT_B)
        assert out.startswith(_pem_fingerprint(_CERT_A))
        assert "+1 more in the chain" in out
        assert _pem_fingerprint(_CERT_B) in out

    def test_no_certificate_is_malformed(self) -> None:
        assert _pem_fingerprint(b"nothing here") == "<malformed>"


def test_import_bundle_refuses_a_path_as_a_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--name ../x` would write secrets outside ~/.wip-deploy/; it is
    refused before anything is created."""
    monkeypatch.setattr("wip_deploy.cli._default_install_dir", lambda n: tmp_path / "root" / n)
    monkeypatch.setattr(
        "wip_deploy.import_bundle.parse_bundle", lambda raw: SimpleNamespace(),
    )
    bundle = tmp_path / "b.yaml"
    bundle.write_text("x: 1\n")
    r = runner.invoke(app, ["import-bundle", str(bundle), "--name", "../escape"])
    assert r.exit_code == 2, r.output
    assert "not an install name" in r.output
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "root").exists()


class TestAppsOnlyAuth:
    @pytest.mark.parametrize(
        "overrides", [{"auth_mode": "oidc"}, {"auth_gateway": True}],
    )
    def test_conflicting_auth_override_is_refused_by_name(self, overrides: dict) -> None:
        with pytest.raises(ValueError, match="--apps-only runs with auth.mode=api-key-only"):
            build_deployment(BuildInputs(apps_only=True, apps=["wip-kb"], **overrides))

    def test_api_key_only_is_accepted(self) -> None:
        d = build_deployment(
            BuildInputs(
                apps_only=True, apps=["wip-kb"], auth_mode="api-key-only",
                compose_data_dir=Path("/tmp/wip-data"),
            ),
        )
        assert d.spec.auth.mode == "api-key-only"


def test_purge_all_removes_each_installs_pod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """podman-compose names an install's pod after the install (`pod_tc02`);
    the fixed patterns never matched it."""
    import subprocess

    for name in ("tc02", "default"):
        (tmp_path / name).mkdir()
    pods = ["pod_tc02", "pod_default", "pod_unrelated"]
    monkeypatch.setattr(
        "wip_deploy.nuke._podman_ls_names", lambda args: pods if args[0] == "pod" else [],
    )
    monkeypatch.setattr(
        "wip_deploy.nuke._podman", lambda args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    report = nuke_purge_all(install_root=tmp_path)
    assert sorted(report.pods_removed) == ["pod_default", "pod_tc02"]


class TestInstallSecretsLocation:
    def test_install_dir_keeps_its_own_secrets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("wip_deploy.cli._try_load_previous_deployment", lambda d: None)
        assert _install_secrets_location(None, tmp_path) == str(tmp_path / "secrets")

    def test_an_existing_install_keeps_its_recorded_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        saved = SimpleNamespace(spec=SimpleNamespace(secrets=SimpleNamespace(location="/old/secrets")))
        monkeypatch.setattr("wip_deploy.cli._try_load_previous_deployment", lambda d: saved)
        assert _install_secrets_location(None, tmp_path) == "/old/secrets"

    def test_explicit_and_default_are_untouched(self, tmp_path: Path) -> None:
        assert _install_secrets_location("/x", tmp_path) == "/x"
        assert _install_secrets_location(None, None) is None


def test_no_wait_is_not_reported_as_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    from wip_deploy.apply import ApplyResult
    from wip_deploy.cli import _echo_apply_outcome

    _echo_apply_outcome(
        ApplyResult(install_dir=Path("."), services_up=3, healthy=True, health_checked=False),
        "Install complete", "Install",
    )
    out = capsys.readouterr().out
    assert "health not checked" in out
    assert "✓" not in out


def test_discovery_collects_a_non_utf8_manifest(tmp_path: Path) -> None:
    """discover() promises never to raise; a manifest that is not UTF-8
    becomes a collected error."""
    comp = tmp_path / "components" / "broken"
    comp.mkdir(parents=True)
    (comp / "wip-component.yaml").write_bytes(b"name: \xff\xfe\n")
    (tmp_path / "apps").mkdir()
    result = discover(tmp_path)
    assert any("broken" in str(e.path) for e in result.errors)
