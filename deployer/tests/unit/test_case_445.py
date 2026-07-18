"""Regression coverage for CASE-445 — `wip-deploy verify --security`.

The v2 successor to the retired v1 `production-check.sh` (CASE-383).
Covers:

  1. **Hostname classification** — the public/private split every
     exposure-sensitive check keys on.
  2. **Individual check functions** — each `check_*` returns the right
     CheckResult given a controlled fixture install.
  3. **The report roll-up** — verify_security over a healthy fixture.
  4. **CLI integration** — exit codes + the no-state / no-flag paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.spec import Deployment
from wip_deploy.verify_security import (
    check_api_key_strength,
    check_caddy_security_headers,
    check_published_ports,
    check_secret_permissions,
    check_tls_hostname_sanity,
    check_variant_exposure,
    is_public_hostname,
    verify_security,
)

runner = CliRunner()


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _make_deployment(
    tmp_path: Path,
    *,
    variant: str = "dev",
    hostname: str = "localhost",
    tls: str = "internal",
    target: str = "compose",
) -> Deployment:
    """A valid Deployment whose file-backend secrets live under tmp_path."""
    d = build_deployment(
        BuildInputs(
            target=target,
            variant=variant,
            hostname=hostname,
            tls=tls,
            compose_data_dir=tmp_path / "data",
        )
    )
    secrets = d.spec.secrets.model_copy(
        update={"location": str(tmp_path / "secrets")}
    )
    spec = d.spec.model_copy(update={"secrets": secrets})
    return d.model_copy(update={"spec": spec})


def _make_secrets(tmp_path: Path, *, api_key: str = "kR7mX2pQ9vL4nB8wZ3cF6a") -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700, exist_ok=True)
    key_file = secrets / "api-key"
    key_file.write_text(api_key)
    key_file.chmod(0o600)
    return secrets


def _write_compose(tmp_path: Path, ports_by_service: dict[str, list]) -> Path:
    compose = {
        "services": {
            svc: {"image": "x", "ports": ports}
            for svc, ports in ports_by_service.items()
        }
    }
    path = tmp_path / "docker-compose.yaml"
    path.write_text(yaml.safe_dump(compose))
    return path


# ────────────────────────────────────────────────────────────────────
# Hostname classification
# ────────────────────────────────────────────────────────────────────


class TestIsPublicHostname:
    def test_private_shapes(self) -> None:
        for h in (
            "localhost",
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.20",
            "wip.local",
            "gitea.internal",
            "router.lan",
            "pi.home.arpa",
            "wip-pi",  # dotless single label
            "",
        ):
            assert not is_public_hostname(h), h

    def test_public_shapes(self) -> None:
        for h in ("wip.example.com", "example.org", "8.8.8.8"):
            assert is_public_hostname(h), h


# ────────────────────────────────────────────────────────────────────
# Secret permissions
# ────────────────────────────────────────────────────────────────────


class TestCheckSecretPermissions:
    def test_correct_modes_pass(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path)
        d = _make_deployment(tmp_path)
        r = check_secret_permissions(tmp_path, d)
        assert r.passed

    def test_world_readable_file_fails(self, tmp_path: Path) -> None:
        secrets = _make_secrets(tmp_path)
        (secrets / "api-key").chmod(0o644)
        d = _make_deployment(tmp_path)
        r = check_secret_permissions(tmp_path, d)
        assert not r.passed
        assert "api-key" in r.message

    def test_group_accessible_dir_fails(self, tmp_path: Path) -> None:
        secrets = _make_secrets(tmp_path)
        secrets.chmod(0o750)
        d = _make_deployment(tmp_path)
        r = check_secret_permissions(tmp_path, d)
        assert not r.passed

    def test_loose_env_file_fails(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path)
        env = tmp_path / ".env"
        env.write_text("SECRET=x\n")
        env.chmod(0o644)
        d = _make_deployment(tmp_path)
        r = check_secret_permissions(tmp_path, d)
        assert not r.passed
        assert ".env" in r.message

    def test_missing_dir_fails(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path)
        r = check_secret_permissions(tmp_path, d)
        assert not r.passed

    def test_non_file_backend_not_applicable(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path)
        secrets = d.spec.secrets.model_copy(update={"backend": "k8s-secret"})
        spec = d.spec.model_copy(update={"secrets": secrets})
        d = d.model_copy(update={"spec": spec})
        r = check_secret_permissions(tmp_path, d)
        assert r.passed
        assert "not applicable" in r.message


# ────────────────────────────────────────────────────────────────────
# API key strength
# ────────────────────────────────────────────────────────────────────


class TestCheckApiKeyStrength:
    def test_random_key_passes(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path)
        r = check_api_key_strength(tmp_path, _make_deployment(tmp_path))
        assert r.passed

    def test_documented_default_fails(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, api_key="dev_master_key_for_testing")
        r = check_api_key_strength(tmp_path, _make_deployment(tmp_path))
        assert not r.passed
        assert "default" in r.message

    def test_short_key_fails(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, api_key="abc123")
        r = check_api_key_strength(tmp_path, _make_deployment(tmp_path))
        assert not r.passed

    def test_missing_key_fails(self, tmp_path: Path) -> None:
        (tmp_path / "secrets").mkdir(mode=0o700)
        r = check_api_key_strength(tmp_path, _make_deployment(tmp_path))
        assert not r.passed


# ────────────────────────────────────────────────────────────────────
# TLS / hostname / variant
# ────────────────────────────────────────────────────────────────────


class TestCheckTlsHostnameSanity:
    def test_internal_on_private_hostname_passes(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path, hostname="wip.local", tls="internal")
        assert check_tls_hostname_sanity(d.spec.network).passed

    def test_internal_on_public_hostname_fails(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path, hostname="wip.example.com", tls="internal")
        r = check_tls_hostname_sanity(d.spec.network)
        assert not r.passed
        assert "wip.example.com" in r.message


class TestCheckVariantExposure:
    def test_letsencrypt_dev_variant_fails(self, tmp_path: Path) -> None:
        """The check the original proposal predates: a prod-shaped install
        with every startup guard disarmed."""
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="letsencrypt", variant="dev"
        )
        r = check_variant_exposure(d)
        assert not r.passed
        assert "variant='dev'" in r.message

    def test_letsencrypt_prod_variant_passes(self, tmp_path: Path) -> None:
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="letsencrypt", variant="prod"
        )
        assert check_variant_exposure(d).passed

    def test_public_hostname_internal_tls_dev_fails(self, tmp_path: Path) -> None:
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="internal", variant="dev"
        )
        assert not check_variant_exposure(d).passed

    def test_local_dev_install_passes(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path)
        assert check_variant_exposure(d).passed


# ────────────────────────────────────────────────────────────────────
# Published ports
# ────────────────────────────────────────────────────────────────────


class TestCheckPublishedPorts:
    def test_caddy_only_passes(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, {"caddy": ["8443:8443"]})
        d = _make_deployment(tmp_path)
        r = check_published_ports(tmp_path, d)
        assert r.passed
        assert "8443" in r.message

    def test_published_mongo_fails(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, {"mongodb": ["27017:27017"], "caddy": ["8443:8443"]})
        d = _make_deployment(tmp_path)
        r = check_published_ports(tmp_path, d)
        assert not r.passed
        assert "MongoDB" in r.message

    def test_long_syntax_and_host_ip_forms(self, tmp_path: Path) -> None:
        _write_compose(
            tmp_path,
            {
                "nats": [{"published": 8222, "target": 8222}],
                "postgres": ["127.0.0.1:5433:5432"],
            },
        )
        d = _make_deployment(tmp_path)
        r = check_published_ports(tmp_path, d)
        assert not r.passed
        assert "NATS monitor" in r.message
        assert "PostgreSQL" in r.message

    def test_bare_container_port_is_published(self, tmp_path: Path) -> None:
        """Compose publishes a bare port to an ephemeral host port —
        still host-reachable, still a finding."""
        _write_compose(tmp_path, {"minio": ["9001"]})
        d = _make_deployment(tmp_path)
        r = check_published_ports(tmp_path, d)
        assert not r.passed
        assert "MinIO console" in r.message

    def test_no_compose_file_not_applicable(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path)
        r = check_published_ports(tmp_path, d)
        assert r.passed
        assert "not applicable" in r.message


# ────────────────────────────────────────────────────────────────────
# Caddyfile security headers
# ────────────────────────────────────────────────────────────────────


class TestCheckCaddySecurityHeaders:
    def _write_caddyfile(self, tmp_path: Path, text: str) -> None:
        caddy_dir = tmp_path / "config" / "caddy"
        caddy_dir.mkdir(parents=True)
        (caddy_dir / "Caddyfile").write_text(text)

    def test_internal_tls_passes_with_note(self, tmp_path: Path) -> None:
        d = _make_deployment(tmp_path)
        r = check_caddy_security_headers(tmp_path, d)
        assert r.passed
        assert "LAN-shaped" in r.message

    def test_letsencrypt_without_hsts_fails(self, tmp_path: Path) -> None:
        self._write_caddyfile(tmp_path, "example.com {\n  respond OK\n}\n")
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="letsencrypt"
        )
        r = check_caddy_security_headers(tmp_path, d)
        assert not r.passed
        assert r.fix_hint is not None

    def test_letsencrypt_with_hsts_passes(self, tmp_path: Path) -> None:
        self._write_caddyfile(
            tmp_path,
            'example.com {\n  header Strict-Transport-Security "max-age=31536000"\n}\n',
        )
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="letsencrypt"
        )
        assert check_caddy_security_headers(tmp_path, d).passed

    def test_letsencrypt_missing_caddyfile_fails(self, tmp_path: Path) -> None:
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="letsencrypt"
        )
        assert not check_caddy_security_headers(tmp_path, d).passed


# ────────────────────────────────────────────────────────────────────
# Report roll-up + CLI
# ────────────────────────────────────────────────────────────────────


class TestVerifySecurityReport:
    def test_healthy_dev_install_all_pass(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path)
        _write_compose(tmp_path, {"caddy": ["8443:8443"]})
        d = _make_deployment(tmp_path)
        report = verify_security(tmp_path, d)
        assert report.ok, [f"{r.name}: {r.message}" for r in report.failures]
        assert len(report.results) == 6

    def test_failures_are_collected(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, api_key="dev_master_key_for_testing")
        _write_compose(tmp_path, {"mongodb": ["27017:27017"]})
        d = _make_deployment(tmp_path)
        report = verify_security(tmp_path, d)
        assert not report.ok
        assert {r.name for r in report.failures} == {
            "API key strength",
            "published host ports",
        }


class TestCli:
    def test_no_flag_exits_2(self) -> None:
        result = runner.invoke(app, ["verify"])
        assert result.exit_code == 2
        assert "--security" in result.output

    def test_missing_install_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["verify", "--security", "--install-dir", str(tmp_path / "nope")],
        )
        assert result.exit_code == 2

    def test_missing_state_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["verify", "--security", "--install-dir", str(tmp_path)]
        )
        assert result.exit_code == 2
        assert "deployer-state" in result.output

    def test_healthy_install_exits_0(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path)
        _write_compose(tmp_path, {"caddy": ["8443:8443"]})
        d = _make_deployment(tmp_path)
        _persist_deployment(d, tmp_path, repo_root=None)
        result = runner.invoke(
            app, ["verify", "--security", "--install-dir", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "6 check(s) passed" in result.output

    def test_failing_install_exits_1_with_hints(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, api_key="dev_master_key_for_testing")
        _write_compose(tmp_path, {"caddy": ["8443:8443"]})
        d = _make_deployment(tmp_path)
        _persist_deployment(d, tmp_path, repo_root=None)
        result = runner.invoke(
            app, ["verify", "--security", "--install-dir", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "publicly documented dev default" in result.output

    def test_state_round_trip_preserves_verdict(self, tmp_path: Path) -> None:
        """The CLI path loads the persisted state — the same spec that
        verify_security saw in-memory must be what round-trips."""
        d = _make_deployment(
            tmp_path, hostname="wip.example.com", tls="letsencrypt", variant="prod"
        )
        reloaded = Deployment.model_validate(
            json.loads(json.dumps(d.model_dump(mode="json")))
        )
        assert check_variant_exposure(reloaded).passed
