"""Named preset fragments.

Each preset is a partial DeploymentSpec (dict form) that provides
sensible defaults for `modules`, `auth`, `apps`, and `images`. The CLI
fills in `target`, `network`, `platform`, and `secrets` from user flags,
then merges the preset on top.

Presets are Python modules (not YAML) so they can share defaults, compose
with each other, and be type-checked.
"""

from typing import Any

from wip_deploy.presets.analytics import ANALYTICS
from wip_deploy.presets.core import CORE
from wip_deploy.presets.full import FULL
from wip_deploy.presets.headless import HEADLESS
from wip_deploy.presets.standard import STANDARD

PRESETS: dict[str, dict[str, Any]] = {
    "headless": HEADLESS,
    "core": CORE,
    "standard": STANDARD,
    "analytics": ANALYTICS,
    "full": FULL,
}


def get_preset(name: str) -> dict[str, Any]:
    """Return the preset dict by name. Raises KeyError with a helpful
    message if unknown."""
    try:
        return PRESETS[name]
    except KeyError as e:
        raise KeyError(
            f"unknown preset {name!r}; available: {sorted(PRESETS)}"
        ) from e


__all__ = ["PRESETS", "get_preset", "HEADLESS", "CORE", "STANDARD", "ANALYTICS", "FULL"]
