"""Renderer base types.

A renderer is a pure function: `(deployment, components, apps, configs,
secrets) → FileTree`. The FileTree is the thing `apply` then writes to
disk and hands to the platform (podman-compose, kubectl).

Kept deliberately tiny — real renderer logic lives in target-specific
modules (compose.py, k8s.py, dev.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileEntry:
    """One file in a rendered tree.

    `mode` is an octal permission bits mask. Default is 0o644.

    Other host users are kept out by the tree's root directory, which
    `FileTree.write` makes 0o700, not by file modes. A file bind-mounted
    into a container that runs as a non-root user must stay 0o644: on
    Linux, under both rootless podman and a root daemon, that user is
    neither the file's owner nor in its group, so 0o600/0o640 make the
    file unreadable inside the container. 0o600 is only for files no
    non-root container reads (`.env`, which compose reads on the host;
    Garage's config, whose container runs as root).
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

        `root` is made 0o700 on every write, existing roots included: the
        tree holds secrets, and the directory is what keeps other host
        users out while bind-mounted files stay readable to the containers
        (a bind mount hands over the file itself, so a container never
        traverses its host parents).
        """
        import contextlib

        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        # Best-effort chmod — Windows / unusual FS may reject it.
        with contextlib.suppress(NotImplementedError, OSError):
            root.chmod(0o700)
        for rel, entry in self.files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.content)
            with contextlib.suppress(NotImplementedError, OSError):
                target.chmod(entry.mode)
