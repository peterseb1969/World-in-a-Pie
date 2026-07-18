"""Regression coverage for CASE-691 — hardening headers in the rendered
Caddy config for publicly exposed installs.

The decision (which installs get headers) lives in config_gen
(`CaddyConfig.emit_hardening_headers`, keyed on tls=letsencrypt); the
compose/dev renderer serializes it. The k8s edge is nginx-ingress and
does not consume CaddyConfig — its HSTS is controller-provided, and
`verify --security` reports the check as not-applicable there.
"""

from __future__ import annotations

from pathlib import Path

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen import generate_caddy_config
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.spec import Deployment
from wip_deploy.verify_security import check_caddy_security_headers

HARDENING_HEADERS = (
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)


def _deployment(*, tls: str, hostname: str, target: str = "compose") -> Deployment:
    return build_deployment(
        BuildInputs(
            target=target,
            hostname=hostname,
            tls=tls,
            compose_data_dir=Path("/tmp/d"),
        )
    )


def _render(d: Deployment) -> str:
    cfg = generate_caddy_config(d, components=[], apps=[])
    return render_caddyfile(cfg)


class TestConfigGenDecision:
    def test_letsencrypt_arms_headers(self) -> None:
        d = _deployment(tls="letsencrypt", hostname="wip.example.com")
        cfg = generate_caddy_config(d, components=[], apps=[])
        assert cfg.emit_hardening_headers

    def test_internal_does_not(self) -> None:
        d = _deployment(tls="internal", hostname="wip.local")
        cfg = generate_caddy_config(d, components=[], apps=[])
        assert not cfg.emit_hardening_headers


class TestRenderedCaddyfile:
    def test_letsencrypt_renders_all_hardening_headers(self) -> None:
        text = _render(_deployment(tls="letsencrypt", hostname="wip.example.com"))
        for header in HARDENING_HEADERS:
            assert header in text, header
        # Site-block level, not inside a route handle: the header block
        # must appear before the first route block so it applies globally.
        assert text.index("header {") < text.index("handle")

    def test_internal_renders_no_hardening_headers(self) -> None:
        text = _render(_deployment(tls="internal", hostname="wip.local"))
        for header in HARDENING_HEADERS:
            assert header not in text, header


class TestVerifySecurityIntegration:
    def test_fresh_letsencrypt_render_passes_the_check(self, tmp_path: Path) -> None:
        """The loop closes: a Caddyfile rendered by the current deployer
        passes CASE-445's HSTS check with no manual edit."""
        d = _deployment(tls="letsencrypt", hostname="wip.example.com")
        caddy_dir = tmp_path / "config" / "caddy"
        caddy_dir.mkdir(parents=True)
        (caddy_dir / "Caddyfile").write_text(_render(d))
        r = check_caddy_security_headers(tmp_path, d)
        assert r.passed
        assert "Strict-Transport-Security present" in r.message

    def test_k8s_install_is_not_applicable(self, tmp_path: Path) -> None:
        """CASE-445's check misfired on public k8s installs ('rendered
        Caddyfile missing') — the k8s edge is nginx-ingress, no Caddyfile
        exists. Corrected to not-applicable."""
        d = _deployment(tls="letsencrypt", hostname="wip.example.com", target="k8s")
        r = check_caddy_security_headers(tmp_path, d)
        assert r.passed
        assert "nginx-ingress" in r.message
