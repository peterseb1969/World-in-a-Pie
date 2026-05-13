"""Regression coverage for CASE-356 — dir-based app source registration.

Three surfaces under test:

  1. **`app_registry.py` module** — pure-function read/write/delete of
     the `~/.wip-deploy/apps/<name>.yaml` directory. Tests use a
     temp directory passed explicitly (the module accepts a
     `directory` override for exactly this purpose).
  2. **CLI verbs** — `wip-deploy register-app <name> --path <path>`
     and `wip-deploy unregister-app <name>`. Smoke-level: argument
     parsing, error paths, idempotency.
  3. **`_assemble` integration** — registered paths back-fill into
     `app_sources` for enabled apps; CLI `--app-source` shadows with
     a yellow warning.

The module-under-test uses `Path.home() / ".wip-deploy" / "apps"`
when no directory is passed. We avoid touching the real home
directory by passing explicit dirs in unit tests and monkeypatching
`Path.home` for the CLI integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.app_registry import (
    AppRegistryError,
    read_registry,
    register_app,
    registry_dir,
    unregister_app,
)
from wip_deploy.cli import app

runner = CliRunner()


# ────────────────────────────────────────────────────────────────────
# app_registry module — pure-function layer
# ────────────────────────────────────────────────────────────────────


class TestReadRegistry:
    def test_empty_directory_returns_empty_dict(self, tmp_path: Path) -> None:
        assert read_registry(tmp_path) == {}

    def test_missing_directory_returns_empty_dict(self, tmp_path: Path) -> None:
        """A registry directory that doesn't exist is not an error —
        operators who haven't run register-app yet just see an empty map."""
        assert read_registry(tmp_path / "never-created") == {}

    def test_reads_valid_entry(self, tmp_path: Path) -> None:
        (tmp_path / "react-console.yaml").write_text(
            yaml.safe_dump({"name": "react-console", "local_path": "/opt/rc"})
        )
        out = read_registry(tmp_path)
        assert out == {"react-console": Path("/opt/rc")}

    def test_reads_multiple_entries(self, tmp_path: Path) -> None:
        (tmp_path / "react-console.yaml").write_text(
            yaml.safe_dump({"name": "react-console", "local_path": "/opt/rc"})
        )
        (tmp_path / "kb.yaml").write_text(
            yaml.safe_dump({"name": "kb", "local_path": "/opt/kb"})
        )
        out = read_registry(tmp_path)
        assert out == {
            "react-console": Path("/opt/rc"),
            "kb": Path("/opt/kb"),
        }

    def test_skips_malformed_yaml(self, tmp_path: Path) -> None:
        """Corrupt entries are skipped silently — registry is
        operator-managed; one bad file shouldn't fail the install."""
        (tmp_path / "good.yaml").write_text(
            yaml.safe_dump({"name": "good", "local_path": "/opt/good"})
        )
        (tmp_path / "bad.yaml").write_text("not: valid: yaml: at all:")
        out = read_registry(tmp_path)
        assert "good" in out
        assert "bad" not in out

    def test_skips_entries_with_missing_keys(self, tmp_path: Path) -> None:
        (tmp_path / "no-name.yaml").write_text(
            yaml.safe_dump({"local_path": "/opt/x"})
        )
        (tmp_path / "no-path.yaml").write_text(yaml.safe_dump({"name": "x"}))
        assert read_registry(tmp_path) == {}

    def test_skips_relative_paths(self, tmp_path: Path) -> None:
        """Relative paths are ambiguous across cwd; refuse them."""
        (tmp_path / "relative.yaml").write_text(
            yaml.safe_dump({"name": "x", "local_path": "../some-relative"})
        )
        assert read_registry(tmp_path) == {}

    def test_skips_invalid_name_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "weird.yaml").write_text(
            yaml.safe_dump({"name": "WEIRD_NAME", "local_path": "/opt/x"})
        )
        assert read_registry(tmp_path) == {}


# ────────────────────────────────────────────────────────────────────
# register_app / unregister_app
# ────────────────────────────────────────────────────────────────────


