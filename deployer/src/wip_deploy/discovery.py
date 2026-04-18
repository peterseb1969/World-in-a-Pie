"""Manifest discovery.

Walks the repo for `wip-component.yaml` and `wip-app.yaml` files, parses
each into its typed Pydantic model, and returns a `Discovery` bundle.
Collects all errors rather than failing on the first one — a single
broken manifest shouldn't hide others.

Search paths:
  - `components/*/wip-component.yaml` — backend services + infrastructure
  - `apps/*/wip-app.yaml`             — app manifests
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


class DiscoveryError(Exception):
    """One manifest that couldn't be loaded (YAML parse or spec validation)."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


@dataclass
class Discovery:
    """Result of a discovery pass."""

    components: list[Component] = field(default_factory=list)
    apps: list[App] = field(default_factory=list)
    errors: list[DiscoveryError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ────────────────────────────────────────────────────────────────────


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: cwd) looking for a .git directory.

    Raises FileNotFoundError if no .git directory is found.
    """
    p = (start or Path.cwd()).resolve()
    for ancestor in [p, *p.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    raise FileNotFoundError(f"no .git directory found above {p}")


def discover(repo_root: Path) -> Discovery:
    """Walk the repo for manifests. Never raises; errors are collected
    on `Discovery.errors`."""
    result = Discovery()

    for path in sorted(repo_root.glob("components/*/wip-component.yaml")):
        component = _load_component(path)
        if isinstance(component, DiscoveryError):
            result.errors.append(component)
        else:
            result.components.append(component)

    for path in sorted(repo_root.glob("apps/*/wip-app.yaml")):
        app = _load_app(path)
        if isinstance(app, DiscoveryError):
            result.errors.append(app)
        else:
            result.apps.append(app)

    return result


# ────────────────────────────────────────────────────────────────────


def _load_component(path: Path) -> Component | DiscoveryError:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return DiscoveryError(path, f"YAML parse error: {e}")
    except OSError as e:
        return DiscoveryError(path, f"read error: {e}")

    if not isinstance(data, dict):
        return DiscoveryError(path, "manifest must be a YAML mapping")

    try:
        return Component.model_validate(data)
    except Exception as e:
        return DiscoveryError(path, f"spec validation: {e}")


def _load_app(path: Path) -> App | DiscoveryError:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return DiscoveryError(path, f"YAML parse error: {e}")
    except OSError as e:
        return DiscoveryError(path, f"read error: {e}")

    if not isinstance(data, dict):
        return DiscoveryError(path, "manifest must be a YAML mapping")

    try:
        return App.model_validate(data)
    except Exception as e:
        return DiscoveryError(path, f"spec validation: {e}")
