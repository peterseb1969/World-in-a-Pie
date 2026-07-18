"""File-based secret backend — one file per secret in a directory.

Directory layout:

    ~/.wip-deploy/<deployment-name>/secrets/
      ├── api-key              (0600)
      ├── mongo-password
      ├── dex-password-admin
      ├── dex-client-wip-gateway
      └── ...

The directory is created lazily on `persist()` with mode 0700; each
file is written with mode 0600. Values are stored as-is (no JSON
wrapping, no trailing newline stripping beyond the final one) so they
round-trip byte-identical.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path


class FileSecretBackend:
    """Persists secrets as individual files in a directory.

    The cache is essential for correctness: if the same secret is
    requested twice before `persist()`, both calls must return the
    same value. Without the cache the second call would generate a
    new random value.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, str] = {}
        self._dirty: set[str] = set()

    # ────────────────────────────────────────────────────────────────

    def get_or_generate(self, name: str, generator: Callable[[], str]) -> str:
        _validate_name(name)

        if name in self._cache:
            return self._cache[name]

        path = self._path(name)
        if path.exists():
            value = path.read_text()
            # Trailing newline is a common editor artifact; strip exactly one.
            if value.endswith("\n"):
                value = value[:-1]
            self._cache[name] = value
            return value

        value = generator()
        self._cache[name] = value
        self._dirty.add(name)
        return value

    def persist(self) -> None:
        """Write any newly-generated values to disk. Idempotent — calling
        persist() again with no new secrets is a no-op."""
        if not self._dirty:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        # Best-effort chmod — on Windows these calls are no-ops, which
        # is correct since Windows has its own ACL model.
        with contextlib.suppress(NotImplementedError, OSError):
            self.directory.chmod(0o700)
        for name in self._dirty:
            path = self._path(name)
            path.write_text(self._cache[name])
            with contextlib.suppress(NotImplementedError, OSError):
                path.chmod(0o600)
        self._dirty.clear()

    def list_names(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(p.name for p in self.directory.iterdir() if p.is_file())

    def remove(self, name: str) -> None:
        _validate_name(name)
        path = self._path(name)
        if path.exists():
            path.unlink()
        self._cache.pop(name, None)
        self._dirty.discard(name)

    # ────────────────────────────────────────────────────────────────

    def _path(self, name: str) -> Path:
        return self.directory / name


def _validate_name(name: str) -> None:
    """Reject names that would escape the secrets directory or do weird
    filesystem things. Secret names come from manifests under the same
    control boundary, but we still defend against `../` and similar."""
    if not name:
        raise ValueError("secret name must not be empty")
    if "/" in name or "\\" in name or name.startswith(".") or "\0" in name:
        raise ValueError(f"invalid secret name: {name!r}")
