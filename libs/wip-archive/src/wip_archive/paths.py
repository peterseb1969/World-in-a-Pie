"""Archive-supplied names used as filesystem paths or ZIP member names.

An archive carries namespace prefixes, file ids and template values that the
readers and writers of this library turn into paths: ``namespaces/<prefix>/``,
``blobs/<file_id>``, ``<out>/<prefix>/<template>.csv``. Every one of them comes
from the archive, so a crafted archive controls it. ``pathlib`` restarts at an
absolute right-hand operand and does not collapse ``..``, so an unchecked value
writes wherever the operator can write.

The rule is segment safety, not a naming policy: a value must be usable as ONE
path segment and nothing else. It deliberately does not apply the Registry's
namespace-prefix pattern, which is stricter than the names that have ever been
legal (namespaces created before that rule keep their names), so using it here
would refuse archives that restore today.
"""

from __future__ import annotations

from .exceptions import UnsafePathError

_FORBIDDEN_CHARS = ("/", "\\", "\x00")


def is_safe_segment(value: object) -> bool:
    """Whether ``value`` is a string usable as exactly one path segment."""
    if not isinstance(value, str) or value in ("", ".", ".."):
        return False
    if any(ch in value for ch in _FORBIDDEN_CHARS):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return False
    # A Windows drive letter ("C:") would make the joined path absolute there.
    return not (len(value) >= 2 and value[1] == ":" and value[0].isalpha())


def safe_segment(value: object, what: str) -> str:
    """Return ``value`` if it is a safe single path segment, else raise.

    ``what`` names the value in the error ("namespace prefix", "file id", …),
    so the refusal says which archive field was unsafe.
    """
    if not is_safe_segment(value):
        raise UnsafePathError(
            f"archive {what} {value!r} cannot be used as a path: it must be a "
            "single path segment (no '/', '\\', control characters, '.' or '..')"
        )
    return value  # type: ignore[return-value]  # is_safe_segment checked str


def check_member_name(name: str) -> None:
    """Refuse a ZIP member name that would escape or confuse the layout.

    Every member must be a relative POSIX path without ``.``/``..`` segments
    or backslashes. Members under ``namespaces/`` must name a safe namespace
    segment, and members under ``blobs/`` must be one safe file-id segment.
    """
    if name.startswith("/") or "\\" in name or "\x00" in name:
        raise UnsafePathError(f"archive member name {name!r} is not a relative path")
    parts = name.split("/")
    # A trailing "/" (directory entry) leaves an empty last part; allow it.
    body = parts[:-1] if parts and parts[-1] == "" else parts
    if any(p in ("", ".", "..") for p in body):
        raise UnsafePathError(
            f"archive member name {name!r} has an empty, '.' or '..' segment"
        )
    if parts[0] == "namespaces" and len(parts) >= 3:
        safe_segment(parts[1], "namespace prefix")
    elif parts[0] == "blobs" and len(parts) >= 2 and parts[1]:
        if len(parts) != 2:
            raise UnsafePathError(
                f"archive blob member {name!r} must be blobs/<file_id>"
            )
        safe_segment(parts[1], "file id")
