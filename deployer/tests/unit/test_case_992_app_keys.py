"""Each app's own key stays inside its namespaces (`verify --security --live`).

An app's derived key is sent across every boundary it must not cross, and
each refusal is paired with a request the same key must get through. These
tests pin the verdicts through an httpx.MockTransport standing in for the
install, and the static check that only a privileged app renders with the
master key, against real compose and k8s renders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Deployment
from wip_deploy.verify_app_keys import (
    ABSENT_ENTRY_ID,
    RESERVED_VIEW_NAME,
    UNRESERVED_PROBE_NAMESPACE,
    AppKey,
    app_keys,
    check_app_keys,
    check_rendered_master_key,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = "https://wip.example"
ADMIN = "admin-key"
COOK = "cook-key"
ALL = {"registry", "def-store", "document-store", "reporting-sync"}
FOREIGN_ENTRY = "0190aaaa-0000-7000-0000-000000000001"
KEYS = [
    AppKey(app="cook", key=COOK, owned=["cooking"],
           reserved={"cooking": {}}, readable={"cooking", "wip"}),
]


def _json(status: int, body: Any = None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {"detail": "x"})


def _enforcing(request: httpx.Request) -> httpx.Response:
    """An install whose boundaries hold."""
    path = request.url.path
    ns = request.url.params.get("namespace")
    key = request.headers.get("X-API-Key")
    if key == ADMIN:
        if path == "/api/registry/namespaces":
            return _json(200, [{"prefix": p} for p in ("wip", "cooking", "kb")])
        if path == "/api/registry/entries":
            return _json(200, {"items": [{"entry_id": FOREIGN_ENTRY}], "total": 1})
        return _json(500)
    if key != COOK:
        return _json(401)
    if path.startswith("/api/registry/entries/"):
        return _json(404, {"detail": f"Entry not found: {path.rsplit('/', 1)[1]}"})
    if path == "/api/document-store/documents":
        return _json(200, {"items": []}) if ns == "cooking" else _json(404)
    if path == "/api/def-store/terminologies":
        return _json(200, {"items": []}) if request.method == "GET" and ns == "wip" else _json(403)
    if path == "/api/reporting-sync/namespace/cooking/views":
        return _json(400)
    if path.startswith("/api/reporting-sync/namespace/"):
        return _json(403)
    if path.startswith("/api/registry/namespaces/"):
        return _json(403)
    return _json(500)


def _run(handler, keys=KEYS, active=ALL, admin=ADMIN):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return check_app_keys(BASE, keys, admin, active, client)


def test_an_install_whose_boundaries_hold_passes():
    [result] = _run(_enforcing)
    assert result.passed, result.fix_hint
    assert "not probed" in result.message


def test_reading_another_apps_namespace_fails():
    def handler(request):
        if request.url.path == "/api/document-store/documents":
            return _json(200, {"items": []})
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "GET documents in 'kb': app key got 200 — not a refusal" in result.fix_hint


def test_an_unknown_app_key_is_a_failure_not_a_refusal():
    """401 means the service never loaded the app's key; every refusal on
    it would pass without testing anything."""
    def handler(request):
        if request.url.path.startswith("/api/reporting-sync/"):
            return _json(401)
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "does not recognise the app's key" in result.fix_hint


def test_a_key_that_cannot_reach_its_own_namespace_fails():
    """The paired request: without it a route that refuses everyone would
    pass every refusal row."""
    def handler(request):
        if request.url.path == "/api/document-store/documents":
            return _json(404)
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "GET documents in its own 'cooking': app key got 404, expected 200" in result.fix_hint


def test_a_view_permission_control_expects_the_reserved_name_refusal():
    def handler(request):
        if request.url.path == "/api/reporting-sync/namespace/cooking/views":
            assert request.read() and RESERVED_VIEW_NAME.encode() in request.read()
            return _json(403)
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "view permission in its own 'cooking'" in result.fix_hint


def test_a_refused_entry_must_read_like_a_missing_one():
    def handler(request):
        if request.url.path == f"/api/registry/entries/{FOREIGN_ENTRY}":
            return _json(404, {"detail": "Entry in a closed namespace"})
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "shows the entry exists" in result.fix_hint


def test_a_namespace_outside_the_reservation_must_be_refused():
    def handler(request):
        if request.url.path == f"/api/registry/namespaces/{UNRESERVED_PROBE_NAMESPACE}":
            return _json(200, {"prefix": UNRESERVED_PROBE_NAMESPACE})
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert f"it may have written namespace '{UNRESERVED_PROBE_NAMESPACE}'" in result.fix_hint


def test_changing_its_own_namespace_against_the_reservation_must_be_refused():
    def handler(request):
        if request.url.path == "/api/registry/namespaces/cooking":
            return _json(200)
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "'cooking' may now be isolation_mode=strict, restore it" in result.fix_hint


def test_writing_into_wip_must_be_refused():
    def handler(request):
        if request.url.path == "/api/def-store/terminologies" and request.method == "POST":
            return _json(200, {"results": [{"status": "created"}]})
        return _enforcing(request)

    [result] = _run(handler)
    assert not result.passed
    assert "terminology 'WIP_VERIFY_PROBE' in 'wip'" in result.fix_hint


def test_rows_without_a_target_are_reported_as_skipped_never_as_passed():
    """Alone on an install, with no namespace yet, an app's cross-namespace
    and own-namespace rows have nothing to probe; the message says so."""
    def handler(request):
        if request.headers.get("X-API-Key") == ADMIN and request.url.path == "/api/registry/namespaces":
            return _json(200, [{"prefix": "wip"}])
        return _enforcing(request)

    [result] = _run(handler)
    assert result.passed, result.fix_hint
    assert "no other namespace on this install" in result.message
    assert "the app has not bootstrapped" in result.message


def test_no_app_keys_no_results():
    assert _run(_enforcing, keys=[]) == []


def test_a_missing_app_key_secret_fails():
    keys = [AppKey(app="cook", key=None, owned=["cooking"], reserved={"cooking": {}})]
    [result] = _run(_enforcing, keys=keys)
    assert not result.passed
    assert "not in the install's secret backend" in result.message


def test_the_absent_entry_id_is_shaped_like_an_id():
    assert len(ABSENT_ENTRY_ID) == len(FOREIGN_ENTRY)


# --- the rendered install -------------------------------------------------


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, name: str, **spec: Any) -> App:
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = name
    # A privileged app holds the master key and declares no namespaces.
    raw["spec"]["namespaces"] = [] if spec.get("privileged_key") else [
        {"role": "main", "name": name, "env": "WIP_NAMESPACE"},
    ]
    raw["spec"].update(spec)
    return App.model_validate(raw)


def _write(target: str, disc: Discovery, apps: list[App], tmp_path: Path) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name=a.metadata.name) for a in apps]
    secrets = ResolvedSecrets(values={
        n: f"secret-of-{n}" for n in collect_required_secrets(d, disc.components, apps)
    })
    render = render_k8s if target == "k8s" else render_compose
    tree = render(d, disc.components, apps, secrets)
    for path, f in tree.files.items():
        out = tmp_path / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f.content)
    return d


MASTER = {"privileged_key": True, "env": {"required": [
    {"name": "WIP_API_KEY", "source": {"from_secret": "api-key"}},
]}}


@pytest.mark.parametrize("target", ["compose", "k8s"])
def test_only_the_privileged_app_renders_with_the_master_key(
    discovery: Discovery, tmp_path: Path, target: str,
) -> None:
    apps = [_app(discovery, "console", **MASTER), _app(discovery, "cook")]
    d = _write(target, discovery, apps, tmp_path)
    result = check_rendered_master_key(tmp_path, d, apps)
    assert result.passed, result.message
    assert result.message == "master key held by: console"


@pytest.mark.parametrize("target", ["compose", "k8s"])
def test_a_render_handing_an_app_the_master_key_fails(
    discovery: Discovery, tmp_path: Path, target: str,
) -> None:
    """The render is read, not the spec: a renderer that bypassed the
    spec's refusal is caught. Simulated by declaring 'cook' privileged
    for the render and checking against its unprivileged manifest."""
    sneaky = _app(discovery, "cook", **MASTER)
    d = _write(target, discovery, [sneaky], tmp_path)
    result = check_rendered_master_key(tmp_path, d, [_app(discovery, "cook")])
    assert not result.passed
    assert "cook render(s) WIP_API_KEY from the master key" in result.message


def test_app_keys_reads_each_apps_secret_and_scope(discovery: Discovery, tmp_path: Path) -> None:
    apps = [_app(discovery, "console", **MASTER), _app(discovery, "cook")]
    d = _write("compose", discovery, apps, tmp_path)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "app-cook-api-key").write_text("cook-secret\n")
    d.spec.secrets.location = str(secrets)
    [key] = app_keys(tmp_path, d, apps)
    assert key.app == "cook"
    assert key.key == "cook-secret"
    assert key.owned == ["cook"]
    assert key.readable == {"cook", "wip"}
