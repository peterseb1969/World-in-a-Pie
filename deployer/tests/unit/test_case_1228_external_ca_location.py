"""CASE-1228 item 4 — the external CA is probed, written and mounted from one place.

Three parties had to agree on where `external-ca.crt` sits, and they agreed only
by coincidence:

  * `install` probed `<install-dir>/secrets/external-ca.crt`.
  * The compose and dev renderers emitted `./secrets/external-ca.crt:...:ro`,
    install-dir-relative because apply runs compose with the install dir as cwd.
  * `import-bundle` wrote the CA into `_default_install_dir(name)/secrets`,
    ignoring where the install actually lives — it took no install-dir at all.

So `install --install-dir /opt/wip` (or `--secrets-location /srv/wip-secrets`)
put the store somewhere the probe and the mount did not follow: the CA the
operator imported was never found, the flag stayed off, and the app containers
got no cross-host trust — silently, since a missing file looks like "no bundle".

All three now derive the path from `spec.secrets.location`, the field that names
the store: `external_ca_host_path()` for the renderers and the install/render
probes, and `_install_secrets_location()` — the rule `install` itself uses — for
import-bundle's destination. import-bundle gained `--install-dir` so an install
materialized outside `~/.wip-deploy/<name>/` can be named at import time, and
passes it through to the install command it suggests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

import wip_deploy.cli as cli
from wip_deploy.cli import _DEPLOYMENT_JSON_VERSION, app
from wip_deploy.config_gen import ResolvedEnv
from wip_deploy.renderers.compose import _service_block, external_ca_host_path
from wip_deploy.spec.app import App, AppMetadata
from wip_deploy.spec.component import ComponentMetadata, ComponentSpec, ImageRef
from wip_deploy.spec.deployment import (
    ApplySpec,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    ModulesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

runner = CliRunner()

_FAKE_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCCATigAwIBAgIQ...fake-test-cert...\n"
    "-----END CERTIFICATE-----\n"
)

STATE = "deployment.deployer-state"
DATA_DIR = "/tmp/wip-test-data"


def _bundle_yaml(name: str = "test-bundle") -> str:
    return yaml.safe_dump(
        {
            "api_version": "wip.dev/v1",
            "kind": "BootstrapBundle",
            "metadata": {"name": name, "generated_at": datetime.now(UTC).isoformat()},
            "spec": {
                "external_base_url": "https://wip.example/",
                "api_key": {
                    "value": "opaque-secret-value",
                    "name": name,
                    "scope": {"namespaces": ["kb"], "permissions": "read"},
                    "expires_at": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
                },
                "ca_cert": _FAKE_PEM,
                "suggested_apps": [{"name": "react-console"}],
            },
        },
        sort_keys=False,
    )


def _deployment(*, location: str | None, mount: bool = False) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="ca"),
        spec=DeploymentSpec(
            target="compose",
            apps=[],
            modules=ModulesSpec(optional=[]),
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(
                hostname="wip.localhost", tls="internal", external_ca_mount=mount,
            ),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path(DATA_DIR))),
            secrets=SecretsSpec(backend="file", location=location),
            apply=ApplySpec(),
        ),
    )


def _app_owner() -> App:
    return App(
        metadata=ComponentMetadata(
            name="test-app", category="optional", description="test app",
        ),
        spec=ComponentSpec(image=ImageRef(name="test-app", tag="latest")),
        app_metadata=AppMetadata(display_name="Test", label="test"),
    )


def _service_volumes(deployment: Deployment) -> list[str]:
    block = _service_block(
        _app_owner(),
        deployment,
        ResolvedEnv(required={}, optional={}),
        is_app=True,
        healthcheck_owners=set(),
        active_names=set(),
    )
    return block.get("volumes") or []


def _seed_ca(store: Path) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    ca = store / "external-ca.crt"
    ca.write_text(_FAKE_PEM)
    return ca


def _persist_state(install_dir: Path, deployment: Deployment) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
        "deployment": deployment.model_dump(mode="json"),
    }
    (install_dir / STATE).write_text(json.dumps(payload, indent=2, default=str) + "\n")


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep `Path.home()` off the real home directory (shared pattern, CASE-360)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


# ────────────────────────────────────────────────────────────────────
# The one rule that names the path


class TestExternalCaHostPath:
    def test_absolute_from_the_store_location(self, tmp_path: Path) -> None:
        store = tmp_path / "srv" / "wip-secrets"
        assert external_ca_host_path(_deployment(location=str(store))) == str(
            store / "external-ca.crt"
        )

    def test_none_when_the_spec_declares_no_store(self) -> None:
        # No file store means there is nothing to mount; the callers must skip
        # the mount rather than guess a directory that holds no CA.
        assert external_ca_host_path(_deployment(location=None)) is None


# ────────────────────────────────────────────────────────────────────
# Renderers


class TestComposeMountFollowsTheStore:
    def test_mounts_from_the_store_not_the_install_dir(self, tmp_path: Path) -> None:
        store = tmp_path / "srv" / "wip-secrets"
        volumes = _service_volumes(_deployment(location=str(store), mount=True))
        assert f"{store / 'external-ca.crt'}:/etc/ssl/certs/external-ca.crt:ro" in volumes
        assert not any(v.startswith("./secrets/") for v in volumes)

    def test_no_phantom_mount_when_the_store_is_unknown(self) -> None:
        # Flag set with no location could only come from a hand-written spec:
        # emit no CA volume rather than one pointing at a directory nobody
        # writes, which the container runtime would then fail on or shadow.
        volumes = _service_volumes(_deployment(location=None, mount=True))
        assert not any("external-ca.crt" in v for v in volumes)


# ────────────────────────────────────────────────────────────────────
# install's probe


class _Stop(Exception):
    """Ends the walk once the rendered deployment has been observed."""


def _install_capture(
    monkeypatch: pytest.MonkeyPatch, *extra: str,
) -> Deployment:
    """Run `install` far enough to see the spec it was about to render.

    Preflight and render are stood down: both are irrelevant to which directory
    the probe read, and neither can run against a tmp install dir.
    """
    captured: dict[str, Deployment] = {}

    def fake_render_tree(deployment: Deployment, *_a: Any, **_k: Any) -> Any:
        captured["deployment"] = deployment
        raise _Stop

    monkeypatch.setattr(cli, "_run_preflight_or_exit", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_render_tree", fake_render_tree)
    with pytest.raises(_Stop):
        runner.invoke(
            app,
            [
                "install",
                "--name", "ca",
                "--target", "compose",
                "--hostname", "wip.localhost",
                "--auth-mode", "api-key-only",
                "--no-auth-gateway",
                "--data-dir", DATA_DIR,
                # compose targets need a registry to resolve short image names;
                # nothing here pulls, the run stops at the render stub.
                "--registry", "ghcr.io/test",
                *extra,
            ],
            catch_exceptions=False,
        )
    return captured["deployment"]


class TestInstallProbesTheStore:
    def test_ca_in_a_store_outside_the_install_dir_is_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "opt" / "wip"
        store = tmp_path / "srv" / "wip-secrets"
        _seed_ca(store)

        deployment = _install_capture(
            monkeypatch,
            "--install-dir", str(install_dir),
            "--secrets-location", str(store),
        )
        assert deployment.spec.network.external_ca_mount is True

    def test_a_stray_ca_under_the_install_dir_is_not_the_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The coincidence, inverted: a CA sitting in `<install-dir>/secrets`
        while the store lives elsewhere must not arm the mount — the renderers
        would mount from the store, and the file behind that path is absent."""
        install_dir = tmp_path / "opt" / "wip"
        store = tmp_path / "srv" / "wip-secrets"
        _seed_ca(install_dir / "secrets")

        deployment = _install_capture(
            monkeypatch,
            "--install-dir", str(install_dir),
            "--secrets-location", str(store),
        )
        assert deployment.spec.network.external_ca_mount is False

    def test_default_layout_still_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dir = tmp_path / "opt" / "wip"
        _seed_ca(install_dir / "secrets")

        deployment = _install_capture(monkeypatch, "--install-dir", str(install_dir))
        assert deployment.spec.secrets.location == str(install_dir / "secrets")
        assert deployment.spec.network.external_ca_mount is True


