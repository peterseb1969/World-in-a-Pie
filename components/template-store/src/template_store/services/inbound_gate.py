"""The inbound gate, applied while a template's references are resolved.

A namespace whose `inbound_references` is `closed` (the default) accepts a
new reference only from a writer who can read it. A refused reference must
reach the writer exactly as a reference that does not resolve: same error
code, message, details, and the same place among the write's other
failures. Anything less lets a writer test whether a template or a
terminology exists in a namespace it cannot read.

So the gate is consulted by the resolvers themselves, at the point each has
found its target: a refused target is dropped from what resolved, and the
resolver's own not-found branch reports it. Refusing after resolution, as a
separate step, cannot be made indistinguishable: a template naming one
refused and one missing reference would report differently from one naming
two missing references.

A reference the template's previous version already held is not checked
(`carried`), so revoking read never breaks an existing template. Unlike a
document create, every template write knows its previous version before it
resolves anything, so one enforcing pass is enough.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from wip_auth import UserIdentity
from wip_auth.identity import get_identity_string
from wip_auth.reference_policy import (
    WIP_NAMESPACE,
    PermissionUnavailableError,
    caller_can_read,
    inbound_open,
)

from .errors import TemplateError
from .reference_validator import RegistryUnavailableError, get_reference_validator
from .registry_client import RegistryError, get_registry_client

logger = logging.getLogger(__name__)


class InboundGate:
    """One template write's view of which reference targets it may not name."""

    def __init__(
        self,
        source_namespace: str,
        *,
        carried: frozenset[str] = frozenset(),
        identity: UserIdentity | None = None,
    ) -> None:
        self.source_namespace = source_namespace
        self.carried = carried
        self.identity = identity
        self._placed: dict[str, str | None] = {}

    async def refuses(self, target_namespace: str | None, entity_id: str | None) -> bool:
        """Should the target be treated as not found?"""
        if not target_namespace or target_namespace in (self.source_namespace, WIP_NAMESPACE):
            return False
        if entity_id and entity_id in self.carried:
            return False
        # When the target's settings or the writer's permission cannot be
        # read, the write is refused rather than let through unchecked.
        try:
            config = await get_reference_validator()._get_namespace(target_namespace)
            if inbound_open(target_namespace, config):
                return False
            if await caller_can_read(target_namespace, self.identity):
                return False
        except (RegistryUnavailableError, PermissionUnavailableError) as e:
            raise TemplateError(str(e), "registry_error", rule="unreachable") from e
        # The writer learns only that the target was not found; the operator
        # log says which closed namespace refused whom.
        logger.warning(
            "Inbound reference refused: %s writing a template in '%s' cannot read '%s'",
            get_identity_string(), self.source_namespace, target_namespace,
        )
        return True

    async def refuses_entry(self, entity_id: str | None) -> bool:
        """`refuses` for a target known only by its Registry entry id."""
        if not entity_id or entity_id in self.carried:
            return False
        await self._place([entity_id])
        return await self.refuses(self._placed.get(entity_id), entity_id)

    async def visible(self, resolved: dict[str, str]) -> dict[str, str]:
        """``resolved`` (raw identifier -> canonical id) without refused targets."""
        await self._place([i for i in resolved.values() if i not in self.carried])
        return {
            raw: entity_id for raw, entity_id in resolved.items()
            if not await self.refuses(self._placed.get(entity_id), entity_id)
        }

    async def _place(self, entity_ids: list[str]) -> None:
        """Learn the namespace of each active Registry entry, in one lookup."""
        pending = sorted({i for i in entity_ids if i not in self._placed})
        if not pending:
            return
        try:
            placed = await get_registry_client().lookup_entry_namespaces(
                pending, raise_on_error=True,
            )
        except (RegistryError, httpx.HTTPError) as e:
            raise TemplateError(
                f"Cannot check the namespaces of the template's references: {e}",
                "registry_error", rule="unreachable",
            ) from e
        for entity_id in pending:
            self._placed[entity_id] = placed.get(entity_id)


def carried_ids(previous: Any) -> frozenset[str]:
    """Every id the template's previous version referenced (none for a create)."""
    if previous is None:
        return frozenset()
    from .template_service import TemplateService

    return frozenset(TemplateService._stored_reference_ids(previous))
