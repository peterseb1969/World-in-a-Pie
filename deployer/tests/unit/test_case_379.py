"""Regression coverage for CASE-379-C — check-app-deployability.

The slash command extends CASE-353's validate-manifest with source-repo
checks. Without these, the gaps that surfaced over today's CASE-375
debugging journey (four sequential blow-ups) keep surfacing on every
new wip-deployable app.

This test file covers:

  1. **Individual check functions** — each `check_*` returns the right
     CheckResult given a controlled fixture.
  2. **Manifest auto-discovery** — finds the right manifest for a
     real-world hyphenation drift case (react-console vs
     wip-reactconsole package name).
  3. **CLI integration** — exit codes + output structure match the
     contract Peter expects.
  4. **Live state** — runs against the four real apps in the repo
     (react-console, clintrial, wip-kb pass; WIP-AA fails on the
     missing-manifest gap, since AA-YAC hasn't registered yet).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.check_app import (
    check_app_deployability,
    check_dockerfile_dev,
    check_manifest_declares_dev_port,
    check_package_dev_script,
    check_vite_host,
    check_vite_proxy_port_matches_manifest,
    find_manifest_for_source,
)
from wip_deploy.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _make_app_source(
    tmp_path: Path,
    *,
    pkg_name: str = "test-app",
    has_dockerfile_dev: bool = True,
    has_vite_config: bool = True,
    vite_host: str | None = "0.0.0.0",
    vite_proxy_port: int | None = None,
    has_dev_script: bool = True,
) -> Path:
    """Build a minimal but realistic app source dir for the checks to
    inspect. Each kwarg flips one gap on/off."""
    src = tmp_path / "src"
    src.mkdir()

    pkg: dict = {"name": pkg_name, "scripts": {}}
    if has_dev_script:
        pkg["scripts"]["dev"] = "vite"
    (src / "package.json").write_text(json.dumps(pkg))

    if has_dockerfile_dev:
        (src / "Dockerfile.dev").write_text("FROM node:20-alpine\nCMD npm run dev\n")

    if has_vite_config:
        lines = [
            "import { defineConfig } from 'vite'",
            "export default defineConfig({",
            "  server: {",
        ]
        if vite_host is not None:
            lines.append(f"    host: '{vite_host}',")
        if vite_proxy_port is not None:
            lines.append("    proxy: {")
            lines.append(f"      '/api': 'http://localhost:{vite_proxy_port}',")
            lines.append("    },")
        lines.append("  },")
        lines.append("})")
        (src / "vite.config.ts").write_text("\n".join(lines))

    return src


def _make_manifest(tmp_path: Path, *, name: str, http_port: int, dev_port: int | None = 5173) -> Path:
    """Build a minimal but renderer-valid wip-app.yaml.

    Uses only `literal` + `from_secret` env sources so validate_manifest's
    cross-reference checks pass against an empty temp repo. Production
    manifests (e.g., react-console) use `from_component: router` which
    requires a discovered `router` component — out of test scope.
    """
    apps = tmp_path / "apps" / name
    apps.mkdir(parents=True)
    ports = [{"name": "http", "container_port": http_port}]
    if dev_port is not None:
        ports.append({"name": "dev", "container_port": dev_port})
    spec = {
        "api_version": "wip.dev/v1",
        "kind": "App",
        "metadata": {"name": name, "category": "optional", "description": "test"},
        "spec": {
            "image": {"name": name, "tag": "test"},
            "ports": ports,
            "env": {
                "required": [
                    {"name": "WIP_API_KEY", "source": {"from_secret": "api-key"}},
                    {"name": "PORT", "source": {"literal": str(http_port)}},
                    {"name": "APP_BASE_PATH", "source": {"literal": f"/apps/{name}"}},
                    {"name": "NODE_ENV", "source": {"literal": "production"}},
                ],
            },
            "routes": [{"path": f"/apps/{name}", "auth_required": True}],
            "healthcheck": {"endpoint": f"/apps/{name}/api/health"},
        },
        "app_metadata": {
            "display_name": name.replace("-", " ").title(),
            "route_prefix": f"/apps/{name}",
            "ui_only": False,
        },
    }
    path = apps / "wip-app.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


# ────────────────────────────────────────────────────────────────────
# Individual check functions
# ────────────────────────────────────────────────────────────────────


class TestCheckDockerfileDev:
    def test_passes_when_present(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, has_dockerfile_dev=True)
        r = check_dockerfile_dev(src)
        assert r.passed
        assert "found at" in r.message

    def test_fails_when_missing(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, has_dockerfile_dev=False)
        r = check_dockerfile_dev(src)
        assert not r.passed
        assert r.fix_hint is not None
        # Fix hint must reference the actual failure mode CASE-375
        # documented — NODE_ENV contradiction → SPA 404.
        assert "NODE_ENV" in r.fix_hint


class TestCheckViteHost:
    def test_passes_when_0_0_0_0(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, vite_host="0.0.0.0")
        r = check_vite_host(src)
        assert r.passed

    def test_fails_when_localhost(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, vite_host="localhost")
        r = check_vite_host(src)
        assert not r.passed
        assert "ECONNREFUSED" in (r.fix_hint or "")

    def test_fails_when_host_omitted(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, vite_host=None)
        r = check_vite_host(src)
        assert not r.passed

    def test_fails_when_no_vite_config(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, has_vite_config=False)
        r = check_vite_host(src)
        assert not r.passed


class TestCheckViteProxyPortMatchesManifest:
    def test_passes_when_aligned(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, vite_proxy_port=3012)
        manifest = _make_manifest(tmp_path, name="t", http_port=3012)
        r = check_vite_proxy_port_matches_manifest(src, manifest)
        assert r.passed

    def test_fails_on_port_mismatch_3001_vs_3012(self, tmp_path: Path) -> None:
        """Exact reproduction of CASE-375's WIP-KB foot-gun: vite proxy
        copy-pasted from clintrial (port 3001) but Express is on 3012."""
        src = _make_app_source(tmp_path, vite_proxy_port=3001)
        manifest = _make_manifest(tmp_path, name="t", http_port=3012)
        r = check_vite_proxy_port_matches_manifest(src, manifest)
        assert not r.passed
        assert "3001" in r.message
        assert "3012" in r.message
        # Fix hint must name the WIP-KB scaffold mistake
        assert "scaffold copy-paste" in (r.fix_hint or "").lower() or "3012" in (r.fix_hint or "")

    def test_skips_when_no_proxy_section(self, tmp_path: Path) -> None:
        """Some apps (like clintrial) don't proxy through vite — they
        SSR proxy in Express. The check should skip gracefully, not
        false-positive."""
        src = _make_app_source(tmp_path, vite_proxy_port=None)
        manifest = _make_manifest(tmp_path, name="t", http_port=3012)
        r = check_vite_proxy_port_matches_manifest(src, manifest)
        assert r.passed
        assert "skipped" in r.message.lower()


class TestCheckPackageDevScript:
    def test_passes_when_dev_present(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, has_dev_script=True)
        r = check_package_dev_script(src)
        assert r.passed

    def test_fails_when_dev_missing(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, has_dev_script=False)
        r = check_package_dev_script(src)
        assert not r.passed
        assert "dev" in r.fix_hint.lower()


class TestCheckManifestDeclaresDevPort:
    def test_passes_when_both_present(self, tmp_path: Path) -> None:
        manifest = _make_manifest(tmp_path, name="t", http_port=3012, dev_port=5173)
        r = check_manifest_declares_dev_port(manifest)
        assert r.passed

    def test_fails_when_dev_missing(self, tmp_path: Path) -> None:
        manifest = _make_manifest(tmp_path, name="t", http_port=3012, dev_port=None)
        r = check_manifest_declares_dev_port(manifest)
        assert not r.passed
        assert "dev" in r.message
        # Fix hint cites CASE-55 (the original dev-port contract)
        assert "CASE-55" in (r.fix_hint or "")

    def test_fails_when_no_manifest(self) -> None:
        r = check_manifest_declares_dev_port(None)
        assert not r.passed


# ────────────────────────────────────────────────────────────────────
# Manifest auto-discovery — the real-world hyphenation drift case
# ────────────────────────────────────────────────────────────────────


class TestFindManifestForSource:
    def test_direct_match_when_dir_equals_pkg_name(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, pkg_name="myapp")
        _make_manifest(tmp_path, name="myapp", http_port=3000)
        m, name, _ = find_manifest_for_source(src, tmp_path)
        assert m is not None
        assert name == "myapp"

    def test_hyphenation_drift_react_console(self, tmp_path: Path) -> None:
        """The real react-console case: package.json `name` is
        `wip-reactconsole` (no hyphen between react+console), manifest
        dir is `react-console` (with hyphen). The auto-discovery must
        match these via the normalize-and-substring fallback."""
        src = _make_app_source(tmp_path, pkg_name="wip-reactconsole")
        _make_manifest(tmp_path, name="react-console", http_port=3011)
        m, name, _ = find_manifest_for_source(src, tmp_path)
        assert m is not None
        assert name == "react-console"

    def test_clintrial_subdir_case(self, tmp_path: Path) -> None:
        """clintrial's package.json `name` is `clintrial-explorer` but
        the app is registered as just `clintrial`. The substring match
        on normalized names handles this."""
        src = _make_app_source(tmp_path, pkg_name="clintrial-explorer")
        _make_manifest(tmp_path, name="clintrial", http_port=3001)
        m, name, _ = find_manifest_for_source(src, tmp_path)
        assert m is not None
        assert name == "clintrial"

    def test_no_match_returns_actionable_note(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, pkg_name="something-unrelated")
        _make_manifest(tmp_path, name="other", http_port=3000)
        # apps/ dir must exist for the scan
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert m is None
        assert note is not None
        assert "something-unrelated" in note

    def test_no_package_json_returns_note(self, tmp_path: Path) -> None:
        src = tmp_path / "no-pkg"
        src.mkdir()
        m, name, note = find_manifest_for_source(src, tmp_path)
        assert m is None
        assert "package.json" in (note or "")


# ────────────────────────────────────────────────────────────────────
# Full report end-to-end
# ────────────────────────────────────────────────────────────────────


class TestCheckAppDeployabilityReport:
    def test_all_pass_synthetic(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, pkg_name="t", vite_proxy_port=3012)
        _make_manifest(tmp_path, name="t", http_port=3012)
        report = check_app_deployability(src, repo_root=tmp_path)
        assert report.ok, [f"{r.name}: {r.message}" for r in report.failures]

    def test_missing_manifest_reports_one_failure(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, pkg_name="t")
        # apps dir exists but no matching manifest
        (tmp_path / "apps").mkdir()
        report = check_app_deployability(src, repo_root=tmp_path)
        assert not report.ok
        names = [r.name for r in report.failures]
        assert "matching app manifest discovered" in names


# ────────────────────────────────────────────────────────────────────
# CLI integration
# ────────────────────────────────────────────────────────────────────


class TestCheckAppDeployabilityCLI:
    def test_help_works(self) -> None:
        r = runner.invoke(app, ["check-app-deployability", "--help"])
        assert r.exit_code == 0
        # typer wraps help text; just check for key tokens
        assert "deployab" in r.output  # "deployable" / "deployability"
        assert "CASE-379" in r.output

    def test_exit_2_when_source_missing(self) -> None:
        r = runner.invoke(app, ["check-app-deployability", "/no/such/dir"])
        assert r.exit_code == 2
        assert "does not exist" in r.output

    def test_exit_0_on_synthetic_perfect_app(self, tmp_path: Path) -> None:
        src = _make_app_source(tmp_path, pkg_name="t", vite_proxy_port=3012)
        _make_manifest(tmp_path, name="t", http_port=3012)
        r = runner.invoke(
            app,
            ["check-app-deployability", str(src), "--repo-root", str(tmp_path)],
        )
        assert r.exit_code == 0, r.output
        assert "check(s) passed" in r.output
        assert "App is wip-deployable" in r.output

    def test_exit_1_with_fix_hints_when_failures(self, tmp_path: Path) -> None:
        src = _make_app_source(
            tmp_path, pkg_name="t",
            has_dockerfile_dev=False,
            vite_host="localhost",
        )
        _make_manifest(tmp_path, name="t", http_port=3012)
        r = runner.invoke(
            app,
            ["check-app-deployability", str(src), "--repo-root", str(tmp_path)],
        )
        assert r.exit_code == 1, r.output
        # Both failures should be visible with fix hints
        assert "Dockerfile.dev" in r.output
        assert "host: '0.0.0.0'" in r.output or "0.0.0.0" in r.output


# ────────────────────────────────────────────────────────────────────
# Live check against the real apps in this repo — guards the gold
# standard. If react-console / clintrial / wip-kb ever stop being
# wip-deployable, these tests catch it.
# ────────────────────────────────────────────────────────────────────


class TestLiveAgainstRealApps:
    @pytest.mark.parametrize(
        ("source_subdir",),
        [
            ("WIP-ReactConsole",),
            ("WIP-ClinTrial/clintrial-explorer",),
            ("WIP-KB",),
        ],
    )
    def test_real_app_passes_all_checks(self, source_subdir: str) -> None:
        """Skip if the source repo isn't checked out on this machine —
        CI doesn't have these. They're for local-dev validation."""
        src = Path.home() / "Development" / source_subdir
        if not src.is_dir():
            pytest.skip(f"source repo not present: {src}")
        report = check_app_deployability(src, repo_root=REPO_ROOT)
        assert report.ok, (
            f"real app at {src} unexpectedly fails the contract:\n"
            + "\n".join(f"  ✗ {r.name}: {r.message}" for r in report.failures)
        )
