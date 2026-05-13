"""Regression coverage for CASE-358 — cross-host external base URL.

Three surfaces:

  1. **Spec** — `NetworkSpec.remote_wip_url` field + validator.
     Refuses malformed URLs (no scheme, missing host, trailing
     paths/queries).

  2. **SpecContext** — `SpecContextNetwork.external_base_url` derives
     from `network.remote_wip_url` when set, otherwise from this
     install's own `_public_base`. Available to manifests via
     `from_spec: network.external_base_url`.

  3. **CLI** — `--remote-wip URL` plumbs to the spec, fires a
     yellow warning that the local backend stack still comes up
     (CASE-359 dependency), and produces a deployment whose
     SpecContext returns the remote URL.

Design refinement vs. case body: the case proposed `--remote-wip`
override `network.hostname/https_port`. That would break the Mac's
local Caddy binding + OIDC issuer URLs. This implementation introduces
a separate `network.remote_wip_url` field that ONLY affects
`external_base_url` — the local install's own services stay correct
on the local hostname.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import app
from wip_deploy.config_gen import make_spec_context
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

runner = CliRunner()


# ────────────────────────────────────────────────────────────────────
# Spec — NetworkSpec.remote_wip_url validation
# ────────────────────────────────────────────────────────────────────


class TestRemoteWipUrlValidator:
    def test_defaults_to_none(self) -> None:
        net = NetworkSpec(hostname="localhost")
        assert net.remote_wip_url is None

    def test_accepts_https_with_port(self) -> None:
        net = NetworkSpec(
            hostname="localhost", remote_wip_url="https://wip-pi.local:8443"
        )
        assert net.remote_wip_url == "https://wip-pi.local:8443"

    def test_accepts_https_default_port(self) -> None:
        net = NetworkSpec(
            hostname="localhost", remote_wip_url="https://wip.example.com"
        )
        assert net.remote_wip_url == "https://wip.example.com"

    def test_accepts_http(self) -> None:
        """http for LAN-only deployments where the operator doesn't
        want TLS. Unusual but not invalid."""
        net = NetworkSpec(
            hostname="localhost", remote_wip_url="http://wip.lan:8080"
        )
        assert net.remote_wip_url == "http://wip.lan:8080"

    @pytest.mark.parametrize(
        ("url", "expected_error"),
        [
            ("wip-pi.local:8443", "scheme"),  # no scheme
            ("ftp://wip.lan", "scheme"),  # wrong scheme
            ("https://", "hostname"),  # no host
            ("https://wip.lan/api", "path"),  # trailing path
            ("https://wip.lan/", "path"),  # bare trailing slash
            ("https://wip.lan?foo=bar", "query or fragment"),  # query
            ("https://wip.lan#frag", "query or fragment"),  # fragment
        ],
    )
    def test_rejects_malformed(self, url: str, expected_error: str) -> None:
        with pytest.raises(ValueError, match=expected_error):
            NetworkSpec(hostname="localhost", remote_wip_url=url)


# ────────────────────────────────────────────────────────────────────
# SpecContext — external_base_url derivation
# ────────────────────────────────────────────────────────────────────


def _compose_deployment(
    *, hostname: str = "wip.local", remote_wip_url: str | None = None
) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(
                hostname=hostname,
                tls="internal",
                remote_wip_url=remote_wip_url,
            ),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


class TestSpecContextExternalBaseUrl:
    def test_default_uses_install_own_public_base(self) -> None:
        """No remote_wip_url → external_base_url = this install's URL.
        Useful even for same-host apps that want to emit absolute URLs."""
        d = _compose_deployment(hostname="wip-pi.local")
        ctx = make_spec_context(d, [])
        # compose default https_port=8443 → URL keeps the port.
        assert ctx.network.external_base_url == "https://wip-pi.local:8443"

    def test_remote_wip_url_overrides(self) -> None:
        """With remote_wip_url set, external_base_url returns it verbatim.
        Even when the install's own hostname differs."""
        d = _compose_deployment(
            hostname="localhost", remote_wip_url="https://wip-pi.local:8443"
        )
        ctx = make_spec_context(d, [])
        assert ctx.network.external_base_url == "https://wip-pi.local:8443"

    def test_remote_wip_url_does_not_affect_other_fields(self) -> None:
        """Critical: setting remote_wip_url must NOT change the install's
        own hostname-derived values. OIDC issuer URLs, cors_origins
        (localhost variant), and Caddy binding all use the LOCAL
        hostname. Otherwise the Mac's Caddy would try to bind to
        wip-pi.local and OIDC redirects would point at the wrong place."""
        d = _compose_deployment(
            hostname="localhost", remote_wip_url="https://wip-pi.local:8443"
        )
        ctx = make_spec_context(d, [])
        assert ctx.network.hostname == "localhost"
        # cors_origins includes the localhost variant when hostname is
        # localhost — unchanged by remote_wip_url.
        assert "localhost" in ctx.network.cors_origins
        assert "wip-pi.local" not in ctx.network.cors_origins
        # OIDC issuer URL is derived from network.hostname, not remote_wip_url.
        assert "localhost" in ctx.auth.issuer_url_public
        assert "wip-pi.local" not in ctx.auth.issuer_url_public

    def test_resolve_from_spec_path(self) -> None:
        """Apps reference the value via `from_spec: network.external_base_url`.
        Verify the dotted path actually resolves."""
        from wip_deploy.config_gen import resolve_from_spec

        d = _compose_deployment(
            hostname="wip-pi.local", remote_wip_url="https://remote:9000"
        )
        ctx = make_spec_context(d, [])
        assert resolve_from_spec("network.external_base_url", ctx) == (
            "https://remote:9000"
        )


