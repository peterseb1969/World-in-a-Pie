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

# NATS roles that see every namespace's messages (ingest-producer only
# publishes, nats-monitor reads listings, not contents).
_ALL_NAMESPACE_NATS_ROLES = ("event-subscriber", "replay-subscriber")


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
        mc = self.spec.mongo_client
        if mc is not None and mc.raw:
            raise ValueError(
                f"app {self.metadata.name!r}: mongo_client must declare `roles` "
                "(read-all), not databases — apps never write to MongoDB"
            )
        # These roles read every namespace: NATS subjects carry no namespace
        # to scope a subscription by, and MongoDB databases are per service.
        # Only an app that holds the master key may have them.
        if not self.spec.privileged_key:
            unscoped = [
                *(r for r in (nc.roles if nc else []) if r in _ALL_NAMESPACE_NATS_ROLES),
                *(f"mongo {r}" for r in (mc.roles if mc else [])),
            ]
            if unscoped:
                raise ValueError(
                    f"app {self.metadata.name!r}: {', '.join(unscoped)} reads every "
                    "namespace, so only a privileged_key app may declare it. An app "
                    "reads its own data through the WIP API and reporting-sync"
                )
        return self

    @model_validator(mode="after")
    def own_key_unless_privileged(self) -> App:
        """Every app gets a key of its own, scoped to its namespaces, and the
        deployer sets WIP_API_KEY from it. Only an app that declares
        `privileged_key: true` (an admin console) may receive the install's
        master key (`api-key`)."""
        name = self.metadata.name
        env = [*self.spec.env.required, *self.spec.env.optional]
        if self.spec.privileged_key:
            if self.spec.namespaces or self.spec.namespace_access:
                raise ValueError(
                    f"app {name!r}: a privileged_key app holds the master key; "
                    "namespaces / namespace_access would scope a key it never gets"
                )
            return self
        master = [e.name for e in env if e.source.from_secret == "api-key"]
        if master:
            raise ValueError(
                f"app {name!r}: {', '.join(master)} reads the install's master key "
                "(from_secret: api-key). Apps get a key of their own, scoped by "
                "spec.namespaces and spec.namespace_access; only an app declaring "
                "privileged_key: true may hold the master key"
            )
        if any(e.name == "WIP_API_KEY" for e in env):
            raise ValueError(
                f"app {name!r}: WIP_API_KEY is set by the deployer from the app's "
                "own key; remove it from env"
            )
        owned = {n.name for n in self.spec.namespaces}
        accessed = [a.namespace for a in self.spec.namespace_access]
        both = sorted(owned & set(accessed))
        if both:
            raise ValueError(
                f"app {name!r}: {', '.join(both)} is both owned (namespaces) and "
                "listed in namespace_access; an owned namespace already gets admin"
            )
        if len(accessed) != len(set(accessed)):
            raise ValueError(f"app {name!r}: namespace_access lists a namespace twice")
        return self
