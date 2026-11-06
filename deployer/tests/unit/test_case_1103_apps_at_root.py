"""Every app served at the root of its own origin: the deployer side
(CASE-1103, which removed path mode; app origins themselves are CASE-1061).

Each deployer app is served at https://<label>.<hostname>[:port]/ with a
session of its own (the gateway's handshake, auth_gateway/app_origins.py).
wip-caddy renders:

- on WIP's origin: no app route, and nothing under /apps/ (no redirect for
  the old paths: they answer 404); the bare host goes to the first app's
  origin;
- one site per deployer app: its routes at their own paths, its `/` route as
  the site's catch-all, the gateway's handshake paths, forward_auth whose
  login is the handshake on WIP's origin, and the app session cookie removed
  before the app;
- with the app edge, a catch-all site for attached apps, handed to the edge,
  with certificates on demand only for names the edge confirms.

On k8s wip-caddy sits behind the Ingress on plain HTTP: WIP's site answers
its hostname only, each app its own host with the forwarded host set to it,
attached apps reach the edge through a catch-all, and the Ingress and the
self-signed certificate carry *.<hostname>.

There is no choice of mode: a saved spec's network.app_origins is dropped on
load, and an install that serves apps needs a hostname app origins work
under.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _load_deployment, _persist_deployment
from wip_deploy.config_gen.caddy import generate_caddy_config
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose_caddy import render_caddyfile

from .renderers.test_compose import REPO_ROOT


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


# ── the Caddyfile ──────────────────────────────────────────────────────────


def test_wips_origin_serves_nothing_under_apps(discovery: Discovery) -> None:
    main = _site(_caddy(_build(), discovery), "wip.example")
    assert "/apps" not in main
    assert "reverse_proxy wip-app-edge" not in main
    # The bare host goes straight to the root of the first app's origin.
    assert "redir * https://rc.wip.example:8443/ permanent" in main


def test_each_deployer_app_is_served_at_its_origins_root(discovery: Discovery) -> None:
    rc = _site(_caddy(_build(), discovery), "rc.wip.example")
    assert "@gateway path /auth/handoff /auth/renew /auth/renewed /auth/logout /auth/userinfo" in rc
    assert "/apps" not in rc
    # rc's only route is `/`: the site's catch-all, protected, cookie removed.
    catchall = rc[rc.index("\n    handle {"):]
    assert "uri /auth/verify?allowed_groups=wip-admins" in catchall
    assert (
        "redir * https://wip.example:8443/auth/app-login?app=rc"
        "&return_to={http.request.uri_escaped} 302"
    ) in catchall
    assert 'request_header Cookie "(^|;\\s*)wip_app_session=[^;]*" ""' in catchall
    assert "reverse_proxy wip-react-console:3011" in catchall
    assert "/auth/login" not in rc  # a login happens on WIP's origin only


def test_an_apps_narrower_routes_come_before_its_catch_all(discovery: Discovery) -> None:
    kb = _site(_caddy(_build(), discovery), "kb.wip.example")
    open_route = kb.index("handle /server-api/kb-client/* {")
    assert open_route < kb.index("\n    handle {")
    block = kb[open_route:kb.index("\n    }\n", open_route)]
    assert "forward_auth" not in block  # declared auth_required: false


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


# ── no mode, no base path ───────────────────────────────────────────────


def test_a_saved_spec_loses_its_mode_on_load(tmp_path: Path) -> None:
    _persist_deployment(_build(), tmp_path, repo_root=REPO_ROOT)
    state = tmp_path / "deployment.deployer-state"
    payload = json.loads(state.read_text())
    payload["wip_deploy_format_version"] = 2
    payload["deployment"]["spec"]["network"]["app_origins"] = "path"
    state.write_text(json.dumps(payload))
    loaded = _load_deployment(tmp_path)
    assert "app_origins" not in loaded.spec.network.model_dump()


def test_an_app_cannot_be_told_a_base_path(tmp_path: Path, discovery: Discovery) -> None:
    from wip_deploy.renderers.compose import render_compose

    from .renderers.test_compose import _secrets

    d = _build()
    tree = render_compose(d, discovery.components, discovery.apps, _secrets(tmp_path, d, discovery))
    compose = tree.files[Path("docker-compose.yaml")].content
    assert "APP_BASE_PATH" not in compose and "VITE_BASE_PATH" not in compose


@pytest.mark.parametrize("bad", [
    {"routes": [{"path": "/apps/x"}]},
    {"healthcheck": {"endpoint": "/apps/x/health"}},
    {"env": {"required": [{"name": "APP_BASE_PATH", "source": {"literal": "/apps/x"}}]}},
    {"env": {"required": [{"name": "VITE_BASE_PATH", "source": {"literal": "/"}}]}},
    {"routes": [{"path": "/auth"}]},
    {"routes": [{"path": "/auth/renew"}]},
])
def test_an_app_manifest_in_the_old_form_is_refused(bad: dict) -> None:
    from pydantic import ValidationError

    from wip_deploy.spec.app import App

    spec = {"image": {"name": "x"}, "ports": [{"name": "http", "container_port": 1}],
            "routes": [{"path": "/"}], **bad}
    with pytest.raises(ValidationError):
        App.model_validate({
            "metadata": {"name": "x", "category": "optional", "description": "x"},
            "spec": spec,
            "app_metadata": {"display_name": "X", "label": "x"},
        })


def test_k8s_wip_caddy_serves_each_origin_on_plain_http(discovery: Discovery) -> None:
    caddyfile = _caddy(_build(target="k8s"), discovery)
    assert "on_demand" not in caddyfile  # the Ingress terminates TLS
    assert "\nhttp://wip.example:8080 {" in caddyfile
    rc = caddyfile[caddyfile.index("\nhttp://rc.wip.example:8080 {"):]
    rc = rc[: rc.index("\n}\n")]
    # The gateway reads the app from the forwarded host: this site's own.
    assert "header_up X-Forwarded-Host rc.wip.example" in rc
    assert "header_up X-Forwarded-Host wip.example\n" not in rc
    # https on 443: no port in the handshake URL.
    assert "redir * https://wip.example/auth/app-login?app=rc" in rc
    assert "tls {" not in rc
    attached = caddyfile[caddyfile.index("\n:8080 {"):]
    assert "reverse_proxy wip-app-edge." in attached
    # An attached app's handshake reaches the gateway, naming its origin
    # (seen live on wip-k3s: without this the handoff reached the edge, 404).
    assert "@gateway path /auth/handoff /auth/renew" in attached
    assert "header_up X-Forwarded-Host {host}" in attached


def test_k8s_without_the_edge_answers_unknown_app_hosts_404(discovery: Discovery) -> None:
    d = build_deployment(BuildInputs(
        target="k8s", hostname="wip.example", preset="standard", apps=["react-console"],
        remove=["app-edge"], k8s_namespace="wip", registry="ghcr.io/x",
    ))
    caddyfile = _caddy(d, discovery)
    assert caddyfile.rstrip().endswith(":8080 {\n    respond 404\n}")


def test_the_ingress_and_its_certificate_carry_the_app_origins() -> None:
    import yaml

    from wip_deploy.config_gen.ingress import generate_ingress_config
    from wip_deploy.renderers.k8s import _render_ingress

    ingress = yaml.safe_load(_render_ingress(generate_ingress_config(_build(target="k8s")), "wip"))
    assert ingress["spec"]["tls"][0]["hosts"] == ["wip.example", "*.wip.example"]
    assert [r["host"] for r in ingress["spec"]["rules"]] == ["wip.example", "*.wip.example"]


def test_the_self_signed_certificate_names_the_app_origins() -> None:
    from unittest.mock import MagicMock

    from wip_deploy.apply import _ensure_self_signed_tls_secret

    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/openssl"),
        patch("wip_deploy.apply.subprocess.run") as run,
    ):
        run.side_effect = [
            MagicMock(returncode=0, stderr=""),  # create namespace
            MagicMock(returncode=1, stderr="NotFound"),  # no secret yet
            MagicMock(returncode=0, stderr=""),  # openssl req
            MagicMock(returncode=0, stderr=""),  # create secret
        ]
        _ensure_self_signed_tls_secret(
            ns="wip", secret_name="wip-tls", hostname="wip.example",
            extra_names=("*.wip.example",),
        )
    openssl = next(c[0][0] for c in run.call_args_list if c[0][0][0] == "openssl")
    assert "subjectAltName=DNS:wip.example,DNS:*.wip.example" in openssl


def test_an_existing_certificate_without_the_app_origins_is_refused() -> None:
    import base64
    from unittest.mock import MagicMock

    from wip_deploy.apply import ApplyError, _ensure_self_signed_tls_secret

    cert = base64.b64encode(b"pem").decode()
    san = lambda text: MagicMock(returncode=0, stdout=f"X509v3 Subject Alternative Name:\n    {text}\n", stderr="")  # noqa: E731
    with (
        patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/openssl"),
        patch("wip_deploy.apply.subprocess.run") as run,
    ):
        run.side_effect = [
            MagicMock(returncode=0, stderr=""), MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=0, stdout=cert, stderr=""), san("DNS:wip.example"),
            MagicMock(returncode=0, stdout=cert, stderr=""), san("DNS:wip.example"),
        ]
        with pytest.raises(ApplyError, match="does not cover hostname 'app.wip.example'"):
            _ensure_self_signed_tls_secret(
                ns="wip", secret_name="wip-tls", hostname="wip.example",
                extra_names=("*.wip.example",),
            )


# ── hostnames that cannot carry app origins (CASE-1105) ─────────────────
#
# An app's session is renewed in a frame on its origin that calls WIP's
# origin; the browser sends WIP's SameSite=Lax cookie only when both are the
# same site. A dotless hostname or an IP address is its own site, apart from
# <app>.<hostname>: every renewal then read as a logout.


@pytest.mark.parametrize("host", ["localhost", "wip-pi-internal", "192.168.1.20", "::1", "[::1]"])
def test_a_hostname_without_a_registrable_part_cannot_carry_app_origins(host: str) -> None:
    from wip_deploy.spec.deployment import app_origins_problem

    assert app_origins_problem(host) is not None


@pytest.mark.parametrize("host", ["wip.localhost", "wip-pi.internal", "wip.example."])
def test_a_dotted_hostname_can(host: str) -> None:
    from wip_deploy.spec.deployment import app_origins_problem

    assert app_origins_problem(host) is None


def test_an_install_with_apps_on_a_dotless_hostname_is_refused() -> None:
    with pytest.raises(ValueError, match="has no dot"):
        build_deployment(BuildInputs(target="dev", hostname="localhost", apps=["react-console"]))
    with pytest.raises(ValueError, match="has no dot"):
        build_deployment(BuildInputs(target="dev", hostname="localhost", add=["app-edge"]))


def test_one_without_apps_is_not() -> None:
    build_deployment(BuildInputs(target="dev", hostname="localhost", preset="core"))


def test_dev_defaults_to_a_hostname_apps_work_under() -> None:
    from wip_deploy.cli import _resolve_hostname

    assert _resolve_hostname(None, "dev") == "wip.localhost"
