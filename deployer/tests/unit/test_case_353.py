"""Regression coverage for CASE-353 — validate-manifest external.

Two surfaces under test:

  - `validate_manifest.validate_manifest(path, repo_root)` —
    the pure-function validator. Loads the manifest, runs schema
    + reference checks, returns errors.
  - `wip-deploy validate-manifest <path>` — the CLI wrapper.
    Exit codes 0/1/2.

We use the real `apps/react-console/wip-app.yaml` as the known-good
fixture (it's also the canonical example referenced in
`docs/wip-guide.md` §6.1). Synthetic broken manifests cover the
error paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.cli import app
from wip_deploy.discovery import find_repo_root
from wip_deploy.validate_manifest import (
    ManifestLoadError,
    load_manifest,
    resolve_manifest_path,
    validate_manifest,
)

runner = CliRunner()


@pytest.fixture(scope="module")
def repo_root() -> Path:
    """The current WIP repo root — where the real component+app manifests
    live. Used as the discovery target for cross-reference checks."""
    return find_repo_root()


@pytest.fixture
def known_good_manifest(repo_root: Path) -> Path:
    """The react-console manifest — known to pass all checks."""
    return repo_root / "apps" / "react-console" / "wip-app.yaml"


def _write_manifest(path: Path, body: dict) -> Path:
    """Materialize a manifest dict to YAML."""
    path.write_text(yaml.safe_dump(body))
    return path


def _valid_manifest_body(name: str = "test-app") -> dict:
    """Minimal-but-valid manifest body for synthetic tests."""
    return {
        "api_version": "wip.dev/v1",
        "kind": "App",
        "metadata": {
            "name": name,
            "category": "optional",
            "description": "Synthetic test app",
        },
        "spec": {
            "image": {"name": name, "tag": "v0.0.1"},
            "ports": [{"name": "http", "container_port": 3099}],
            "env": {"required": [], "optional": []},
            "routes": [{"path": f"/apps/{name}", "auth_required": True}],
        },
        "app_metadata": {
            "display_name": "Test App",
            "route_prefix": f"/apps/{name}",
            "ui_only": True,
        },
    }


# ────────────────────────────────────────────────────────────────────
# resolve_manifest_path
# ────────────────────────────────────────────────────────────────────


class TestResolveManifestPath:
    def test_file_path_returned_as_is(self, tmp_path: Path) -> None:
        f = tmp_path / "wip-app.yaml"
        f.write_text("{}")
        assert resolve_manifest_path(f) == f

    def test_directory_locates_wip_app_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "wip-app.yaml"
        f.write_text("{}")
        assert resolve_manifest_path(tmp_path) == f

    def test_directory_without_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestLoadError, match="no `wip-app.yaml`"):
            resolve_manifest_path(tmp_path)

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestLoadError, match="no such file or directory"):
            resolve_manifest_path(tmp_path / "nope")


# ────────────────────────────────────────────────────────────────────
# load_manifest — parse-level failures
# ────────────────────────────────────────────────────────────────────


class TestLoadManifest:
    def test_bad_yaml_raises_load_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("{this is not: valid yaml: at all")
        with pytest.raises(ManifestLoadError, match="YAML parse"):
            load_manifest(bad)

    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        listy = tmp_path / "list.yaml"
        listy.write_text("- a\n- b\n")
        with pytest.raises(ManifestLoadError, match="must be a YAML mapping"):
            load_manifest(listy)


# ────────────────────────────────────────────────────────────────────
# validate_manifest — schema + reference happy paths
# ────────────────────────────────────────────────────────────────────


class TestValidateManifestHappyPath:
    def test_known_good_manifest_passes(
        self, known_good_manifest: Path, repo_root: Path
    ) -> None:
        """The actual react-console manifest validates clean against the
        live WIP repo. If this fails, the validator and the manifest
        have drifted from each other."""
        app_obj, errors = validate_manifest(known_good_manifest, repo_root)
        assert app_obj is not None
        assert app_obj.metadata.name == "react-console"
        assert errors == []

    def test_synthetic_minimal_manifest_passes(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        """Minimal synthetic manifest with no references — schema-only
        path stays clean."""
        manifest = _write_manifest(
            tmp_path / "wip-app.yaml",
            _valid_manifest_body("synthetic-mini"),
        )
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert app_obj is not None
        assert errors == []


# ────────────────────────────────────────────────────────────────────
# Schema failures
# ────────────────────────────────────────────────────────────────────


class TestSchemaFailures:
    def test_missing_required_field_flagged(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body()
        del body["metadata"]["description"]
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert app_obj is None  # schema gate blocks downstream
        assert any("description" in e.field for e in errors)

    def test_wrong_api_version_flagged(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body()
        body["api_version"] = "wip.dev/v2"
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert app_obj is None
        assert any("api_version" in e.field for e in errors)


# ────────────────────────────────────────────────────────────────────
# Reference failures
# ────────────────────────────────────────────────────────────────────


class TestReferenceFailures:
    def test_unknown_from_component_flagged_with_hint(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body("ref-bad-component")
        body["spec"]["env"]["required"].append({
            "name": "MONGO_URL",
            "source": {"from_component": "totallynotacomponent"},
        })
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert app_obj is not None  # schema is fine; reference is broken
        ref_errors = [e for e in errors if "totallynotacomponent" in e.message]
        assert len(ref_errors) == 1
        assert "from_component" in ref_errors[0].field
        # The hint should list discovered components so the operator
        # sees what's available — common typo recovery.
        assert ref_errors[0].hint is not None
        assert "mongodb" in ref_errors[0].hint  # real component name appears

    def test_unknown_depends_on_flagged(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body("ref-bad-dep")
        body["spec"]["depends_on"] = ["mongodb", "imaginary-svc"]
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert app_obj is not None
        assert any(
            "imaginary-svc" in e.message and "depends_on" in e.field
            for e in errors
        )
        # The valid dep doesn't fire.
        assert not any(
            "mongodb" in e.message and "depends_on" in e.field
            for e in errors
        )

    def test_from_secret_accepts_arbitrary_names(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        """v1 scope: secret existence is install-time state. The
        validator accepts any non-empty secret name as plausible —
        only `from_component*` names need to resolve."""
        body = _valid_manifest_body("ref-secret")
        body["spec"]["env"]["required"].append({
            "name": "MY_KEY",
            "source": {"from_secret": "totally-made-up-secret-name"},
        })
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert errors == []


# ────────────────────────────────────────────────────────────────────
# Route collision detection
# ────────────────────────────────────────────────────────────────────


class TestRouteCollisions:
    def test_route_path_collision_flagged(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body("ref-route-collide")
        # /apps/rc is react-console's route — colliding deliberately.
        body["spec"]["routes"] = [{"path": "/apps/rc", "auth_required": True}]
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert any(
            "react-console" in e.message and "routes[0].path" in e.field
            for e in errors
        )

    def test_route_prefix_collision_flagged(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body("ref-prefix-collide")
        body["app_metadata"]["route_prefix"] = "/apps/rc"
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        assert any(
            "route_prefix" in e.field and "react-console" in e.message
            for e in errors
        )

    def test_same_name_excluded_from_collision_check(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        """When validating an UPDATE to an existing app (same metadata.name
        as a discovered app), the discovered app is excluded from the
        collision-check set — the app isn't colliding with itself."""
        body = _valid_manifest_body("react-console")
        # Use react-console's actual routes + prefix.
        body["spec"]["routes"] = [{"path": "/apps/rc", "auth_required": True}]
        body["app_metadata"]["route_prefix"] = "/apps/rc"
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        app_obj, errors = validate_manifest(manifest, repo_root)
        # No route_prefix or route-path collision — same-name self-exclusion.
        collision_errors = [
            e for e in errors
            if "route_prefix" in e.field or "routes" in e.field
        ]
        assert collision_errors == [], (
            f"unexpected collision errors when validating self: "
            f"{[e.format() for e in collision_errors]}"
        )


