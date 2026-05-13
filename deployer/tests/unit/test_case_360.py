"""Regression coverage for CASE-360 — export Caddy internal CA.

Two surfaces:

  1. `export_ca.export_caddy_internal_ca()` — pure-function wrapper
     around `podman exec wip-caddy cat /data/caddy/.../root.crt`.
     Tested with `subprocess.run` mocked, since CI doesn't have a
     real wip-caddy container.

  2. `wip-deploy export-ca` CLI verb — argument parsing, friendly
     error paths per the (target, tls) matrix.

The compose `--tls internal` happy path is the primary case body
scenario. Other tls modes (letsencrypt, external) and the k8s target
each get a friendly error.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wip_deploy.cli import app
from wip_deploy.export_ca import (
    ExportCAError,
    export_caddy_internal_ca,
)

runner = CliRunner()


# A short but plausible PEM cert payload. Real Caddy CA certs are
# ~1.5 KB; the format only matters as bytes flowing through.
_FAKE_PEM = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"MIIBkTCCATigAwIBAgIQ...fake-test-cert...\n"
    b"-----END CERTIFICATE-----\n"
)


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Sandbox `Path.home()` so the CLI never touches the real
    ~/.wip-deploy/. Same pattern as test_case_356.py — without it,
    real-machine state bleeds in. (CASE-366 names this discipline.)"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def _seed_install(
    home: Path,
    name: str = "default",
    *,
    target: str = "compose",
    tls: str = "internal",
) -> Path:
    """Create a minimal ~/.wip-deploy/<name>/ with a persisted spec.

    The spec contents only need to satisfy `_load_deployment` + the
    target/tls dispatch in `export-ca`. We piggy-back on the same
    JSON envelope `_persist_deployment` writes so the CLI's loader
    actually reads it as a Deployment.
    """
    from wip_deploy.cli import _persist_deployment
    from wip_deploy.spec import (
        AuthSpec,
        ComposePlatform,
        Deployment,
        DeploymentMetadata,
        DeploymentSpec,
        ImagesSpec,
        K8sPlatform,
        NetworkSpec,
        PlatformSpec,
        SecretsSpec,
    )

    install_dir = home / ".wip-deploy" / name
    install_dir.mkdir(parents=True, exist_ok=True)

    if target == "k8s":
        platform = PlatformSpec(k8s=K8sPlatform())
        secrets = SecretsSpec(backend="k8s-secret")
        net_https = 443
    else:
        platform = PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d")))
        secrets = SecretsSpec(backend="file", location="/tmp/s")
        net_https = 8443

    # auth-gateway requires oidc/hybrid → use api-key-only with gateway off
    # so the spec validates cleanly without dragging Dex config in.
    deployment = Deployment(
        metadata=DeploymentMetadata(name=name),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(
                hostname="localhost", tls=tls, https_port=net_https,  # type: ignore[arg-type]
            ),
            images=ImagesSpec(),
            platform=platform,
            secrets=secrets,
        ),
    )
    _persist_deployment(deployment, install_dir)
    return install_dir


# ────────────────────────────────────────────────────────────────────
# Pure-function layer
# ────────────────────────────────────────────────────────────────────


class TestExportCaddyInternalCA:
    def _ps_mock(self, *, running: bool) -> MagicMock:
        """Mock the `podman ps` result for the container-running check."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = "wip-caddy\n" if running else ""
        result.stderr = ""
        return result

    def _exec_mock(self, *, success: bool, payload: bytes = _FAKE_PEM) -> MagicMock:
        """Mock the `podman exec cat` result."""
        result = MagicMock()
        result.returncode = 0 if success else 1
        result.stdout = payload if success else b""
        result.stderr = b"" if success else b"cat: No such file or directory"
        return result

    def test_happy_path_returns_pem_bytes(self) -> None:
        with patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"), \
             patch("wip_deploy.export_ca.subprocess.run") as run:
            run.side_effect = [
                self._ps_mock(running=True),
                self._exec_mock(success=True),
            ]
            assert export_caddy_internal_ca() == _FAKE_PEM

    def test_raises_when_podman_missing(self) -> None:
        with (
            patch("wip_deploy.export_ca.shutil.which", return_value=None),
            pytest.raises(ExportCAError, match="podman is not available"),
        ):
            export_caddy_internal_ca()

    def test_raises_when_container_not_running(self) -> None:
        with patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"), \
             patch("wip_deploy.export_ca.subprocess.run") as run:
            run.return_value = self._ps_mock(running=False)
            with pytest.raises(ExportCAError, match="is not running"):
                export_caddy_internal_ca()

    def test_raises_when_ca_not_yet_generated(self) -> None:
        """`podman exec cat` returns non-zero when the file doesn't
        exist (Caddy hasn't received any HTTPS request yet)."""
        with patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"), \
             patch("wip_deploy.export_ca.subprocess.run") as run:
            run.side_effect = [
                self._ps_mock(running=True),
                self._exec_mock(success=False),
            ]
            with pytest.raises(ExportCAError, match="Caddy generates the internal CA lazily"):
                export_caddy_internal_ca()

    def test_raises_when_payload_is_empty(self) -> None:
        """podman exec succeeded but the file was empty — unusual but
        worth a distinct error so the operator doesn't write a 0-byte
        cert and waste time debugging trust."""
        with patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"), \
             patch("wip_deploy.export_ca.subprocess.run") as run:
            run.side_effect = [
                self._ps_mock(running=True),
                self._exec_mock(success=True, payload=b""),
            ]
            with pytest.raises(ExportCAError, match="empty"):
                export_caddy_internal_ca()

    def test_custom_container_name(self) -> None:
        """Allow the container name to be overridden — useful if the
        user customized the compose project name."""
        with patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"), \
             patch("wip_deploy.export_ca.subprocess.run") as run:
            run.side_effect = [
                MagicMock(returncode=0, stdout="my-caddy\n", stderr=""),
                self._exec_mock(success=True),
            ]
            export_caddy_internal_ca(container_name="my-caddy")
            # First call → ps filter mentions the custom name
            ps_args = run.call_args_list[0][0][0]
            assert "name=^my-caddy$" in ps_args
            # Second call → exec targets the custom name
            exec_args = run.call_args_list[1][0][0]
            assert "my-caddy" in exec_args


