"""Namespace reservations made through the Registry's API.

The deployer's file reserves the namespaces of the apps it deploys. A runtime
reservation does the same for an app it does not deploy (an external app, or
someone developing against WIP): a platform admin creates a runtime API key
that reserves its namespaces, with the settings the admin fixes. The key gets
`admin` on each, so its bootstrap can create the namespace as reserved. The
reservation's owner is that key, by name.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar

from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class RuntimeReservation(Document):
    prefix: str = Field(..., description="The reserved namespace")
    app: str = Field(..., description="The runtime API key that owns the reservation")
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="The declared settings (ReservationSettings, unset fields omitted)",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = Field(default="system")

    class Settings:
        name = "namespace_reservations"
        indexes: ClassVar[list] = [
            IndexModel([("prefix", 1)], unique=True, name="reservation_prefix_unique"),
            IndexModel([("app", 1)], name="reservation_app"),
        ]
