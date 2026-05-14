"""CLI tests for CASE-373 Phase 1 — `wip-deploy import-bundle`.

Covers:

  - Full import: writes api-key + external-ca.crt + bootstrap.yaml,
    emits the suggested install command.
  - --update-ca-only: writes external-ca.crt only, leaves api-key
    untouched (FR-YAC caveat #2 — CA-rotation primitive).
  - Bundle on disk vs bundle on stdin.
  - Parse errors surface as friendly CLI output, not tracebacks.
  - Idempotent on re-run.
  - Secret files written with 0600.

The CLI tests build on the parser fixture from
`test_case_373_parser.py` indirectly — they pass strings/Paths to the
CLI rather than re-deriving the schema. The shared `isolated_home`
pattern from test_case_360.py keeps `Path.home()` sandboxed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()


_FAKE_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCCATigAwIBAgIQ...fake-test-cert...\n"
    "-----END CERTIFICATE-----\n"
)

# A second cert payload (different bytes inside the BEGIN/END envelope)
# so the --update-ca-only test can assert the CA file actually
# *changed* — not just that it stayed valid.
_FAKE_PEM_ROTATED = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCCATigAwIBAgIQ...rotated-test-cert-fresh-bytes...\n"
    "-----END CERTIFICATE-----\n"
)


def _bundle_yaml(
    *,
    pem: str = _FAKE_PEM,
    permissions: str = "read",
    namespaces: list | None = None,
    name: str = "test-bundle",
) -> str:
    body: dict = {
        "api_version": "wip.dev/v1",
        "kind": "BootstrapBundle",
        "metadata": {
            "name": name,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "spec": {
            "external_base_url": "https://wip.example/",
            "api_key": {
                "value": "opaque-secret-value",
                "name": name,
                "scope": {
                    "namespaces": namespaces or ["kb"],
                    "permissions": permissions,
                },
                "expires_at": (
                    datetime.now(UTC) + timedelta(days=365)
                ).isoformat(),
            },
            "ca_cert": pem,
            "suggested_apps": [{"name": "react-console"}],
        },
    }
    return yaml.safe_dump(body, sort_keys=False)


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


# ──────────────────────────────────────────────────────────────────────
# Happy path — file-based bundle


def test_import_bundle_writes_secrets_and_bootstrap_state(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(_bundle_yaml())

    result = runner.invoke(
        app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"]
    )
    assert result.exit_code == 0, result.output
    assert "Imported bundle" in result.output

    install_dir = isolated_home / ".wip-deploy" / "laptop-rc"
    api_key = install_dir / "secrets" / "api-key"
    ca = install_dir / "secrets" / "external-ca.crt"
    bootstrap = install_dir / "bootstrap.yaml"

    assert api_key.read_text() == "opaque-secret-value\n"
    assert ca.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    assert bootstrap.exists()

    state = yaml.safe_load(bootstrap.read_text())
    assert state["imported_from_bundle"]["name"] == "test-bundle"
    assert state["imported_from_bundle"]["api_key"]["scope"]["permissions"] == "read"
    assert state["imported_from_bundle"]["external_base_url"] == "https://wip.example/"


def test_import_bundle_emits_suggested_install_command(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(_bundle_yaml())

    result = runner.invoke(
        app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"]
    )
    assert result.exit_code == 0
    # The suggested command threads everything the operator needs.
    assert "wip-deploy install --name laptop-rc" in result.output
    assert "--apps-only" in result.output
    assert "--remote-wip https://wip.example/" in result.output
    assert "--app react-console" in result.output


def test_import_bundle_idempotent_on_rerun(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(_bundle_yaml())

    first = runner.invoke(
        app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"]
    )
    second = runner.invoke(
        app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"]
    )
    assert first.exit_code == 0
    assert second.exit_code == 0

    # Same content; second run overwrites with identical bytes.
    api_key = isolated_home / ".wip-deploy" / "laptop-rc" / "secrets" / "api-key"
    assert api_key.read_text() == "opaque-secret-value\n"


# ──────────────────────────────────────────────────────────────────────
# --update-ca-only (FR-YAC caveat #2)


def test_update_ca_only_refreshes_ca_without_touching_api_key(
    isolated_home: Path, tmp_path: Path
) -> None:
    # First, a full import to seed an api-key.
    initial = tmp_path / "initial.yaml"
    initial.write_text(_bundle_yaml(pem=_FAKE_PEM))
    runner.invoke(app, ["import-bundle", str(initial), "--name", "laptop-rc"])

    install_dir = isolated_home / ".wip-deploy" / "laptop-rc"
    api_key = install_dir / "secrets" / "api-key"
    ca = install_dir / "secrets" / "external-ca.crt"
    bootstrap = install_dir / "bootstrap.yaml"

    original_key = api_key.read_text()
    original_bootstrap = bootstrap.read_text()
    assert b"fake-test-cert" in ca.read_bytes()

    # Then a --update-ca-only import with a different api-key but a
    # rotated CA. The api-key in the file is irrelevant — the bundle
    # parses fine but only the CA is consumed.
    rotated = tmp_path / "rotated.yaml"
    rotated.write_text(_bundle_yaml(pem=_FAKE_PEM_ROTATED))

    result = runner.invoke(
        app,
        [
            "import-bundle", str(rotated),
            "--name", "laptop-rc",
            "--update-ca-only",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Refreshed external CA" in result.output

    # CA changed.
    assert b"rotated-test-cert" in ca.read_bytes()

    # api-key untouched.
    assert api_key.read_text() == original_key

    # bootstrap.yaml untouched — the audit trail still reflects the
    # original full import, not the CA refresh. (A future case may
    # want to append CA-refresh events; not part of Phase 1.)
    assert bootstrap.read_text() == original_bootstrap


def test_update_ca_only_works_without_prior_install(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Even if no prior import has happened, --update-ca-only should
    create the dir and write the CA. The verb's a primitive — it
    doesn't presume the install state exists yet."""
    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(_bundle_yaml())

    result = runner.invoke(
        app,
        [
            "import-bundle", str(bundle_file),
            "--name", "fresh-laptop",
            "--update-ca-only",
        ],
    )
    assert result.exit_code == 0

    ca = isolated_home / ".wip-deploy" / "fresh-laptop" / "secrets" / "external-ca.crt"
    assert ca.exists()
    api_key = isolated_home / ".wip-deploy" / "fresh-laptop" / "secrets" / "api-key"
    assert not api_key.exists()


