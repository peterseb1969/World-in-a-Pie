"""Renderer base types.

A renderer is a pure function: `(deployment, components, apps, configs,
secrets) → FileTree`. The FileTree is the thing `apply` then writes to
disk and hands to the platform (podman-compose, kubectl, tilt).

Kept deliberately tiny — real renderer logic lives in target-specific
modules (compose.py, k8s.py, dev.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileEntry:
    """One file in a rendered tree.

    `mode` is an octal permission bits mask. Default is 0o644. Files
    containing secrets get 0o600 so a curious ls on the install dir
    can't leak them to other users.
    """

    content: str
    mode: int = 0o644


@dataclass
class FileTree:
    """A set of rendered files keyed by relative path.

    Paths are relative to the install directory. `apply` materializes
    them there.
    """

    files: dict[Path, FileEntry] = field(default_factory=dict)

    def add(self, path: str | Path, content: str, *, mode: int = 0o644) -> None:
        self.files[Path(path)] = FileEntry(content=content, mode=mode)

    def paths(self) -> list[Path]:
        return sorted(self.files.keys())

    def write(self, root: Path) -> None:
        """Materialize the tree under `root`. Creates parent directories
        as needed. Overwrites existing files.
        """
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for rel, entry in self.files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.content)
            # Best-effort chmod — Windows / unusual FS may reject it.
            import contextlib

            with contextlib.suppress(NotImplementedError, OSError):
                target.chmod(entry.mode)
