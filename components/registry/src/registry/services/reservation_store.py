"""Reservations from both sources: the deployer's file and the Registry's API.

A prefix is reserved by one source or the other, never both. A runtime
reservation of a prefix the file reserves is refused when it is made. The
reverse (a later apply renders a file reservation of a prefix that already
has a runtime one) is refused by the deployer when it can reach the Registry;
when it could not, the collision reaches the Registry, which refuses to pick
a winner: the namespace upsert answers 409 for that prefix, the listing shows
the conflict, and the collision is logged CRITICAL until an operator removes
one of the two.
"""

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models.id_algorithm import validate_id_config_write
from ..models.namespace import WIP_NAMESPACE, Namespace, prefix_error
from ..models.reservation import RuntimeReservation
from .id_prefixes import IdPrefixError, assert_prefixes_free, check_prefix_rule, claimed_prefixes
from .reservations import Reservation, ReservationSettings, load_reservations

logger = logging.getLogger("registry.reservations")


class ReservationRefused(Exception):
    """A reservation request the Registry refuses; carries the HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ReservationConflict(Exception):
    """A prefix both the install's file and a runtime reservation reserve."""

    def __init__(self, prefix: str, file_app: str, runtime_app: str) -> None:
        self.prefix = prefix
        self.file_app = file_app
        self.runtime_app = runtime_app
        super().__init__(
            f"namespace '{prefix}' is reserved both by the install's reservations "
            f"(app '{file_app}') and by runtime API key '{runtime_app}'; remove one "
            f"of the two (wip-deploy for the app, DELETE "
            f"/api/registry/reservations/{prefix} for the runtime one)"
        )


@dataclass
class ReservationView:
    prefix: str
    app: str
    source: str  # "file" or "runtime"
    settings: dict[str, Any]
    namespace_exists: bool
    conflict: str | None


def _as_reservation(doc: RuntimeReservation) -> Reservation:
    return Reservation(
        prefix=doc.prefix, app=doc.app, settings=ReservationSettings(**doc.settings)
    )


async def find_reservation(prefix: str) -> Reservation | None:
    """The reservation of `prefix` from either source, or None.

    Raises ReservationsUnavailable for an unusable file and ReservationConflict
    when both sources reserve the prefix.
    """
    from_file = load_reservations().get(prefix)
    runtime = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if from_file is not None and runtime is not None:
        conflict = ReservationConflict(prefix, from_file.app, runtime.app)
        logger.critical("%s", conflict)
        raise conflict
    if runtime is not None:
        return _as_reservation(runtime)
    return from_file


async def list_reservations() -> list[ReservationView]:
    """Every reservation from both sources, sorted by prefix, conflicts marked."""
    from_file = load_reservations()
    runtime = {d.prefix: d for d in await RuntimeReservation.find_all().to_list()}
    prefixes = sorted(set(from_file) | set(runtime))
    existing = {
        ns.prefix
        for ns in await Namespace.find({"prefix": {"$in": prefixes}}).to_list()
    }
    views: list[ReservationView] = []
    for prefix in prefixes:
        f, r = from_file.get(prefix), runtime.get(prefix)
        conflict = str(ReservationConflict(prefix, f.app, r.app)) if f and r else None
        for source, res in (("file", f), ("runtime", _as_reservation(r) if r else None)):
            if res is None:
                continue
            views.append(ReservationView(
                prefix=prefix,
                app=res.app,
                source=source,
                settings=res.effective_settings(),
                namespace_exists=prefix in existing,
                conflict=conflict,
            ))
    return views


async def check_new_reservation(prefix: str, settings: ReservationSettings, owner: str) -> None:
    """Refuse (ReservationRefused) a runtime reservation that cannot be made.

    Re-reserving a prefix the same key already holds is allowed (its settings
    are replaced).
    """
    if prefix == WIP_NAMESPACE:
        raise ReservationRefused(400, "the 'wip' namespace cannot be reserved")
    if error := prefix_error(prefix):
        raise ReservationRefused(400, error)
    from_file = load_reservations()
    if prefix in from_file:
        raise ReservationRefused(
            409,
            f"namespace '{prefix}' is reserved by app '{from_file[prefix].app}' in the "
            "install's reservations; wip-deploy manages that app's namespaces and key",
        )
    held = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
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
    # (of either source) may claim one this reservation claims.
    others: list[tuple[str, str, list[str]]] = [
        (p, f"app '{r.app}'", claimed_prefixes(r.effective_settings()["id_config"]))
        for p, r in from_file.items()
    ]
    for doc in await RuntimeReservation.find(RuntimeReservation.prefix != prefix).to_list():
        res = _as_reservation(doc)
        others.append(
            (doc.prefix, f"runtime key '{doc.app}'", claimed_prefixes(res.effective_settings()["id_config"]))
        )
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
    if doc is None:
        doc = RuntimeReservation(prefix=prefix, app=owner, settings=stored, created_by=actor)
        await doc.insert()
    else:
        doc.settings = stored
        await doc.save()
    return doc


async def release(prefix: str) -> str | None:
    """Release the runtime reservation of `prefix` unless its namespace exists.

    Returns "released", "kept" (the namespace exists: the reservation stays
    for an operator to release explicitly), or None (no runtime reservation).
    """
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    if doc is None:
        return None
    if await Namespace.find_one(Namespace.prefix == prefix) is not None:
        return "kept"
    await doc.delete()
    return "released"


async def reservations_of(owner: str) -> list[RuntimeReservation]:
    return await RuntimeReservation.find(RuntimeReservation.app == owner).to_list()
