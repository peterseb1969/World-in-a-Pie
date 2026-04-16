"""Tests for FileSecretBackend — the persistence layer."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wip_deploy.secrets_backend import FileSecretBackend

# ────────────────────────────────────────────────────────────────────
# Caching behavior (correctness, not perf)
# ────────────────────────────────────────────────────────────────────


class TestCaching:
    def test_same_name_returns_same_value_before_persist(
        self, tmp_path: Path
    ) -> None:
        """Critical invariant: repeated get_or_generate for the same name
        returns the first generated value, not a fresh one."""
        backend = FileSecretBackend(tmp_path / "secrets")

        counter = iter(range(100))
        gen = lambda: f"v{next(counter)}"  # noqa: E731 — compact for test

        first = backend.get_or_generate("api-key", gen)
        second = backend.get_or_generate("api-key", gen)
        assert first == second == "v0"

    def test_different_names_generate_independently(
        self, tmp_path: Path
    ) -> None:
        backend = FileSecretBackend(tmp_path / "secrets")
        counter = iter(range(100))
        gen = lambda: f"v{next(counter)}"  # noqa: E731

        a = backend.get_or_generate("a", gen)
        b = backend.get_or_generate("b", gen)
        assert a != b


# ────────────────────────────────────────────────────────────────────
# Persistence & re-load
# ────────────────────────────────────────────────────────────────────


class TestPersistence:
    def test_persist_creates_directory_with_restrictive_mode(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "deeply" / "nested" / "secrets"
        backend = FileSecretBackend(target)
        backend.get_or_generate("api-key", lambda: "value")
        backend.persist()

        assert target.exists()
        if os.name == "posix":
            mode = stat.S_IMODE(target.stat().st_mode)
            assert mode == 0o700

    def test_files_have_0600_permissions(self, tmp_path: Path) -> None:
        backend = FileSecretBackend(tmp_path / "secrets")
        backend.get_or_generate("api-key", lambda: "secret-value")
        backend.persist()

        if os.name == "posix":
            f = tmp_path / "secrets" / "api-key"
            mode = stat.S_IMODE(f.stat().st_mode)
            assert mode == 0o600

    def test_reinstall_reads_existing_value(self, tmp_path: Path) -> None:
        """The lifecycle invariant: a re-install picks up the previous
        secret rather than generating a new one."""
        dir_ = tmp_path / "secrets"

        # First install: generate + persist.
        b1 = FileSecretBackend(dir_)
        b1.get_or_generate("api-key", lambda: "first-value")
        b1.persist()

        # Second install: a fresh backend sees the existing file and
        # returns the existing value even if given a different generator.
        b2 = FileSecretBackend(dir_)
        value = b2.get_or_generate(
            "api-key", lambda: "would-be-wrong"
        )
        assert value == "first-value"

    def test_persist_is_idempotent_on_no_changes(self, tmp_path: Path) -> None:
        dir_ = tmp_path / "secrets"
        b1 = FileSecretBackend(dir_)
        b1.get_or_generate("api-key", lambda: "v")
        b1.persist()
        mtime_before = (dir_ / "api-key").stat().st_mtime

        # Second persist without getting anything new must not rewrite.
        b1.persist()
        mtime_after = (dir_ / "api-key").stat().st_mtime
        assert mtime_before == mtime_after

    def test_persist_only_writes_new_values(self, tmp_path: Path) -> None:
        """If the backend reads an existing file, persist() must not
        rewrite it — preserves mtime, avoids spurious filesystem
        churn on re-installs."""
        dir_ = tmp_path / "secrets"
        dir_.mkdir()
        existing = dir_ / "api-key"
        existing.write_text("existing-value")
        import time

        original_mtime = existing.stat().st_mtime
        time.sleep(0.01)

        b = FileSecretBackend(dir_)
        b.get_or_generate("api-key", lambda: "new-value")
        b.persist()

        assert existing.read_text() == "existing-value"
        assert existing.stat().st_mtime == original_mtime

    def test_trailing_newline_is_stripped_on_read(
        self, tmp_path: Path
    ) -> None:
        """Editors love trailing newlines. We round-trip without them."""
        dir_ = tmp_path / "secrets"
        dir_.mkdir()
        (dir_ / "api-key").write_text("value\n")

        b = FileSecretBackend(dir_)
        assert b.get_or_generate("api-key", lambda: "ignored") == "value"


# ────────────────────────────────────────────────────────────────────
# list_names / remove
# ────────────────────────────────────────────────────────────────────


class TestListAndRemove:
    def test_list_names_empty_when_absent(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "nosuch")
        assert b.list_names() == []

    def test_list_names_returns_sorted(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "s")
        for n in ["z", "a", "m"]:
            b.get_or_generate(n, lambda: "v")
        b.persist()
        assert b.list_names() == ["a", "m", "z"]

    def test_remove_drops_from_cache_and_disk(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "s")
        b.get_or_generate("k", lambda: "v")
        b.persist()
        assert (tmp_path / "s" / "k").exists()

        b.remove("k")
        assert not (tmp_path / "s" / "k").exists()
        # Subsequent get_or_generate re-generates.
        again = b.get_or_generate("k", lambda: "newval")
        assert again == "newval"

    def test_remove_nonexistent_is_noop(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "s")
        b.remove("never-existed")  # must not raise


# ────────────────────────────────────────────────────────────────────
# Name validation
# ────────────────────────────────────────────────────────────────────


class TestNameValidation:
    def test_slash_rejected(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "s")
        with pytest.raises(ValueError, match="invalid secret name"):
            b.get_or_generate("evil/path", lambda: "v")

    def test_dotdot_rejected_via_leading_dot(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "s")
        with pytest.raises(ValueError, match="invalid secret name"):
            b.get_or_generate("..etc-passwd", lambda: "v")

    def test_empty_rejected(self, tmp_path: Path) -> None:
        b = FileSecretBackend(tmp_path / "s")
        with pytest.raises(ValueError, match="must not be empty"):
            b.get_or_generate("", lambda: "v")
