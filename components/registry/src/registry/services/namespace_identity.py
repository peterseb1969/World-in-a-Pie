"""Namespace identity: an id per namespace, apart from its name.

A namespace's name says which namespace holds it now. Whether two namespaces
are the same one (the namespace a grant was made for and one created later
under the same name; the namespace an archive was taken from and the one a
restore creates) is answered by its `namespace_id`, a UUID7 the Registry
mints. The rules:

- **Reserve.** A reservation is for the namespace of its name while that
  exists, and takes its id. With no namespace, a runtime reservation (made
  by an API key) mints one and keeps it while its owner stays the same; a
  new owner reserves another namespace, so a new id. A reservation the
  install's file makes (managed by wip-deploy) holds no id while no
  namespace of its name exists: the install gives the name to its app,
  whichever namespace holds it, so there is no identity to keep apart —
  and a pinned id would refuse the one thing an operator does with an app's
  namespace, restoring it from another install or after deleting it.
- **Create.** A namespace takes its reservation's id, else a new one. A
  platform admin's ID-preserving restore or import carries the archived id
  instead: it is adopted unless a live namespace already holds it (restoring
  beside the original makes a copy, with a new id). Carried onto a name
  reserved here under another id it is refused: the reservation is for a
  different namespace. An install's reservation without an id takes the
  archived one.
- **Delete.** The id ends with the namespace. A runtime reservation that
  outlives it gets a new one, so creating the name again is a new namespace;
  an install's reservation goes back to holding none.
- **Orphaned grants** carry the id they were made for and are activated when
  a namespace with that id is created, whoever creates it.

Effective grants carry no id: a grant that is not orphaned belongs to the
namespace holding its name, and its id is read from there when needed
(`id_held_for`).
"""

from __future__ import annotations

import logging

from ..models.grant import NamespaceGrant
from ..models.id_algorithm import IdGenerator
from ..models.namespace import Namespace
from ..models.reservation import RuntimeReservation

logger = logging.getLogger(__name__)


class NamespaceIdentityConflict(Exception):
    """A create that carries an id onto a name reserved for another one."""

    status_code = 409

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def new_namespace_id() -> str:
    return IdGenerator.generate_uuid7()


async def reservation_namespace_id(
    prefix: str, owner: str, existing: RuntimeReservation | None, *, managed: bool = False,
) -> str | None:
    """The id a reservation of ``prefix`` by ``owner`` holds after a write.

    The namespace's own while it exists. Otherwise none for a reservation the
    install's file makes (``managed``); for a runtime one, its current id when
    the owner is unchanged, else a new one.
    """
    ns = await Namespace.find_one(Namespace.prefix == prefix)
    if ns is not None and ns.namespace_id:
        return ns.namespace_id
    if managed:
        return None
    if existing is not None and existing.app == owner and existing.namespace_id:
        return existing.namespace_id
    return new_namespace_id()


async def id_for_create(prefix: str, archived_id: str | None = None) -> tuple[str, str | None]:
    """The id a namespace created as ``prefix`` takes, and a warning or None.

    Raises NamespaceIdentityConflict when ``archived_id`` is carried onto a
    name reserved here under another id.
    """
    reservation = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    reserved_id = reservation.namespace_id if reservation is not None else None
    if archived_id is None:
        return (reserved_id or new_namespace_id()), None
    if reservation is not None and reserved_id is not None and reserved_id != archived_id:
        raise NamespaceIdentityConflict(_reserved_conflict(prefix, reservation, archived_id))
    holder = await Namespace.find_one(Namespace.namespace_id == archived_id)
    if holder is not None:
        return new_namespace_id(), (
            f"namespace id {archived_id} is held by namespace '{holder.prefix}' on this "
            f"install; '{prefix}' is created as a copy, with a new id, and the "
            "archive's orphaned grants stay orphaned"
        )
    return archived_id, None


def _reserved_conflict(prefix: str, reservation: RuntimeReservation, archived_id: str) -> str:
    """The refusal of an archived id carried onto a name reserved for another
    id: what holds the name, and the order of steps that avoids it."""
    if reservation.managed_by:
        holder = f"for app '{reservation.app}' (by wip-deploy)"
        release = (
            "That reservation is wip-deploy's and the API does not release it: "
            "remove the app with wip-deploy, restore, then add the app again"
        )
    else:
        holder = f"by API key '{reservation.app}'"
        release = (
            "To restore it now, a platform admin releases that reservation "
            f"(DELETE /api/registry/reservations/{prefix}) and the restore is "
            "run again; the key can reserve the name again afterwards"
        )
    return (
        f"Namespace '{prefix}' is reserved here {holder} for namespace id "
        f"{reservation.namespace_id}, not the archived namespace {archived_id}: "
        "the reservation is for a different namespace. Restore a namespace's "
        "data before deploying the apps or creating the keys that reserve its "
        f"name. {release}."
    )


