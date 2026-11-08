"""Reservations, held by the Registry: one record per prefix.

Two writers keep them: the install's reservations file, which the Registry
imports (key_import) into records managed by wip-deploy, and the Registry's
API, through a runtime API key. Every lookup first brings the import up to
date with the file, so an answer is never older than the file.

A runtime reservation of a prefix the install's file reserves is refused
when it is made (409, error_code managed_by_deployer). The reverse, a file
reservation of a prefix an API reservation already holds, is resolved by the
import: the file's reservation replaces it, logged CRITICAL. The key that
made it keeps its grants.
"""

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models.id_algorithm import validate_id_config_write
from ..models.namespace import WIP_NAMESPACE, Namespace, prefix_error
from ..models.reservation import RuntimeReservation
from .id_prefixes import IdPrefixError, assert_prefixes_free, check_prefix_rule, claimed_prefixes
from .managed import reservation_refusal
from .reservations import Reservation, ReservationSettings

logger = logging.getLogger("registry.reservations")


class ReservationRefused(Exception):
    """A reservation request the Registry refuses; carries the HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class ReservationView:
    prefix: str
    app: str
    source: str  # "file" (managed by wip-deploy) or "runtime" (the API)
    settings: dict[str, Any]
    namespace_exists: bool
    managed_by: str | None = None


def _as_reservation(doc: RuntimeReservation) -> Reservation:
    return Reservation(
        prefix=doc.prefix, app=doc.app, settings=ReservationSettings(**doc.settings)
    )


async def _current() -> None:
    """Bring the import up to date with the files before answering. Raises
    ReservationsUnavailable for a reservations file that is present and
    unusable: guessing would either refuse an app its own namespace or create
    one at settings nobody approved."""
    from .key_import import ensure_current

    await ensure_current()


async def find_reservation(prefix: str) -> Reservation | None:
    """The reservation of `prefix`, or None. Raises ReservationsUnavailable
    for an unusable reservations file."""
    await _current()
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    return _as_reservation(doc) if doc is not None else None


async def list_reservations() -> list[ReservationView]:
    """Every reservation, sorted by prefix."""
    await _current()
    docs = sorted(await RuntimeReservation.find_all().to_list(), key=lambda d: d.prefix)
    existing = {
        ns.prefix
        for ns in await Namespace.find({"prefix": {"$in": [d.prefix for d in docs]}}).to_list()
    }
    return [
        ReservationView(
            prefix=d.prefix,
            app=d.app,
            source="file" if d.managed_by is not None else "runtime",
            settings=_as_reservation(d).effective_settings(),
            namespace_exists=d.prefix in existing,
            managed_by=d.managed_by,
        )
        for d in docs
    ]


async def check_new_reservation(prefix: str, settings: ReservationSettings, owner: str) -> None:
    """Refuse (ReservationRefused) a runtime reservation that cannot be made.

    Re-reserving a prefix the same key already holds is allowed (its settings
    are replaced). A prefix the install's file reserves raises
    ManagedRecordRefused (409, managed_by_deployer).
    """
    if prefix == WIP_NAMESPACE:
        raise ReservationRefused(400, "the 'wip' namespace cannot be reserved")
    if error := prefix_error(prefix):
        raise ReservationRefused(400, error)
    await _current()
    held = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if held is not None and held.managed_by is not None:
        raise reservation_refusal(prefix, held.app)
    if held is not None and held.app != owner:
        raise ReservationRefused(
            409, f"namespace '{prefix}' is already reserved by runtime API key '{held.app}'"
        )

    effective = Reservation(prefix=prefix, app=owner, settings=settings).effective_settings()
    try:
        id_config = validate_id_config_write(effective["id_config"])
    except ValidationError as e:
        raise ReservationRefused(422, f"settings.id_config: {e.errors()}") from e
    try:
        id_prefixes = check_prefix_rule(id_config)
        await assert_prefixes_free(prefix, id_prefixes)
    except IdPrefixError as e:
        raise ReservationRefused(e.status_code, e.detail) from e

    # An ID prefix names one sequence on the install: no other reservation
    # (the install's or the API's) may claim one this reservation claims.
    others: list[tuple[str, str, list[str]]] = []
    for doc in await RuntimeReservation.find(RuntimeReservation.prefix != prefix).to_list():
        res = _as_reservation(doc)
        label = f"app '{doc.app}'" if doc.managed_by is not None else f"runtime key '{doc.app}'"
        others.append((doc.prefix, label, claimed_prefixes(res.effective_settings()["id_config"])))
    for other_prefix, owner_label, claimed in others:
        clash = sorted(set(id_prefixes) & set(claimed))
        if clash:
            raise ReservationRefused(
                409,
                f"id_config prefix {', '.join(repr(c) for c in clash)} is already reserved "
                f"for namespace '{other_prefix}' ({owner_label}); a prefix names one ID "
                "sequence on the install",
            )


async def save_reservation(
    prefix: str, settings: ReservationSettings, owner: str, actor: str
) -> RuntimeReservation:
    """Create or replace the runtime reservation of `prefix` (checked first)."""
    stored = settings.model_dump(exclude_none=True)
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if doc is not None and doc.managed_by is not None:
        raise reservation_refusal(prefix, doc.app)
    from .namespace_identity import reservation_namespace_id

    namespace_id = await reservation_namespace_id(prefix, owner, doc)
    if doc is None:
        doc = RuntimeReservation(
            prefix=prefix, namespace_id=namespace_id, app=owner, settings=stored,
            created_by=actor,
        )
        await doc.insert()
    else:
        doc.settings = stored
        doc.namespace_id = namespace_id
        await doc.save()
    return doc


async def release(prefix: str) -> str | None:
    """Release the runtime reservation of `prefix` unless its namespace exists.

    Returns "released", "kept" (the namespace exists: the reservation stays
    for an operator to release explicitly), or None (no runtime reservation).
    """
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if doc is None or doc.managed_by is not None:
        return None
    if await Namespace.find_one(Namespace.prefix == prefix) is not None:
        return "kept"
    await doc.delete()
    return "released"


async def reservations_of(owner: str) -> list[RuntimeReservation]:
    """The API reservations a runtime key owns. A managed reservation's `app`
    names an app, not a key, so it is never one of them."""
    return await RuntimeReservation.find(
        RuntimeReservation.app == owner, RuntimeReservation.managed_by == None  # noqa: E711
    ).to_list()
