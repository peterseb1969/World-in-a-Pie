"""Per-operator local-app-source registry (CASE-356).

Each app the operator has cloned locally registers itself in
`~/.wip-deploy/apps/<name>.yaml`:

    name: react-console
    local_path: /Users/peter/Development/WIP-ReactConsole

`wip-deploy install --target dev` consults this directory: for each
enabled app, the resolved source path comes from (priority order):

  1. CLI `--app-source NAME=PATH` (per-invocation override)
  2. `~/.wip-deploy/apps/<name>.yaml` (registered)
  3. Nothing — trips CASE-355's loud-fail at render time

Phase 1 scope: file-based registry, dev-mode hot-reload ergonomics
only. Phase 2 (apps as WIP documents via an App Manager) is out of
scope; the migration path is laid out in CASE-356's body.

Operator-managed state. Lives at `~/.wip-deploy/apps/` (peer to
`~/.wip-deploy/<install-name>/`). Per-machine, not per-install:
register once, every install on this machine discovers it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# ────────────────────────────────────────────────────────────────────


# Same name-validation pattern as `AppRef.name` (spec/deployment.py) —
# kebab-case starting with a letter. Keeps the registry filename in
# lockstep with what the deployer accepts as an app name.
_APP_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def registry_dir() -> Path:
    """Per-operator registry directory.

    Computed lazily so tests can override via env or monkeypatching
    `Path.home`. Idempotent: callers that need to write check / create.
    """
    return Path.home() / ".wip-deploy" / "apps"


class AppRegistryError(Exception):
    """Bad input to register-app / corrupt registry file."""


# ────────────────────────────────────────────────────────────────────
# Read
# ────────────────────────────────────────────────────────────────────


def read_registry(directory: Path | None = None) -> dict[str, Path]:
    """Discover all registered apps as a `{name: local_path}` mapping.

    Missing or unreadable files are skipped silently — the registry
    is operator-managed and a corrupt file shouldn't block an install.
    Empty directory or missing directory returns an empty dict.

    Returns absolute Paths even when the YAML stored relative ones
    (resolved against the operator's HOME for stability).
    """
    target = directory or registry_dir()
    if not target.is_dir():
        return {}

    out: dict[str, Path] = {}
    for f in sorted(target.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        local_path = data.get("local_path")
        if not isinstance(name, str) or not isinstance(local_path, str):
            continue
        if not _APP_NAME_RE.match(name):
            continue
        path = Path(local_path).expanduser()
        if not path.is_absolute():
            # Refuse relative paths — they're ambiguous across cwd.
            continue
        out[name] = path
    return out


# ────────────────────────────────────────────────────────────────────
# Write
# ────────────────────────────────────────────────────────────────────


def register_app(
    name: str,
    path: Path,
    *,
    directory: Path | None = None,
) -> Path:
    """Write a registry entry for `name` pointing at `path`.

    Validates the name pattern and path-is-directory before writing.
    Overwrites any existing entry for the same name (idempotent;
    repeat registrations are how operators update a moved checkout).

    Returns the path of the written registry file.
    """
    if not _APP_NAME_RE.match(name):
        raise AppRegistryError(
            f"app name {name!r} must match {_APP_NAME_RE.pattern!r} "
            f"(lowercase, starts with a letter, kebab-case)"
        )
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise AppRegistryError(
            f"path {resolved} is not a directory"
        )

    target = directory or registry_dir()
    target.mkdir(parents=True, exist_ok=True)
    entry_file = target / f"{name}.yaml"

    payload: dict[str, Any] = {
        "name": name,
        "local_path": str(resolved),
    }
    entry_file.write_text(yaml.safe_dump(payload, sort_keys=False))
    return entry_file


def unregister_app(
    name: str,
    *,
    directory: Path | None = None,
) -> bool:
    """Remove the registry entry for `name`. Returns True if it existed.

    Silent and idempotent when the entry doesn't exist — the operator
    can re-run `unregister-app NAME` without worrying about prior state.
    """
    target = directory or registry_dir()
    entry_file = target / f"{name}.yaml"
    if not entry_file.is_file():
        return False
    entry_file.unlink()
    return True
