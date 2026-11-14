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
from pymongo.errors import DuplicateKeyError

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


def reservation_id_prefixes(prefix: str, app: str, settings: dict[str, Any]) -> list[str]:
    """The ID prefixes a reservation with these stored settings claims: the
    field the unique index keeps to one reservation per prefix."""
    effective = Reservation(
        prefix=prefix, app=app, settings=ReservationSettings(**settings),
    ).effective_settings()
    return claimed_prefixes(effective["id_config"])


async def backfill_id_prefixes() -> list[str]:
    """Derive id_prefixes for every stored reservation whose field is
    missing or stale (records written before the field). Returns the
    prefixes of the reservations that cannot hold theirs because another
    reservation already does: they are logged, and left as they were."""
    clashing: list[str] = []
    for doc in await RuntimeReservation.find_all().to_list():
        try:
            wanted = reservation_id_prefixes(doc.prefix, doc.app, doc.settings)
        except (ValidationError, ValueError):
            continue  # unreadable settings: nothing to claim
        if doc.id_prefixes == wanted:
            continue
        try:
            await RuntimeReservation.get_motor_collection().update_one(
                {"_id": doc.id}, {"$set": {"id_prefixes": wanted}},
            )
        except DuplicateKeyError:
            clashing.append(doc.prefix)
            logger.critical(
                "Reservation of namespace '%s' claims an id_config prefix another "
                "reservation holds (%s); it was made before one prefix was kept to one "
                "reservation. Release one of them (DELETE /reservations/{prefix}).",
                doc.prefix, ", ".join(wanted),
            )
    return clashing


def is_id_prefix_clash(exc: DuplicateKeyError) -> bool:
    """Whether a duplicate-key error is the id_prefixes index (another
    reservation claims one of the prefixes), not the namespace's own."""
    from ..models.reservation import RESERVATION_ID_PREFIXES_INDEX

    details = exc.details or {}
    return RESERVATION_ID_PREFIXES_INDEX in str(details.get("errmsg", "")) or (
        "id_prefixes" in (details.get("keyPattern") or {})
    )


async def _refuse_id_prefix_clash(prefix: str, id_prefixes: list[str]) -> None:
    """Raise the refusal for a write the id_prefixes index refused: the
    deployer's own (managed_by_deployer) when the reservation holding the
    prefix is the install's, as the check before the write refuses it."""
    holder = await RuntimeReservation.find_one(
        {"id_prefixes": {"$in": id_prefixes}, "prefix": {"$ne": prefix}},
    )
    if holder is not None and holder.managed_by is not None:
        raise reservation_refusal(holder.prefix, holder.app)
    where = f" for namespace '{holder.prefix}'" if holder is not None else " for another namespace"
    raise ReservationRefused(
        409,
        f"an id_config prefix of namespace '{prefix}' is reserved{where} (written while "
        "this request ran); a prefix names one ID sequence on the install",
    )


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


def _refuse_holder(prefix: str, held: RuntimeReservation | None, owner: str) -> None:
    """Refuse a reservation of `prefix` by `owner` that `held` already holds
    for someone else: the install's file (ManagedRecordRefused) or another
    runtime key (ReservationRefused, 409)."""
    if held is not None and held.managed_by is not None:
        raise reservation_refusal(prefix, held.app)
    if held is not None and held.app != owner:
        raise ReservationRefused(
            409, f"namespace '{prefix}' is already reserved by runtime API key '{held.app}'"
        )


async def check_new_reservation(
    prefix: str,
    settings: ReservationSettings,
    owner: str,
    pending: list[tuple[str, list[str]]] | None = None,
) -> list[str]:
    """Refuse (ReservationRefused) a runtime reservation that cannot be made,
    and return the ID prefixes it claims.

    Re-reserving a prefix the same key already holds is allowed (its settings
    are replaced). A prefix the install's file reserves raises
    ManagedRecordRefused (409, managed_by_deployer). `pending` holds the
    (prefix, claimed ID prefixes) of reservations checked earlier in the same
    request and not yet written: an ID prefix one of them claims is refused
    here too.
    """
    if prefix == WIP_NAMESPACE:
        raise ReservationRefused(400, "the 'wip' namespace cannot be reserved")
    if error := prefix_error(prefix):
        raise ReservationRefused(400, error)
    await _current()
    _refuse_holder(prefix, await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix), owner)

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
    # (the install's, the API's, or one this request makes) may claim one
    # this reservation claims.
    others: list[tuple[str, str, list[str]]] = [
        (other, "this request", claimed) for other, claimed in pending or [] if other != prefix
    ]
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
    return id_prefixes


async def save_reservation(
    prefix: str, settings: ReservationSettings, owner: str, actor: str,
    *, create_only: bool = False,
) -> tuple[RuntimeReservation, bool]:
    """Create or replace `owner`'s runtime reservation of `prefix`; returns
    the record and whether this call created it.

    The write itself is conditional on the holder, so a check made earlier
    in the request is never acted on stale: a prefix the install's file took
    meanwhile raises ManagedRecordRefused, one another key took raises
    ReservationRefused (409), and another key's settings are never replaced.
    ``create_only`` (a key being created): a reservation already held under
    the key's name is refused (409), never replaced. It is another request's
    (a concurrent create of the same name) or a key of that name's before,
    and a refused create undoes only what it wrote.
    """
    stored = settings.model_dump(exclude_none=True)
    id_prefixes = reservation_id_prefixes(prefix, owner, stored)
    doc = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    _refuse_holder(prefix, doc, owner)
    if create_only and doc is not None:
        raise ReservationRefused(
            409, f"namespace '{prefix}' is already reserved under the name '{owner}'; "
            "a key being created reserves only namespaces nobody holds",
        )
    from .namespace_identity import reservation_namespace_id

    namespace_id = await reservation_namespace_id(prefix, owner, doc)
    if doc is None:
        doc = RuntimeReservation(
            prefix=prefix, namespace_id=namespace_id, app=owner, settings=stored,
            created_by=actor, id_prefixes=id_prefixes,
        )
        try:
            await doc.insert()
        except DuplicateKeyError as exc:
            if is_id_prefix_clash(exc):
                await _refuse_id_prefix_clash(prefix, id_prefixes)
            if create_only:
                raise ReservationRefused(
                    409, f"namespace '{prefix}' was reserved while this key was created",
                ) from None
            await _refuse_taken(prefix, owner)
        return doc, True
    try:
        result = await RuntimeReservation.get_motor_collection().update_one(
            {"prefix": prefix, "app": owner, "managed_by": None},
            {"$set": {"settings": stored, "namespace_id": namespace_id,
                      "id_prefixes": id_prefixes}},
        )
    except DuplicateKeyError as exc:
        if is_id_prefix_clash(exc):
            await _refuse_id_prefix_clash(prefix, id_prefixes)
        raise
    if result.matched_count == 0:
        await _refuse_taken(prefix, owner)
    doc.settings, doc.namespace_id, doc.id_prefixes = stored, namespace_id, id_prefixes
    return doc, False


async def _refuse_taken(prefix: str, owner: str) -> None:
    """The holder of `prefix` changed between the read and the write: refuse
    as the new holder dictates, or ask for a retry when it was released."""
    _refuse_holder(prefix, await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix), owner)
    raise ReservationRefused(409, f"the reservation of '{prefix}' changed while it was written; retry")


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
