"""CASE-1203 / 1204 / 1205 — the deployer's checks report only what they
established.

A check that could not look fails or says "not checked"; "not applicable"
is decided by the install's spec, never by a missing file; a refusal counts
only beside a control that proves the route exists.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.check_app import (
    check_app_deployability,
    check_healthcheck_probe_binary,
    check_runtime_fetches_nothing,
    check_vite_proxy_port_matches_manifest,
)
from wip_deploy.export_ca import ExportCAError, export_caddy_internal_ca
from wip_deploy.spec import Deployment
from wip_deploy.verify_app_keys import check_rendered_master_key
from wip_deploy.verify_security import (
    check_mongodb_authentication,
    check_nats_authentication,
    verify_live,
)


def _deployment(*, target: str = "compose", apps_only: bool = False) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        hostname="wip.example.org", tls="internal", compose_data_dir=Path("/tmp/d"),
        k8s_namespace="wip-t",
    ))
    if apps_only:
        d.spec.modules.suppress_core = True
    return d


# ── 1203: rendered-file checks ──────────────────────────────────────────


def _compose_with_nats(tmp_path: Path, conf: str) -> None:
    (tmp_path / "docker-compose.yaml").write_text(yaml.safe_dump({"services": {"nats": {}}}))
    (tmp_path / "config" / "nats").mkdir(parents=True)
    (tmp_path / "config" / "nats" / "nats.conf").write_text(conf)


def test_nats_with_no_user_fails(tmp_path: Path) -> None:
    _compose_with_nats(tmp_path, "authorization {\n  users = [\n  ]\n}\nhttp: 127.0.0.1:8222\n")
    r = check_nats_authentication(tmp_path, _deployment())
    assert not r.passed and "no user" in r.message


def test_nats_with_users_passes(tmp_path: Path) -> None:
    _compose_with_nats(tmp_path, "authorization {\n  users = [\n    { user: a, password: b }\n  ]\n}\nhttp: 127.0.0.1:8222\n")
    assert check_nats_authentication(tmp_path, _deployment()).passed


@pytest.mark.parametrize("check", [check_nats_authentication, check_mongodb_authentication])
def test_a_compose_install_without_its_compose_file_fails(tmp_path: Path, check) -> None:
    r = check(tmp_path, _deployment())
    assert not r.passed and "rendered file missing" in r.message


def test_live_checks_are_not_applicable_on_an_apps_only_install() -> None:
    [r] = verify_live(_deployment(apps_only=True), set(), "k")
    assert r.passed and "apps-only" in r.message


# ── 1204 item 5: apps-only apps run with the install's api-key ──────────


def test_the_master_key_rule_does_not_apply_to_apps_only(tmp_path: Path) -> None:
    r = check_rendered_master_key(tmp_path, _deployment(apps_only=True), [])
    assert r.passed and "apps-only" in r.message


# ── 1204: the app checks ────────────────────────────────────────────────


def _manifest(tmp_path: Path, **spec) -> Path:
    path = tmp_path / "wip-app.yaml"
    path.write_text(yaml.safe_dump({"metadata": {"name": "t"}, "spec": spec}))
    return path


def _dockerfile(src: Path, text: str) -> None:
    src.mkdir(exist_ok=True)
    (src / "Dockerfile").write_text(text)


HC = {"healthcheck": {"endpoint": "/api/health", "probe": "curl"}}


def test_curl_installed_only_in_the_build_stage_is_not_in_the_image(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _dockerfile(src, "FROM node:22 AS build\nRUN apt-get install -y curl\nFROM node:22-slim\nCMD [\"node\", \"s.js\"]\n")
    assert not check_healthcheck_probe_binary(src, _manifest(tmp_path, **HC)).passed


def test_curl_on_a_continuation_line_and_apk_flags_count(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _dockerfile(src, "FROM node:22-slim\nRUN apt-get update && apt-get install -y \\\n    tini \\\n    curl\n")
    assert check_healthcheck_probe_binary(src, _manifest(tmp_path, **HC)).passed
    _dockerfile(src, "FROM node:22-alpine\nRUN apk --no-cache add curl\n")
    assert check_healthcheck_probe_binary(src, _manifest(tmp_path, **HC)).passed


def _node_app(src: Path, cmd: str, scripts: dict | None = None, dev: dict | None = None) -> None:
    _dockerfile(src, f"FROM node:22-slim\nRUN npm ci --omit=dev\nCMD {cmd}\n")
    (src / "package.json").write_text(json.dumps({
        "scripts": scripts or {}, "dependencies": {}, "devDependencies": dev or {},
    }))


def test_npm_run_is_followed_into_its_script(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _node_app(src, '["npm", "run", "serve"]', scripts={"serve": "tsx server.ts"}, dev={"tsx": "1"})
    r = check_runtime_fetches_nothing(src)
    assert not r.passed and "tsx" in r.message


def test_an_absolute_bin_path_is_a_runner(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _node_app(src, '["/app/node_modules/.bin/tsx", "server.ts"]', dev={"tsx": "1"})
    assert not check_runtime_fetches_nothing(src).passed


def test_a_bin_is_mapped_to_its_package(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _node_app(src, '["./node_modules/.bin/tsc", "-p", "."]', dev={"typescript": "5"})
    (src / "node_modules" / "typescript").mkdir(parents=True)
    (src / "node_modules" / "typescript" / "package.json").write_text(json.dumps({"bin": {"tsc": "bin/tsc"}}))
    r = check_runtime_fetches_nothing(src)
    assert not r.passed and "typescript" in r.message


def test_an_unmapped_bin_is_not_checked_rather_than_passed(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _node_app(src, '["./node_modules/.bin/tsc", "-p", "."]', dev={"typescript": "5"})
    r = check_runtime_fetches_nothing(src)
    assert r.passed and r.message.startswith("not checked")


def test_a_proxy_section_this_check_cannot_read_fails(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "vite.config.ts").write_text("export default { server: { proxy: { '/api': { target: `http://127.0.0.1:${PORT}` } } } }")
    manifest = _manifest(tmp_path, ports=[{"name": "http", "container_port": 3001}])
    r = check_vite_proxy_port_matches_manifest(src, manifest)
    assert not r.passed and "proxy section" in r.message


def test_no_dockerfile_and_a_missing_manifest_fail(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    report = check_app_deployability(src, manifest_path=tmp_path / "absent.yaml", repo_root=tmp_path)
    failed = {r.name: r.message for r in report.failures}
    assert "production Dockerfile present" in failed
    assert "does not exist" in failed["matching app manifest discovered"]
    assert report.not_checked  # the contract rows the tool cannot read are named


def test_the_repo_root_comes_from_wip_repo_root(tmp_path: Path, monkeypatch) -> None:
    from wip_deploy.check_app import _default_repo_root

    monkeypatch.setenv("WIP_REPO_ROOT", str(tmp_path))
    assert _default_repo_root() == tmp_path.resolve()
    monkeypatch.delenv("WIP_REPO_ROOT")
    root = _default_repo_root()
    assert (root / "components").is_dir() and (root / "deployer").is_dir()


# ── 1205: export-ca ─────────────────────────────────────────────────────


def _ps() -> MagicMock:
    return MagicMock(returncode=0, stdout="t_wip-caddy\n", stderr="")


def test_export_ca_refuses_another_installs_caddy(tmp_path: Path) -> None:
    with patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"), \
         patch("wip_deploy.export_ca.subprocess.run") as run:
        run.side_effect = [_ps(), MagicMock(returncode=0, stdout="/elsewhere/config/caddy/Caddyfile\n", stderr="")]
        with pytest.raises(ExportCAError, match="does not serve this install"):
            export_caddy_internal_ca("t_wip-caddy", install_dir=tmp_path)


def test_export_ca_bounds_each_podman_call(tmp_path: Path) -> None:
    with (
        patch("wip_deploy.export_ca.shutil.which", return_value="/usr/bin/podman"),
        patch("wip_deploy.export_ca.subprocess.run",
              side_effect=subprocess.TimeoutExpired("podman", 30)) as run,
        pytest.raises(ExportCAError, match="did not answer"),
    ):
        export_caddy_internal_ca("t_wip-caddy", install_dir=tmp_path)
    assert run.call_args.kwargs["timeout"] == 30
