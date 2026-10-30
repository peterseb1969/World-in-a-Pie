"""Who may reference an entity in another namespace.

A cross-namespace reference passes two gates, each set by one side:

- **Outbound** — the referencing (source) namespace's ``isolation_mode``.
  ``open`` may reference any namespace; ``strict`` only itself and its
  ``allowed_external_refs``.
- **Inbound** — the referenced (target) namespace's ``inbound_references``.
  ``open`` accepts a reference from any writer; ``closed`` (the default)
  only from a writer who can read it. ``wip`` is always open: it holds the
  shared vocabularies every namespace references.

Both stores' reference validators call :func:`check_reference`, so the rule
lives in one place. Callers apply it to the references that are new in a
write; a reference carried over unchanged from the previous version is not
re-checked, so revoking read never breaks an existing document.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from .identity import get_current_identity
from .models import UserIdentity
from .permissions import (
    PERMISSION_UNAVAILABLE,
    permission_sufficient,
    resolve_permission,
)

WIP_NAMESPACE = "wip"

# Groups whose keys act for the platform and are not limited by namespace.
_PRIVILEGED_GROUPS = ("wip-admins", "wip-services")

Verdict = Literal["ok", "outbound", "inbound"]


class PermissionUnavailableError(Exception):
    """The caller's permission on the target namespace could not be read.

    The check then refuses rather than passes: an inbound gate that opens
    whenever the Registry is slow is not a gate.
    """


def outbound_allows(source_ns: str, source_config: dict[str, Any], target_ns: str) -> bool:
    """Does the source namespace's own setting let it reference ``target_ns``?"""
    if target_ns == source_ns:
        return True
    if target_ns in (source_config.get("allowed_external_refs") or []):
        return True
    return bool(source_config.get("isolation_mode", "open") != "strict")


def inbound_open(target_ns: str, target_config: dict[str, Any]) -> bool:
    """Does the target namespace accept references from any writer?

    A namespace whose settings predate the field reads as closed, the
    default every namespace has.
    """
    if target_ns == WIP_NAMESPACE:
        return True
    return bool(target_config.get("inbound_references", "closed") == "open")


async def caller_can_read(target_ns: str, identity: UserIdentity | None = None) -> bool:
    """Can the writing caller read ``target_ns``?

    With no identity in the request context nothing vouches for the write,
    so the answer is no.
    """
    identity = identity if identity is not None else get_current_identity()
    if identity is None:
        return False
    if identity.has_any_group(list(_PRIVILEGED_GROUPS)):
        return True
    permission = await resolve_permission(identity, target_ns)
    if permission == PERMISSION_UNAVAILABLE:
        raise PermissionUnavailableError(
            f"Cannot read the caller's permission on namespace '{target_ns}'"
        )
    return permission_sufficient(permission, "read")


async def check_reference(
    source_ns: str,
    source_config: dict[str, Any],
    target_ns: str,
    target_config: Callable[[str], Awaitable[dict[str, Any]]],
    identity: UserIdentity | None = None,
) -> Verdict:
    """Apply both gates to one new reference from ``source_ns`` to ``target_ns``.

    ``target_config`` loads a namespace's settings; it is called only when
    the inbound gate needs them, so a reference the outbound gate refuses,
    or one into ``wip``, costs no lookup.

    Returns ``"ok"``, or the gate that refused it. An inbound refusal must
    reach the caller as "not found": a closed namespace does not confirm
    that its entities exist to someone who cannot read it.
    """
    if target_ns == source_ns:
        return "ok"
    if not outbound_allows(source_ns, source_config, target_ns):
        return "outbound"
    if target_ns == WIP_NAMESPACE or inbound_open(target_ns, await target_config(target_ns)):
        return "ok"
    if await caller_can_read(target_ns, identity):
        return "ok"
    return "inbound"