# ────────────────────────────────────────────────────────────────────
# build_deployment — BuildInputs.remote_wip_url plumbs to NetworkSpec
# ────────────────────────────────────────────────────────────────────


class TestBuildInputsPlumbing:
    def test_remote_wip_url_lands_on_network_spec(self) -> None:
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            tls="internal",
            remote_wip_url="https://wip-pi.local:8443",
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        assert d.spec.network.remote_wip_url == "https://wip-pi.local:8443"

    def test_default_is_none(self) -> None:
        inputs = BuildInputs(
            name="t",
            preset="standard",
            target="dev",
            hostname="localhost",
            tls="internal",
            dev_mode="simple",
        )
        d = build_deployment(inputs)
        assert d.spec.network.remote_wip_url is None


# ────────────────────────────────────────────────────────────────────
# CLI — --remote-wip flag end-to-end
# ────────────────────────────────────────────────────────────────────


class TestRemoteWipCLI:
    REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()

    def test_flag_lands_on_rendered_spec(self) -> None:
        """show-spec --format json should carry remote_wip_url through
        the entire pipeline."""
        import json

        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--remote-wip", "https://wip-pi.local:8443",
                "--format", "json",
                "--repo-root", str(self.REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        # CASE-366 mitigation: the yellow warning goes to stderr now;
        # stdout is clean JSON. mix_stderr=False makes the runner respect
        # that separation.
        parsed = json.loads(r.stdout)
        assert parsed["spec"]["network"]["remote_wip_url"] == (
            "https://wip-pi.local:8443"
        )

    def test_warning_fires_on_stderr(self) -> None:
        """The CASE-359 caveat warning should land on stderr (CASE-366
        discipline)."""
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--remote-wip", "https://wip-pi.local:8443",
                "--format", "yaml",
                "--repo-root", str(self.REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "CASE-359" in r.stderr
        assert "https://wip-pi.local:8443" in r.stderr

    def test_no_flag_no_warning(self) -> None:
        """Standard invocation without --remote-wip → no cross-host
        warning. Pre-existing same-host installs stay quiet."""
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--format", "yaml",
                "--repo-root", str(self.REPO_ROOT),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "CASE-359" not in r.stderr
        assert "remote-wip" not in r.stderr.lower()

    def test_malformed_url_exits_with_actionable_error(self) -> None:
        """Pydantic validation should surface a clean error, not a stack."""
        r = runner.invoke(
            app,
            [
                "show-spec",
                "--preset", "standard",
                "--target", "dev",
                "--hostname", "localhost",
                "--remote-wip", "wip-pi.local:8443",  # missing scheme
                "--format", "yaml",
                "--repo-root", str(self.REPO_ROOT),
            ],
        )
        assert r.exit_code == 2, r.output
        assert "scheme" in r.output