# ────────────────────────────────────────────────────────────────────
# import-bundle's destination


class TestImportBundleDestination:
    def test_install_dir_keeps_its_own_secrets(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        bundle = tmp_path / "bundle.yaml"
        bundle.write_text(_bundle_yaml())
        install_dir = tmp_path / "opt" / "wip"

        result = runner.invoke(
            app,
            [
                "import-bundle", str(bundle),
                "--name", "laptop-rc",
                "--install-dir", str(install_dir),
            ],
        )
        assert result.exit_code == 0, result.output

        assert (install_dir / "secrets" / "external-ca.crt").read_text() == _FAKE_PEM
        assert (install_dir / "secrets" / "api-key").read_text() == "opaque-secret-value\n"
        assert (install_dir / "bootstrap.yaml").exists()
        # Nothing leaked into the name-derived location the verb used to use.
        assert not (isolated_home / ".wip-deploy" / "laptop-rc").exists()

    def test_existing_install_keeps_the_location_its_spec_records(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        install_dir = tmp_path / "opt" / "wip"
        store = tmp_path / "srv" / "wip-secrets"
        _persist_state(
            install_dir, _deployment(location=str(store)),
        )
        bundle = tmp_path / "bundle.yaml"
        bundle.write_text(_bundle_yaml())

        result = runner.invoke(
            app,
            [
                "import-bundle", str(bundle),
                "--name", "ca",
                "--install-dir", str(install_dir),
                "--update-ca-only",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (store / "external-ca.crt").read_text() == _FAKE_PEM
        assert not (install_dir / "secrets" / "external-ca.crt").exists()

    def test_default_name_derived_location_unchanged(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        bundle = tmp_path / "bundle.yaml"
        bundle.write_text(_bundle_yaml())

        result = runner.invoke(
            app, ["import-bundle", str(bundle), "--name", "laptop-rc"],
        )
        assert result.exit_code == 0, result.output
        default = isolated_home / ".wip-deploy" / "laptop-rc"
        assert (default / "secrets" / "external-ca.crt").read_text() == _FAKE_PEM
        # A default install gets the default suggestion back — no invented flag.
        assert "--install-dir" not in result.output

    def test_suggested_install_command_carries_the_install_dir(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        bundle = tmp_path / "bundle.yaml"
        bundle.write_text(_bundle_yaml())
        install_dir = tmp_path / "opt" / "wip"

        result = runner.invoke(
            app,
            [
                "import-bundle", str(bundle),
                "--name", "laptop-rc",
                "--install-dir", str(install_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        # Following the printed suggestion must re-create the same pairing; an
        # install elsewhere would probe a store this import did not fill.
        assert f"--install-dir {install_dir}" in result.output

    def test_import_then_install_renders_the_mount(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The end-to-end the item was filed over: import a bundle for an
        install dir outside the default, then install that dir — the app
        containers come out with the CA mounted from the store."""
        install_dir = tmp_path / "opt" / "wip"
        bundle = tmp_path / "bundle.yaml"
        bundle.write_text(_bundle_yaml())
        runner.invoke(
            app,
            [
                "import-bundle", str(bundle),
                "--name", "ca",
                "--install-dir", str(install_dir),
            ],
        )

        deployment = _install_capture(monkeypatch, "--install-dir", str(install_dir))
        assert deployment.spec.network.external_ca_mount is True
        volumes = _service_volumes(deployment)
        assert (
            f"{install_dir / 'secrets' / 'external-ca.crt'}"
            ":/etc/ssl/certs/external-ca.crt:ro"
        ) in volumes
