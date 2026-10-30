"""Namespace reservations: the operator-approved namespaces of an install.

The deployer renders each app's declared namespaces and their settings into a
JSON file beside the config-file API keys (WIP_NAMESPACE_RESERVATIONS_FILE).
A reservation does two things at the namespace upsert:

- It lets a key that is not a platform admin create its app's namespace: the
  key must hold `admin` on the prefix, and every setting it sends must equal
  the reservation's. So an app can trigger its own bootstrap any number of
  times without being able to choose settings the operator did not approve.
- It supplies the settings a create leaves out, for every caller. A bootstrap
  that sends no settings therefore creates the namespace exactly as approved,
  never at platform defaults.

A reservation never retunes a namespace that exists: changing one later
changes nothing until an operator applies the new settings with an admin key.

The file is read on every upsert. Upserts are rare and the file is small, so
a changed reservation applies at once, with no reload interval or restart.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("registry.reservations")

RESERVATIONS_FILE_ENV = "WIP_NAMESPACE_RESERVATIONS_FILE"

# What a namespace create uses for a setting that neither the request nor a
# reservation states: the upsert's documented defaults.
SETTING_DEFAULTS: dict[str, Any] = {
    "description": "",
    "isolation_mode": "open",
    "allowed_external_refs": [],
    "id_config": {},
    "deletion_mode": "retain",
}
SETTING_FIELDS = tuple(SETTING_DEFAULTS)


class ReservationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    isolation_mode: Literal["open", "strict"] | None = None
    allowed_external_refs: list[str] | None = None
    id_config: dict[str, Any] | None = None
    deletion_mode: Literal["retain", "full"] | None = None


class Reservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(min_length=1)
    app: str = Field(min_length=1)
    """The app that owns the namespace (for the operator and error messages)."""
    settings: ReservationSettings = Field(default_factory=ReservationSettings)

    def effective_settings(self) -> dict[str, Any]:
        """Every setting a create applies: the declared ones over the defaults."""
        return {**SETTING_DEFAULTS, **self.settings.model_dump(exclude_none=True)}


class _ReservationsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservations: list[Reservation] = Field(default_factory=list)


class ReservationsUnavailable(Exception):
    """The configured reservations file exists but cannot be used."""


def load_reservations() -> dict[str, Reservation]:
    """The install's reservations by prefix; empty when none are configured.

    A configured but absent file means no reservations (the deployer renders
    one on every install, so absence is a hand-run Registry). A file that is
    present and does not parse raises ReservationsUnavailable: guessing would
    either refuse an app its own namespace or create one at settings nobody
    approved.
    """
    path = os.getenv(RESERVATIONS_FILE_ENV)
    if not path:
        return {}
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        logger.warning("%s=%s does not exist; no namespace reservations", RESERVATIONS_FILE_ENV, path)
        return {}
    except OSError as e:
        raise ReservationsUnavailable(f"cannot read {path}: {e}") from e
    if not raw.strip():
        return {}
    try:
        parsed = _ReservationsFile.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as e:
        raise ReservationsUnavailable(f"{path} does not parse: {e}") from e

    by_prefix: dict[str, Reservation] = {}
    for r in parsed.reservations:
        if r.prefix == "wip":
            raise ReservationsUnavailable(f"{path}: the 'wip' namespace cannot be reserved")
        if r.prefix in by_prefix:
            raise ReservationsUnavailable(
                f"{path}: namespace {r.prefix!r} is reserved twice "
                f"(apps {by_prefix[r.prefix].app!r} and {r.app!r})"
            )
        by_prefix[r.prefix] = r
    return by_prefix