# ────────────────────────────────────────────────────────────────────
# CLI exit codes
# ────────────────────────────────────────────────────────────────────


class TestCLIExitCodes:
    def test_known_good_exits_zero(
        self, known_good_manifest: Path, repo_root: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "validate-manifest", str(known_good_manifest),
                "--repo-root", str(repo_root),
            ],
        )
        assert result.exit_code == 0
        assert "manifest is valid" in result.output

    def test_missing_file_exits_two(self, tmp_path: Path, repo_root: Path) -> None:
        result = runner.invoke(
            app,
            [
                "validate-manifest", str(tmp_path / "does-not-exist.yaml"),
                "--repo-root", str(repo_root),
            ],
        )
        assert result.exit_code == 2
        assert "no such file" in result.output

    def test_schema_failure_exits_one(self, tmp_path: Path, repo_root: Path) -> None:
        body = _valid_manifest_body()
        del body["metadata"]["description"]
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        result = runner.invoke(
            app,
            [
                "validate-manifest", str(manifest),
                "--repo-root", str(repo_root),
            ],
        )
        assert result.exit_code == 1
        assert "validation error" in result.output

    def test_reference_failure_exits_one(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        body = _valid_manifest_body("cli-bad-ref")
        body["spec"]["env"]["required"].append({
            "name": "X", "source": {"from_component": "made-up"},
        })
        manifest = _write_manifest(tmp_path / "wip-app.yaml", body)
        result = runner.invoke(
            app,
            [
                "validate-manifest", str(manifest),
                "--repo-root", str(repo_root),
            ],
        )
        assert result.exit_code == 1
        assert "made-up" in result.output
