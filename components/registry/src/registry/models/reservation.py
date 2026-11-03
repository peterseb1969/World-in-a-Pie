"""Namespace reservations, held by the Registry.

A reservation approves the settings a namespace is created with, and lets a
key that holds `admin` on the prefix create it without being a platform admin.
Two writers keep them here, one record per prefix:

- the install's reservations file, which the Registry imports
  (services/key_import.py): those records are `managed_by: "wip-deploy"`,
  `app` names the app, and wip-deploy owns them;
- the Registry's API: a platform admin creates a runtime API key that reserves
  its namespaces, for an app wip-deploy does not deploy. `app` names that key,
  the reservation's owner, and revoking the key releases it.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class RuntimeReservation(Document):
    prefix: str = Field(..., description="The reserved namespace")
    app: str = Field(
        ...,
        description="The owner: the runtime API key (API reservations) or the app (managed ones)",
    )
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="The declared settings (ReservationSettings, unset fields omitted)",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = Field(default="system")
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None,
        description="'wip-deploy': imported from the install's reservations file",
    )
    origin: Literal["file", "console", "restored"] = Field(
        default="console",
        description="Who wrote the reservation: the install's file, the API, or a restore",
    )

    class Settings:
        name = "namespace_reservations"
        indexes: ClassVar[list] = [
            IndexModel([("prefix", 1)], unique=True, name="reservation_prefix_unique"),
            IndexModel([("app", 1)], name="reservation_app"),
        ]
