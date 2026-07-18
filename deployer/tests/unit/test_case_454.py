"""An HTTP healthcheck's probe binary must exist in the production image.

The rendered probe is a shell curl/wget chain; an image with neither binary
exits 127 on every probe and reports permanently unhealthy while the app
serves fine. check_healthcheck_probe_binary pre-flights this heuristically:
last FROM decides the runtime base (alpine ⇒ busybox wget), an apk/apt
install line naming the binary counts as installed, and declaring no
healthcheck at all is a legal alternative (plain Up, no health status).
"""

from pathlib import Path

import yaml

from wip_deploy.check_app import check_healthcheck_probe_binary


def _write_manifest(tmp_path: Path, healthcheck: dict | None) -> Path:
    spec: dict = {
        "api_version": "wip.dev/v1",
        "kind": "App",
        "metadata": {"name": "probe-test"},
        "spec": {"ports": [{"name": "http", "container_port": 3011}]},
    }
    if healthcheck is not None:
        spec["spec"]["healthcheck"] = healthcheck
    path = tmp_path / "wip-app.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def _write_dockerfile(tmp_path: Path, text: str) -> None:
    (tmp_path / "Dockerfile").write_text(text)


HTTP_HC = {"endpoint": "/api/health", "probe": "auto"}


class TestCheckHealthcheckProbeBinary:
    def test_passes_with_no_healthcheck_declared(self, tmp_path: Path) -> None:
        m = _write_manifest(tmp_path, None)
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert r.passed
        assert "no healthcheck declared" in r.message

    def test_passes_for_exec_style_command_check(self, tmp_path: Path) -> None:
        m = _write_manifest(tmp_path, {"command": ["true"]})
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert r.passed

    def test_passes_on_alpine_runtime_base(self, tmp_path: Path) -> None:
        m = _write_manifest(tmp_path, HTTP_HC)
        _write_dockerfile(tmp_path, "FROM node:20-alpine\nCMD [\"node\"]\n")
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert r.passed
        assert "alpine" in r.message

    def test_fails_on_slim_base_without_install(self, tmp_path: Path) -> None:
        """The wip-song shape: node:22-slim ships neither curl nor wget."""
        m = _write_manifest(tmp_path, HTTP_HC)
        _write_dockerfile(tmp_path, "FROM node:22-slim\nCMD [\"node\"]\n")
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert not r.passed
        assert r.fix_hint is not None
        assert "127" in r.fix_hint

    def test_passes_on_slim_base_with_wget_install(self, tmp_path: Path) -> None:
        m = _write_manifest(tmp_path, HTTP_HC)
        _write_dockerfile(
            tmp_path,
            "FROM node:22-slim\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "wget && rm -rf /var/lib/apt/lists/*\n",
        )
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert r.passed

    def test_multistage_last_from_decides(self, tmp_path: Path) -> None:
        """Alpine build stage does not rescue a slim runtime stage."""
        m = _write_manifest(tmp_path, HTTP_HC)
        _write_dockerfile(
            tmp_path,
            "FROM node:20-alpine AS build\nRUN npm ci\n"
            "FROM node:22-slim\nCMD [\"node\"]\n",
        )
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert not r.passed

    def test_probe_curl_not_satisfied_by_alpine_busybox(self, tmp_path: Path) -> None:
        """busybox provides wget, never curl — an explicit probe: curl
        needs a real curl install even on an alpine base."""
        m = _write_manifest(tmp_path, {"endpoint": "/api/health", "probe": "curl"})
        _write_dockerfile(tmp_path, "FROM node:20-alpine\nCMD [\"node\"]\n")
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert not r.passed

    def test_fails_when_production_dockerfile_missing(self, tmp_path: Path) -> None:
        m = _write_manifest(tmp_path, HTTP_HC)
        r = check_healthcheck_probe_binary(tmp_path, m)
        assert not r.passed
        assert "no production Dockerfile" in r.message
