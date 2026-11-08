"""Typed errors for archive reading.

The reader used to raise raw library exceptions (``zipfile.BadZipFile``,
``KeyError``, ``json.JSONDecodeError``) that a caller could not tell apart from
a bug. These name the malformed-archive failure modes so a caller — the restore
route — can turn them into an actionable refusal at upload time, instead of
minting a job that fails later inside the engine with a raw traceback in its
record.

Format POLICY (which versions a caller accepts) is deliberately NOT here: the
library describes an archive's shape, and the version gate lives in the
document-store restore route. These types are strictly about "this bytes blob
is not a well-formed archive".
"""

from __future__ import annotations


class ArchiveError(Exception):
    """Base for a malformed or unreadable backup archive."""


class NotAnArchiveError(ArchiveError):
    """The file is not a readable ZIP archive (truncated, corrupt, or not a zip)."""


class MissingManifestError(ArchiveError):
    """The archive has no ``manifest.json``."""


class ManifestParseError(ArchiveError):
    """The archive's ``manifest.json`` is not valid JSON."""


class ManifestValidationError(ArchiveError):
    """The archive's ``manifest.json`` is JSON but not a valid manifest
    (a required field is missing or has the wrong type)."""


class EntityParseError(ArchiveError):
    """A line of one of the archive's entity JSONL files is not valid JSON."""


class UnsafePathError(ArchiveError):
    """A name the archive supplies — a namespace prefix, a file id, a template
    value, a ZIP member name — cannot be used safely as a path segment."""
