"""Shared pieces of the surgery transforms: what an edited row must recompute.

Shaping copies rows verbatim. Surgery edits them, through the transform
core's ``rewrite_row`` hook with ``strict_identity`` on, and owes what the
edit invalidates:

- **The identity hash.** A document's Registry row carries its identity hash
  in its primary key and its identity values in clear in a synonym, so an
  edit that changed identity would have to rewrite the Registry row too,
  and changing identity is a fork the platform refuses. Surgery refuses it
  as well: ``require_same_identity`` recomputes the hash with the platform's
  own feed (``wip_archive.identity``) and raises ``IdentityChangeError``
  when it differs. On surgery output, ``strict_identity`` makes any
  remaining mismatch an error rather than a warning.
- **Snapshot paths.** Term, reference and file snapshots name their field
  by path; a renamed field moves its snapshots with it (``rename_paths``).
- **Timestamps and actor.** Rows surgery writes carry ``updated_at`` (and,
  for a new version, ``created_by`` / ``updated_by``) the way the platform
  writes them, with the toolkit operation as the actor (``stamp``,
  ``actor``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .identity import compute_composite_key_hash, resolved_identity_values


class SurgeryError(ValueError):
    """A surgery cannot be done as asked."""


class IdentityChangeError(SurgeryError):
    """An edit would change a document's identity: a fork, not surgery."""

    def __init__(self, document_id: str, version: Any) -> None:
        self.document_id = document_id
        super().__init__(
            f"document {document_id} v{version}: the edit would change its identity hash; "
            "changing identity is a fork, not a surgery"
        )


def actor(transform: str) -> str:
    """The identity recorded on rows a surgery writes."""
    return f"wip-toolkit:{transform}"


def stamp(now: datetime | None = None) -> str:
    """A timestamp in the form engine archives store (UTC, no offset,
    microseconds)."""
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    return moment.isoformat(sep=" ", timespec="microseconds")


def identity_hash(
    data: dict[str, Any],
    template: dict[str, Any],
    term_references: list[dict[str, Any]] | None,
    references: list[dict[str, Any]] | None,
) -> str | None:
    """The identity hash the platform would compute; None for a template
    without identity fields (append-only: there is no identity to keep)."""
    identity_fields = list(template.get("identity_fields") or [])
    if not identity_fields:
        return None
    return compute_composite_key_hash(resolved_identity_values(
        data, identity_fields, list(template.get("fields") or []),
        term_references, references,
    ))


def require_same_identity(
    row: dict[str, Any],
    data: dict[str, Any],
    template: dict[str, Any],
    term_references: list[dict[str, Any]] | None,
    references: list[dict[str, Any]] | None,
) -> None:
    """Raise IdentityChangeError when the edited data would hash differently
    from the row's stored identity hash."""
    stored = row.get("identity_hash")
    if not stored:
        return
    try:
        recomputed = identity_hash(data, template, term_references, references)
    except (ValueError, KeyError, TypeError) as e:
        raise IdentityChangeError(str(row.get("document_id")), row.get("version")) from e
    if recomputed is not None and recomputed != stored:
        raise IdentityChangeError(str(row.get("document_id")), row.get("version"))


def _renamed(path: str, renames: dict[str, str]) -> str:
    """``path`` with a renamed top-level field's old name replaced (renames
    map new name -> old name, as templates declare them)."""
    head = re.split(r"[.\[]", path, maxsplit=1)[0]
    for new, old in renames.items():
        if head == old:
            return new + path[len(old):]
    return path


def rename_paths(snapshots: list[dict[str, Any]] | None, renames: dict[str, str]) -> list[dict[str, Any]]:
    """Copies of term / reference / file snapshots with their field paths
    moved by the template's declared renames."""
    out = []
    for snapshot in snapshots or ():
        moved = dict(snapshot)
        if isinstance(moved.get("field_path"), str):
            moved["field_path"] = _renamed(moved["field_path"], renames)
        out.append(moved)
    return out