async def adopt_on_reservation(prefix: str, namespace_id: str) -> None:
    """Give the reservation of ``prefix`` the id of the namespace just created."""
    await RuntimeReservation.get_motor_collection().update_one(
        {"prefix": prefix}, {"$set": {"namespace_id": namespace_id}},
    )


async def activate_orphans(ns: Namespace, actor: str) -> list[str]:
    """Make the orphaned grants made for ``ns`` effective; returns them as
    'namespace/subject_type:subject'. Orphans on its name made for another
    namespace stay orphaned."""
    if not ns.namespace_id:
        return []
    orphans = await NamespaceGrant.find({
        "namespace": ns.prefix, "orphaned": True, "namespace_id": ns.namespace_id,
    }).to_list()
    for grant in orphans:
        await grant.set({"orphaned": False, "orphaned_at": None, "namespace_id": None})
    labels = sorted(f"{g.namespace}/{g.subject_type}:{g.subject}" for g in orphans)
    if labels:
        logger.warning(
            "Namespace '%s' (id %s) created by %s: activated %d orphaned grant(s) "
            "made for it: %s", ns.prefix, ns.namespace_id, actor, len(labels), ", ".join(labels),
        )
        from .event_bus import access_changed

        await access_changed(keys=False)
    return labels


async def id_held_for(prefix: str) -> str | None:
    """The id of the namespace holding ``prefix``, else of its reservation,
    else None."""
    ns = await Namespace.find_one(Namespace.prefix == prefix)
    if ns is not None:
        return ns.namespace_id
    reservation = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    return reservation.namespace_id if reservation is not None else None


async def after_namespace_deleted(prefix: str) -> None:
    """A runtime reservation that outlives its namespace is for a new one; an
    install's reservation holds no id again."""
    collection = RuntimeReservation.get_motor_collection()
    result = await collection.update_one(
        {"prefix": prefix, "managed_by": None}, {"$set": {"namespace_id": new_namespace_id()}},
    )
    if result.modified_count:
        logger.info("Namespace '%s' deleted: its reservation now holds a new namespace id", prefix)
    result = await collection.update_one(
        {"prefix": prefix, "managed_by": {"$ne": None}}, {"$set": {"namespace_id": None}},
    )
    if result.modified_count:
        logger.info("Namespace '%s' deleted: the install's reservation of it holds no namespace id", prefix)


async def migrate_namespace_ids() -> dict[str, int]:
    """Give every namespace and reservation stored before the field an id, and
    delete orphaned grants that have none: they cannot be told which
    namespace they were made for. Runs at startup; conditional on the field
    being absent, so a second run changes nothing."""
    counts = {"namespaces": 0, "reservations": 0, "orphans_deleted": 0}
    async for ns in Namespace.find({"namespace_id": None}):
        result = await Namespace.get_motor_collection().update_one(
            {"_id": ns.id, "namespace_id": None}, {"$set": {"namespace_id": new_namespace_id()}},
        )
        counts["namespaces"] += result.modified_count
    async for r in RuntimeReservation.find({"namespace_id": None}):
        ns = await Namespace.find_one(Namespace.prefix == r.prefix)
        if ns is None and r.managed_by is not None:
            continue  # an install's reservation of an absent namespace holds none
        value = ns.namespace_id if ns is not None and ns.namespace_id else new_namespace_id()
        result = await RuntimeReservation.get_motor_collection().update_one(
            {"_id": r.id, "namespace_id": None}, {"$set": {"namespace_id": value}},
        )
        counts["reservations"] += result.modified_count
    async for g in NamespaceGrant.find({"orphaned": True, "namespace_id": None}):
        logger.warning(
            "Orphaned grant %s/%s:%s (%s) has no namespace id and cannot be matched to "
            "a namespace: deleted", g.namespace, g.subject_type, g.subject, g.permission,
        )
        await g.delete()
        counts["orphans_deleted"] += 1
    if any(counts.values()):
        logger.info("Namespace ids: %s", counts)
    return counts
