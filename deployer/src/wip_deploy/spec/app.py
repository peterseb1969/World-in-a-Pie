"""App manifest model.

An App is a Component with additional UI-facing metadata (route prefix,
display name). Loaded from `apps/<name>/wip-app.yaml`.

Apps replace the `docker-compose.app.*.yml` label-scraping mechanism from
v1's `setup-wip.sh`. All app configuration is structured data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wip_deploy.spec._base import WIPModel
from wip_deploy.spec.component import ComponentMetadata, ComponentSpec


class AppMetadata(WIPModel):
    display_name: str = Field(min_length=1)
    route_prefix: str = Field(pattern=r"^/.*")
    ui_only: bool = True


class App(WIPModel):
    """Top-level app manifest."""

    api_version: Literal["wip.dev/v1"] = "wip.dev/v1"
    kind: Literal["App"] = "App"
    metadata: ComponentMetadata
    spec: ComponentSpec
    app_metadata: AppMetadata

    @model_validator(mode="after")
    def nats_roles_only(self) -> App:
        """An app's NATS access is a role, never raw subjects: an app
        manifest must not be able to grant itself stream administration or
        another service's subjects."""
        nc = self.spec.nats_client
        if nc is not None and nc.raw:
            raise ValueError(
                f"app {self.metadata.name!r}: nats_client must declare `roles` "
                "(ingest-producer, event-subscriber, replay-subscriber), not raw subjects"
            )
        return self
