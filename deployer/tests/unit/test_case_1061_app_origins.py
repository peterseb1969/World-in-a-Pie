"""Apps on origins of their own: the deployer side (CASE-1061).

network.app_origins="subdomain" serves each app at https://<label>.<hostname>
with a session of its own (the gateway's handshake, auth_gateway/
app_origins.py). wip-caddy then renders:

- on WIP's origin: no app route; /apps/<label>/... answers 301 to the app's
  origin, deployer and attached apps alike;
- one site per deployer app: its routes (still under /apps/<label>), the
  gateway's handshake paths, forward_auth whose login is the handshake on
  WIP's origin, and the app session cookie removed before the app;
- with the app edge, a catch-all site for attached apps, handed to the edge,
  with certificates on demand only for names the edge confirms.

New compose and dev installs default to subdomain; k8s stays on path (its
Ingress does not carry app origins yet), and an existing install keeps its
own mode unless asked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _keep_app_origins, _persist_deployment, app
from wip_deploy.config_gen.caddy import generate_caddy_config
from wip_deploy.config_gen.spec_context import make_spec_context, resolve_from_spec
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose_caddy import render_caddyfile

from .renderers.test_compose import REPO_ROOT, _minimal_compose

runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _build(target: str = "compose", **kw):  # type: ignore[no-untyped-def]
    extra = {"k8s_namespace": "wip"} if target == "k8s" else {"compose_data_dir": Path("/tmp/d")}
    return build_deployment(BuildInputs(
        target=target, hostname="wip.example", preset="standard",
        apps=["react-console", "wip-kb"], add=["app-edge"],
        registry="ghcr.io/x", **extra, **kw,
    ))


def _caddy(d, discovery: Discovery) -> str:  # type: ignore[no-untyped-def]
    return render_caddyfile(generate_caddy_config(d, discovery.components, discovery.apps))


def _site(caddyfile: str, host: str) -> str:
    start = caddyfile.index(f"\n{host}:")
    end = caddyfile.find("\n}\n", start)
    return caddyfile[start:end]


# ── defaults and refusals ─────────────────────────────────────────────────


def test_new_compose_installs_serve_apps_on_their_own_origins() -> None:
    assert _build().spec.network.app_origins == "subdomain"
    assert _build(target="dev").spec.network.app_origins == "subdomain"


def test_k8s_stays_on_path_and_refuses_subdomain() -> None:
    assert _build(target="k8s").spec.network.app_origins == "path"
    with pytest.raises(ValueError, match="not rendered for target='k8s'"):
        _build(target="k8s", app_origins="subdomain")


def test_an_explicit_choice_wins() -> None:
    assert _build(app_origins="path").spec.network.app_origins == "path"


def test_an_install_saved_before_the_field_reads_as_path() -> None:
    assert _minimal_compose().spec.network.app_origins == "path"


def test_an_existing_install_keeps_its_mode_unless_asked() -> None:
    previous = _build(app_origins="path")
    d = _build()
    _keep_app_origins(d, previous, None)
    assert d.spec.network.app_origins == "path"
    d = _build()
    _keep_app_origins(d, previous, "subdomain")
    assert d.spec.network.app_origins == "subdomain"
    d = _build()
    _keep_app_origins(d, None, None)  # a new install
    assert d.spec.network.app_origins == "subdomain"


# ── the Caddyfile ──────────────────────────────────────────────────────────


def test_wips_origin_keeps_no_app_route_and_sends_apps_away(discovery: Discovery) -> None:
    caddyfile = _caddy(_build(), discovery)
    main = _site(caddyfile, "wip.example")  # WIP's own site
    assert "handle /apps/" not in main and "handle_path /apps/" not in main
    assert "reverse_proxy wip-app-edge" not in main  # the edge left WIP's origin
    assert "@app_path path_regexp app_path ^/apps/(" in main
    assert "redir * https://{re.app_path.1}.wip.example:8443{uri} permanent" in main
    # The bare host goes straight to the first app's origin.
    assert "redir * https://rc.wip.example:8443/apps/rc/ permanent" in main


def test_each_deployer_app_gets_its_own_site(discovery: Discovery) -> None:
    rc = _site(_caddy(_build(), discovery), "rc.wip.example")
    assert "@gateway path /auth/handoff /auth/renew /auth/renewed /auth/logout /auth/userinfo" in rc
    assert "redir * /apps/rc/ permanent" in rc
    assert "handle /apps/rc/* {" in rc
    assert "uri /auth/verify?allowed_groups=wip-admins" in rc
    assert (
        "redir * https://wip.example:8443/auth/app-login?app=rc"
        "&return_to={http.request.uri_escaped} 302"
    ) in rc
    assert 'request_header Cookie "(^|;\\s*)wip_app_session=[^;]*" ""' in rc
    assert "/auth/login" not in rc  # a login happens on WIP's origin only


def test_attached_apps_get_the_wildcard_site_and_on_demand_certificates(discovery: Discovery) -> None:
    caddyfile = _caddy(_build(), discovery)
    assert "on_demand_tls {\n        ask http://wip-app-edge:8080/edge/ask\n    }" in caddyfile
    attached = caddyfile[caddyfile.index("\nhttps://:8443 {"):]
    assert "on_demand" in attached
    assert "reverse_proxy wip-app-edge:8080" in attached
    # No wildcard name: Caddy would then skip the deployer apps' own certificates.
    assert "*.wip.example" not in caddyfile


def test_without_the_edge_there_is_no_wildcard_site(discovery: Discovery) -> None:
    d = build_deployment(BuildInputs(
        target="compose", hostname="wip.example", preset="standard", apps=["react-console"],
        remove=["app-edge"], compose_data_dir=Path("/tmp/d"), registry="ghcr.io/x",
    ))
    caddyfile = _caddy(d, discovery)
    assert "https://:8443" not in caddyfile and "on_demand_tls" not in caddyfile
    assert "\nrc.wip.example:8443 {" in caddyfile


def test_path_mode_is_unchanged(discovery: Discovery) -> None:
    caddyfile = _caddy(_build(app_origins="path"), discovery)
    assert "handle /apps/rc/* {" in caddyfile
    assert "rc.wip.example" not in caddyfile and "app_path" not in caddyfile
    assert "/auth/app-login" not in caddyfile


# ── what the gateway and the edge are told ───────────────────────────────


def test_the_gateway_and_the_edge_learn_the_mode_and_the_public_url(discovery: Discovery) -> None:
    d = _build()
    ctx = make_spec_context(d, discovery.components, discovery.apps)
    assert resolve_from_spec("network.app_origins", ctx) == "subdomain"
    assert resolve_from_spec("network.public_base_url", ctx) == "https://wip.example:8443"
    names = {c.metadata.name: c for c in discovery.components}
    for component in ("auth-gateway", "app-edge"):
        env = {e.name for e in names[component].spec.env.required}
        assert "WIP_APP_ORIGINS" in env, component
    assert "WIP_PUBLIC_URL" in {e.name for e in names["auth-gateway"].spec.env.required}


# ── redeploy --app-origins ───────────────────────────────────────────────


def test_redeploy_moves_an_install_to_app_origins(tmp_path: Path) -> None:
    d = _build(app_origins="path")
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    with patch("wip_deploy.cli._apply_and_persist_mutation") as apply:
        result = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path), "--app-origins", "subdomain"])
    assert result.exit_code == 0, result.output
    assert apply.call_args.args[0].spec.network.app_origins == "subdomain"


def test_redeploy_refuses_subdomain_on_k8s_and_a_bad_value(tmp_path: Path) -> None:
    k8s = _build(target="k8s")
    _persist_deployment(k8s, tmp_path / "k8s", repo_root=REPO_ROOT)
    r = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path / "k8s"), "--app-origins", "subdomain"])
    assert r.exit_code == 2 and "not rendered for k8s" in r.output
    compose = _build()
    _persist_deployment(compose, tmp_path / "c", repo_root=REPO_ROOT)
    r = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path / "c"), "--app-origins", "both"])
    assert r.exit_code == 2