class TestRegisterApp:
    def test_writes_entry_file(self, tmp_path: Path) -> None:
        reg = tmp_path / "registry"
        src = tmp_path / "rc-checkout"
        src.mkdir()
        entry_file = register_app("react-console", src, directory=reg)
        assert entry_file == reg / "react-console.yaml"
        data = yaml.safe_load(entry_file.read_text())
        assert data["name"] == "react-console"
        assert data["local_path"] == str(src.resolve())

    def test_creates_registry_dir_if_missing(self, tmp_path: Path) -> None:
        """First register-app on a fresh machine creates the directory."""
        reg = tmp_path / "fresh-registry"
        src = tmp_path / "rc-checkout"
        src.mkdir()
        register_app("react-console", src, directory=reg)
        assert reg.is_dir()

    def test_overwrites_existing_entry(self, tmp_path: Path) -> None:
        """Idempotent: re-registration updates the path."""
        reg = tmp_path / "registry"
        src1 = tmp_path / "rc-v1"
        src1.mkdir()
        src2 = tmp_path / "rc-v2"
        src2.mkdir()

        register_app("react-console", src1, directory=reg)
        register_app("react-console", src2, directory=reg)

        out = read_registry(reg)
        assert out["react-console"] == src2.resolve()

    def test_rejects_invalid_name(self, tmp_path: Path) -> None:
        src = tmp_path / "x"
        src.mkdir()
        with pytest.raises(AppRegistryError, match="must match"):
            register_app("BadName", src, directory=tmp_path / "registry")

    def test_rejects_nonexistent_path(self, tmp_path: Path) -> None:
        bogus = tmp_path / "does-not-exist"
        with pytest.raises(AppRegistryError, match="not a directory"):
            register_app("react-console", bogus, directory=tmp_path / "registry")

    def test_rejects_path_pointing_at_file(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "a-file"
        not_a_dir.write_text("not a directory")
        with pytest.raises(AppRegistryError, match="not a directory"):
            register_app("react-console", not_a_dir, directory=tmp_path / "registry")


class TestUnregisterApp:
    def test_removes_existing_entry(self, tmp_path: Path) -> None:
        reg = tmp_path / "registry"
        src = tmp_path / "rc"
        src.mkdir()
        register_app("react-console", src, directory=reg)
        assert unregister_app("react-console", directory=reg) is True
        assert read_registry(reg) == {}

    def test_silent_on_missing_entry(self, tmp_path: Path) -> None:
        """Idempotent: unregistering an already-absent app returns False."""
        assert unregister_app("never-registered", directory=tmp_path) is False

    def test_silent_on_missing_directory(self, tmp_path: Path) -> None:
        assert (
            unregister_app("anything", directory=tmp_path / "never-created")
            is False
        )


class TestRegistryDir:
    def test_returns_home_relative_path(self) -> None:
        """The default registry dir is operator-machine-local."""
        d = registry_dir()
        assert d.parent.parent == Path.home()
        assert d.parts[-2:] == (".wip-deploy", "apps")


# ────────────────────────────────────────────────────────────────────
# CLI verbs
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `Path.home()` to a temp directory so register-app /
    unregister-app don't touch the real `~/.wip-deploy/apps/`.

    Returns the fake home — tests can inspect what landed under it.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


class TestRegisterAppCLI:
    def test_register_writes_entry(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "rc-checkout"
        src.mkdir()
        result = runner.invoke(
            app,
            ["register-app", "react-console", "--path", str(src)],
        )
        assert result.exit_code == 0, result.output
        entry = isolated_home / ".wip-deploy" / "apps" / "react-console.yaml"
        assert entry.is_file()
        data = yaml.safe_load(entry.read_text())
        assert data["name"] == "react-console"

    def test_register_bad_name_exits_2(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        result = runner.invoke(
            app,
            ["register-app", "BadName", "--path", str(src)],
        )
        assert result.exit_code == 2
        assert "must match" in result.output

    def test_register_bad_path_exits_2(self, isolated_home: Path) -> None:
        result = runner.invoke(
            app,
            ["register-app", "react-console", "--path", "/does/not/exist"],
        )
        assert result.exit_code == 2
        assert "not a directory" in result.output


class TestUnregisterAppCLI:
    def test_unregister_removes_entry(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "rc"
        src.mkdir()
        runner.invoke(
            app,
            ["register-app", "react-console", "--path", str(src)],
        )
        entry = isolated_home / ".wip-deploy" / "apps" / "react-console.yaml"
        assert entry.is_file()  # baseline

        result = runner.invoke(app, ["unregister-app", "react-console"])
        assert result.exit_code == 0
        assert not entry.exists()
        assert "Unregistered" in result.output

    def test_unregister_missing_is_friendly_noop(
        self, isolated_home: Path
    ) -> None:
        result = runner.invoke(app, ["unregister-app", "never-registered"])
        assert result.exit_code == 0
        assert "was not registered" in result.output


# ────────────────────────────────────────────────────────────────────
# _assemble integration — registry back-fills into app_sources
# ────────────────────────────────────────────────────────────────────


class TestAssembleIntegration:
    """`_assemble` is the central plumbing point. After CASE-356, the
    registry is read and merged into `app_sources` before the
    Deployment is built. We verify that via `show-spec --format json`
    which dumps the assembled Deployment.
    """

    def test_registered_app_is_picked_up_when_enabled(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """Register react-console, then run a show-spec with --app
        react-console (no --app-source): the resulting spec carries
        the registered path in DevPlatform.app_sources."""
        src = tmp_path / "rc-checkout"
        src.mkdir()
        runner.invoke(
            app,
            ["register-app", "react-console", "--path", str(src)],
        )

        result = runner.invoke(
            app,
            [
                "show-spec",
                "--target", "dev",
                "--hostname", "localhost",
                "--app", "react-console",
                "--name", "test-356",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0, result.output
        # The dev platform's app_sources should contain the registered path.
        assert str(src.resolve()) in result.output
        assert '"react-console"' in result.output

    def test_registered_app_not_enabled_is_ignored(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """Register react-console, but don't enable it in the install —
        the registry entry should NOT leak into the spec. Registry
        holds machine state for all cloned apps; installs enable a
        subset."""
        src = tmp_path / "rc-checkout"
        src.mkdir()
        runner.invoke(
            app,
            ["register-app", "react-console", "--path", str(src)],
        )

        result = runner.invoke(
            app,
            [
                "show-spec",
                "--target", "dev",
                "--hostname", "localhost",
                # NO --app react-console
                "--name", "test-356-not-enabled",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0
        # The registered path should NOT appear — the app isn't enabled.
        assert str(src.resolve()) not in result.output

    def test_cli_app_source_shadows_registered_path(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """CLI --app-source wins per invocation. When both a registered
        path and a CLI override exist for the same app, the CLI path
        lands in app_sources AND a warning is emitted on stderr."""
        registered = tmp_path / "rc-registered"
        registered.mkdir()
        override = tmp_path / "rc-override"
        override.mkdir()

        runner.invoke(
            app,
            ["register-app", "react-console", "--path", str(registered)],
        )

        result = runner.invoke(
            app,
            [
                "show-spec",
                "--target", "dev",
                "--hostname", "localhost",
                "--app-source", f"react-console={override}",
                "--name", "test-356-shadow",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0
        # CLI override path wins.
        assert str(override.resolve()) in result.output
        # And the warning fires.
        assert "shadows registered path" in result.output

    def test_cli_app_source_matching_registered_path_is_silent(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """When CLI and registry agree on the path, no shadow warning."""
        path = tmp_path / "rc"
        path.mkdir()
        runner.invoke(
            app,
            ["register-app", "react-console", "--path", str(path)],
        )

        result = runner.invoke(
            app,
            [
                "show-spec",
                "--target", "dev",
                "--hostname", "localhost",
                "--app-source", f"react-console={path}",
                "--name", "test-356-same",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0
        assert "shadows registered path" not in result.output
