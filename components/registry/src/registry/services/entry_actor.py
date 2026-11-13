"""Who changed an entry: the authenticated caller, never the caller's text.

Every write that changes a Registry entry stamps it the same way, as
namespaces are stamped: ``updated_by`` is the identity the request
authenticated as, and whatever the caller wrote about who acted (the request
items' ``updated_by`` field) is kept beside it as ``actor_note``. Both are set
on every such write, so a change that names nobody clears the note instead
of leaving an earlier caller's words beside a new ``updated_by``, and no
change ever stores ``updated_by: null``.

Creation follows the same rule: an entry's and a synonym's ``created_by`` is
the authenticated caller, and the caller's ``created_by`` text is the entry's
``actor_note`` (register, reserve and provision set them directly).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from wip_auth import UserIdentity

from ..models.entry import RegistryEntry

# The entry fields a stamp writes; every write that stamps lists them.
STAMP_FIELDS: tuple[str, ...] = ("updated_at", "updated_by", "actor_note")


@dataclass(frozen=True)
class EntryActor:
    """The caller of one entry write: its identity and its own note."""

    by: str
    note: str | None = None

    def stamp(self, entry: RegistryEntry, now: datetime) -> None:
        entry.updated_at = now
        entry.updated_by = self.by
        entry.actor_note = self.note

    def noting(self, note: str | None) -> EntryActor:
        return EntryActor(self.by, note)


def entry_actor(identity: UserIdentity, note: str | None = None) -> EntryActor:
    return EntryActor(identity.identity_string, note)