# ────────────────────────────────────────────────────────────────────
# CLI wiring
# ────────────────────────────────────────────────────────────────────


class TestExportCaCLI:
    def test_help_works(self) -> None:
        r = runner.invoke(app, ["export-ca", "--help"])
        assert r.exit_code == 0, r.output
        assert "Export the Caddy-managed internal CA" in r.output

    def test_missing_install_dir_exits_2(self, isolated_home: Path) -> None:
        """No ~/.wip-deploy/<name>/ → exit 2 with actionable message."""
        r = runner.invoke(app, ["export-ca", "--name", "nonexistent"])
        assert r.exit_code == 2, r.output
        assert "install dir does not exist" in r.output

    def test_k8s_install_exits_2_with_kubectl_hint(
        self, isolated_home: Path
    ) -> None:
        """K8s installs use an operator-provided TLS Secret — point the
        operator at kubectl instead of returning a wrong answer."""
        _seed_install(isolated_home, name="k8s-inst", target="k8s")
        r = runner.invoke(app, ["export-ca", "--name", "k8s-inst"])
        assert r.exit_code == 2, r.output
        assert "not supported for k8s installs" in r.output
        assert "kubectl" in r.output

    def test_letsencrypt_install_exits_2_with_explanation(
        self, isolated_home: Path
    ) -> None:
        """letsencrypt installs use a real CA — no export needed.
        Friendly message tells the operator nothing's wrong."""
        # letsencrypt requires a public hostname; the seeded install
        # uses localhost which the validator refuses. Seed with a
        # public hostname instead.
        from wip_deploy.cli import _persist_deployment
        from wip_deploy.spec import (
            AuthSpec,
            ComposePlatform,
            Deployment,
            DeploymentMetadata,
            DeploymentSpec,
            ImagesSpec,
            NetworkSpec,
            PlatformSpec,
            SecretsSpec,
        )

        install_dir = isolated_home / ".wip-deploy" / "le-inst"
        install_dir.mkdir(parents=True, exist_ok=True)
        d = Deployment(
            metadata=DeploymentMetadata(name="le-inst"),
            spec=DeploymentSpec(
                target="compose",
                auth=AuthSpec(mode="api-key-only", gateway=False),
                network=NetworkSpec(hostname="wip.example.com", tls="letsencrypt"),
                images=ImagesSpec(),
                platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
                secrets=SecretsSpec(backend="file", location="/tmp/s"),
            ),
        )
        _persist_deployment(d, install_dir)

        r = runner.invoke(app, ["export-ca", "--name", "le-inst"])
        assert r.exit_code == 2, r.output
        assert "only applies to --tls internal" in r.output
        assert "letsencrypt" in r.output

    def test_external_install_exits_2_with_explanation(
        self, isolated_home: Path
    ) -> None:
        _seed_install(isolated_home, name="ext-inst", tls="external")
        r = runner.invoke(app, ["export-ca", "--name", "ext-inst"])
        assert r.exit_code == 2, r.output
        assert "only applies to --tls internal" in r.output
        assert "external" in r.output

    def test_happy_path_writes_to_out(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """compose + tls=internal + container running + CA available
        → exit 0, file written, trust instructions printed."""
        _seed_install(isolated_home, name="ok-inst")
        out_path = tmp_path / "ca.crt"

        with patch(
            "wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"
        ), patch("wip_deploy.export_ca.subprocess.run") as run:
            run.side_effect = [
                MagicMock(returncode=0, stdout="wip-caddy\n", stderr=""),
                MagicMock(returncode=0, stdout=_FAKE_PEM, stderr=b""),
            ]
            r = runner.invoke(
                app, ["export-ca", "--name", "ok-inst", "--out", str(out_path)],
            )
        assert r.exit_code == 0, r.output
        assert out_path.read_bytes() == _FAKE_PEM
        assert "Wrote internal CA" in r.output
        assert "NODE_EXTRA_CA_CERTS" in r.output

    def test_happy_path_stdout(self, isolated_home: Path) -> None:
        """No --out → PEM goes to stdout. Useful for `... | openssl x509`."""
        _seed_install(isolated_home, name="stdout-inst")

        with patch(
            "wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"
        ), patch("wip_deploy.export_ca.subprocess.run") as run:
            run.side_effect = [
                MagicMock(returncode=0, stdout="wip-caddy\n", stderr=""),
                MagicMock(returncode=0, stdout=_FAKE_PEM, stderr=b""),
            ]
            # mix_stderr=False would help here, but the CliRunner default
            # captures stdout+stderr together. Look for the PEM marker.
            r = runner.invoke(app, ["export-ca", "--name", "stdout-inst"])
        assert r.exit_code == 0, r.output
        assert "BEGIN CERTIFICATE" in r.output

    def test_container_not_running_exits_1(self, isolated_home: Path) -> None:
        """Spec valid + container down → exit 1 with the run-it-first hint."""
        _seed_install(isolated_home, name="down-inst")

        with patch(
            "wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"
        ), patch("wip_deploy.export_ca.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            r = runner.invoke(app, ["export-ca", "--name", "down-inst"])
        assert r.exit_code == 1, r.output
        assert "not running" in r.output
