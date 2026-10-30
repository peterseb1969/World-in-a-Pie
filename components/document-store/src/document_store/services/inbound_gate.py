"""The inbound gate, applied while a write is validated.

A namespace whose `inbound_references` is `closed` (the default) accepts a
new reference only from a writer who can read it. A refused reference must
reach the writer exactly as a reference whose target does not exist: same
error code, message, details, and the same place among the write's other
errors. Anything less lets a writer test whether an id or a value exists in
a namespace it cannot read.

So the gate is consulted by validation itself, at the point each resolver
has found its target: a refused target is treated as not found, and the
resolver's own not-found branch reports it. Refusing after validation, as a
separate step, could not be made indistinguishable: a write carrying another
invalid field would report the missing reference only when it is missing.

A reference the document's previous version already held is not checked
(`carried`), so revoking read never breaks an existing document. A create
does not know its previous version until validation has computed the
identity, so writes validate in two passes (document_service): first with a
recording gate, which only notes refusals; then, if any refusal is not
carried, again with an enforcing gate.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from wip_auth import UserIdentity
from wip_auth.identity import get_identity_string
from wip_auth.reference_policy import (
    WIP_NAMESPACE,
    PermissionUnavailableError,
    caller_can_read,
    inbound_open,
)

from .reference_validator import RegistryUnavailableError, get_reference_validator
from .registry_client import RegistryError, get_registry_client

logger = logging.getLogger(__name__)


class InboundGate:
    """One write's view of which reference targets it may not name.

    `enforce=False` records refusals without refusing (the first pass of a
    create); `enforce=True` refuses them. `refused` holds the ids refused, in
    either mode.
    """

    def __init__(
        self,
        source_namespace: str,
        *,
        carried: frozenset[str] = frozenset(),
        enforce: bool = True,
        identity: UserIdentity | None = None,
    ) -> None:
        self.source_namespace = source_namespace
        self.carried = carried
        self.enforce = enforce
        self.identity = identity
        self.refused: set[str] = set()
        self._placed: dict[str, str | None] = {}

    async def refuses(self, target_namespace: str | None, entity_id: str | None) -> bool:
        """Should the target be treated as not found?"""
        if not target_namespace or target_namespace in (self.source_namespace, WIP_NAMESPACE):
            return False
        if entity_id and entity_id in self.carried:
            return False
        config = await get_reference_validator()._get_namespace(target_namespace)
        if inbound_open(target_namespace, config):
            return False
        try:
            if await caller_can_read(target_namespace, self.identity):
                return False
        except PermissionUnavailableError as e:
            raise RegistryUnavailableError(str(e)) from e
        self.refused.add(entity_id or f"namespace:{target_namespace}")
        if self.enforce:
            # The writer learns only that the target was not found; the
            # operator log says which closed namespace refused whom.
            logger.warning(
                "Inbound reference refused: %s writing in '%s' cannot read '%s'",
                get_identity_string(), self.source_namespace, target_namespace,
            )
        return self.enforce

    async def refuses_entry(self, entity_id: str | None) -> bool:
        """`refuses` for a target known only by its Registry entry id."""
        if not entity_id:
            return False
        return await self.refuses(await self.namespace_of(entity_id), entity_id)

    async def namespace_of(self, entity_id: str) -> str | None:
        """The namespace an active Registry entry lives in, or None."""
        if entity_id not in self._placed:
            try:
                placed = await get_registry_client().lookup_entry_namespaces(
                    [entity_id], raise_on_error=True,
                )
            except RegistryError as e:
                raise RegistryUnavailableError(
                    f"Cannot check the namespace of a reference: {e}"
                ) from e
            self._placed[entity_id] = placed.get(entity_id)
        return self._placed[entity_id]


_current: contextvars.ContextVar[InboundGate | None] = contextvars.ContextVar(
    "wip_inbound_gate", default=None,
)


def current_gate() -> InboundGate | None:
    return _current.get()


def use_gate(gate: InboundGate | None) -> contextvars.Token[InboundGate | None]:
    return _current.set(gate)


def reset_gate(token: contextvars.Token[InboundGate | None]) -> None:
    _current.reset(token)


async def gate_refuses(target_namespace: str | None, entity_id: str | None) -> bool:
    gate = current_gate()
    return gate is not None and await gate.refuses(target_namespace, entity_id)


async def gate_refuses_entry(entity_id: str | None) -> bool:
    gate = current_gate()
    return gate is not None and await gate.refuses_entry(entity_id)


def carried_ids(previous: Any) -> frozenset[str]:
    """What the previous version already referenced, its template included."""
    from .reference_validator import _referenced_ids

    if previous is None:
        return frozenset()
    template_id = (
        previous.get("template_id") if isinstance(previous, dict)
        else getattr(previous, "template_id", None)
    )
    ids = _referenced_ids(previous)
    if template_id:
        ids.add(template_id)
    return frozenset(ids)
