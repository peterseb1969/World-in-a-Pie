"""CASE-1192 — the umbrella review's low findings, deployer side.

- Files the services re-read while running, through a directory mount (the
  API key file, the federated-groups policy, the reservations, the attach
  policy), are replaced in one step: a reader never sees them empty or half
  written. Files mounted on their own (the Caddyfile, Dex's config) keep
  their inode, or the container would never see a later render.
- On a prefix-stripping route, the login's return_to keeps the prefix:
  Caddy reads {http.request.uri_escaped} after handle_path stripped it.
- `/` with no app enabled answers a page, not a redirect to the login
  (which looped); with an app, the redirect is a never-cached 302.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest

from wip_deploy.config_gen.routing import ResolvedRoute
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.base import FileTree
from wip_deploy.renderers.compose import render_compose
from wip_deploy.renderers.compose_caddy import CaddyConfig, _write_forward_auth, _write_route
from wip_deploy.spec.validators import validate_all

from .renderers.test_compose import REPO_ROOT, _minimal_compose, _secrets


@pytest.fixture(scope="module")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)

LIVE_REREAD = (
    "config/keys/api-keys.json",
    "config/auth/federated-groups.json",
    "config/auth/namespace-reservations.json",
    "config/auth/attach-policy.json",
)


def test_an_atomic_entry_is_replaced_and_a_plain_one_is_rewritten_in_place(tmp_path: Path) -> None:
    tree = FileTree()
    tree.add("config/auth/policy.json", "one", atomic=True)
    tree.add("config/caddy/Caddyfile", "one")
    tree.write(tmp_path)
    atomic, plain = tmp_path / "config/auth/policy.json", tmp_path / "config/caddy/Caddyfile"
    before = (atomic.stat().st_ino, plain.stat().st_ino)

    tree.add("config/auth/policy.json", "two", atomic=True)
    tree.add("config/caddy/Caddyfile", "two")
    tree.write(tmp_path)

    assert atomic.read_text() == "two" and plain.read_text() == "two"
    assert atomic.stat().st_ino != before[0]  # a new file renamed into place
    assert plain.stat().st_ino == before[1]  # a single-file bind keeps seeing it
    assert oct(atomic.stat().st_mode & 0o777) == oct(0o644)
    assert sorted(p.name for p in atomic.parent.iterdir()) == ["policy.json"]  # no temp left


def test_compose_writes_the_live_reread_files_atomically_and_nothing_else(
    tmp_path: Path, real_discovery: Discovery,
) -> None:
    d = _minimal_compose()
    tree = render_compose(d, real_discovery.components, real_discovery.apps, _secrets(tmp_path, d, real_discovery))
    atomic = {str(p) for p, e in tree.files.items() if e.atomic}
    assert atomic == set(LIVE_REREAD), atomic
    assert not tree.files[Path("config/caddy/Caddyfile")].atomic


def _route(**kw) -> ResolvedRoute:
    base = {
        "path": "/files", "backend_component": "store", "backend_port": 9000, "auth_protected": True,
        "streaming": False, "strip_prefix": True, "redirect_bare_path": False,
    }
    base.update(kw)
    return ResolvedRoute(**base)  # type: ignore[arg-type]


def _cfg() -> CaddyConfig:
    return CaddyConfig(
        hostname="wip.local", https_port=8443, tls_mode="internal",
        gateway_service="wip-auth-gateway", gateway_port=4180, routes=[], root_redirect=None,
    )


def test_a_stripped_routes_login_returns_to_the_full_path() -> None:
    out = StringIO()
    _write_route(out, _route(), _cfg())
    assert "handle_path /files/*" in out.getvalue()
    assert "return_to=%2Ffiles{http.request.uri_escaped} 302" in out.getvalue()


def test_a_kept_path_returns_to_the_uri_as_is() -> None:
    out = StringIO()
    _write_route(out, _route(strip_prefix=False), _cfg())
    assert "return_to={http.request.uri_escaped} 302" in out.getvalue()


def test_a_preserved_subpath_of_a_stripped_route_is_not_prefixed_twice() -> None:
    out = StringIO()
    _write_route(out, _route(preserve_prefix_subpaths=("/files/admin",)), _cfg())
    text = out.getvalue()
    kept = text[text.index("handle /files/admin/*"):text.index("handle_path /files/*")]
    assert "return_to={http.request.uri_escaped} 302" in kept


def test_an_app_origin_login_carries_the_prefix_too() -> None:
    out = StringIO()
    login = "https://wip.local:8443/auth/app-login?app=x&return_to={http.request.uri_escaped}"
    _write_forward_auth(out, _cfg(), _route(app="x"), login, stripped=True)
    assert "&return_to=%2Ffiles{http.request.uri_escaped} 302" in out.getvalue()


def test_the_dead_caddy_config_fields_are_gone() -> None:
    fields = set(CaddyConfig.__dataclass_fields__)
    assert not fields & {"admin_email", "gateway_enabled", "has_dex", "dex_service"}


def test_no_file_mode_is_lost_by_the_atomic_path(tmp_path: Path) -> None:
    tree = FileTree()
    tree.add("secret.env", "x", mode=0o600, atomic=True)
    tree.write(tmp_path)
    assert os.stat(tmp_path / "secret.env").st_mode & 0o777 == 0o600


# ── an app cannot take a component's or the front door's name ──────────


@pytest.mark.parametrize("name", ["caddy", "registry", "router"])
def test_an_app_named_after_a_service_is_refused(real_discovery: Discovery, name: str) -> None:
    from .test_app_api_keys import _app, _deployment

    apps = [_app(real_discovery, name)]
    report = validate_all(_deployment("compose", apps), real_discovery.components, apps)
    assert any(f"app {name!r} has the name of" in str(e) for e in report.errors), report.errors


def test_a_free_app_name_passes(real_discovery: Discovery) -> None:
    from .test_app_api_keys import _app, _deployment

    apps = [_app(real_discovery, "my-app")]
    report = validate_all(_deployment("compose", apps), real_discovery.components, apps)
    assert not any("has the name of" in str(e) for e in report.errors), report.errors