# ──────────────────────────────────────────────────────────────────────
# stdin


def test_import_bundle_from_stdin(isolated_home: Path) -> None:
    result = runner.invoke(
        app,
        ["import-bundle", "-", "--name", "laptop-rc"],
        input=_bundle_yaml(),
    )
    assert result.exit_code == 0, result.output
    api_key = isolated_home / ".wip-deploy" / "laptop-rc" / "secrets" / "api-key"
    assert api_key.read_text() == "opaque-secret-value\n"


# ──────────────────────────────────────────────────────────────────────
# Parse errors surface as friendly CLI output


def test_invalid_bundle_yaml_exits_with_friendly_error(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text("{this is not valid yaml: at: all")

    result = runner.invoke(
        app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"]
    )
    assert result.exit_code == 2
    assert "bundle invalid" in result.output


def test_missing_permissions_field_surfaces_caveat(
    isolated_home: Path, tmp_path: Path
) -> None:
    """The CLI's exit message names the field path so operators know
    where to look."""
    body: dict = yaml.safe_load(_bundle_yaml())
    del body["spec"]["api_key"]["scope"]["permissions"]
    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(yaml.safe_dump(body, sort_keys=False))

    result = runner.invoke(
        app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"]
    )
    assert result.exit_code == 2
    assert "permissions" in result.output
    assert "spec.api_key.scope.permissions" in result.output


def test_bundle_file_not_found(isolated_home: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["import-bundle", str(tmp_path / "nope.yaml"), "--name", "laptop-rc"],
    )
    assert result.exit_code == 2
    assert "not found" in result.output.lower()


# ──────────────────────────────────────────────────────────────────────
# Secret file permissions


@pytest.mark.skipif(
    not hasattr(Path, "chmod"),
    reason="POSIX-style chmod required",
)
def test_secret_files_are_0600(isolated_home: Path, tmp_path: Path) -> None:
    import stat

    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(_bundle_yaml())

    runner.invoke(app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"])

    secrets_dir = isolated_home / ".wip-deploy" / "laptop-rc" / "secrets"
    for path in [secrets_dir / "api-key", secrets_dir / "external-ca.crt"]:
        mode = stat.S_IMODE(path.stat().st_mode)
        # On macOS / Linux this is exact; on Windows we'd skip via the
        # marker on this test (chmod is a no-op there).
        assert mode == 0o600, f"{path.name} has mode 0o{mode:o}, expected 0o600"
