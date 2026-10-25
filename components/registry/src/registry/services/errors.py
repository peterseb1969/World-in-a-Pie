"""Machine-readable codes for the Registry's per-item error results.

Registry results keep their reason statuses (``already_exists``,
``not_found``, ``forbidden``, …) — internal callers branch on them by value.
An item whose status is ``error`` also carries an ``error_code`` from the
same vocabulary the other services' bulk results use. ``error_fields`` codes
an exception caught by a per-item handler: a ``ValueError`` is a refusal of
the request (``invalid_request``), anything else a fault on the server's side
(``internal_error``).
"""

from typing import Any

from ..models.id_algorithm import VALID_ENTITY_TYPES


def error_fields(exc: BaseException) -> dict[str, Any]:
    """The error and error_code of a per-item result for ``exc``."""
    code = "invalid_request" if isinstance(exc, ValueError) else "internal_error"
    return {"error": str(exc), "error_code": code}


def invalid_entity_type_fields(*entity_types: str | None) -> dict[str, Any] | None:
    """The per-item refusal for an entity type that does not exist, or None.

    Every lookup and synonym write matches entity types exactly, so an unknown
    one (the singular ``document``) can never match anything: a lookup would
    report ``not_found`` for a key that exists, and a synonym stored under it
    would never resolve. Absent (None) types are not checked.
    """
    invalid = sorted({t for t in entity_types if t is not None and t not in VALID_ENTITY_TYPES})
    if not invalid:
        return None
    return {
        "error": f"Invalid entity_type: {', '.join(invalid)} "
                 f"(valid: {', '.join(sorted(VALID_ENTITY_TYPES))})",
        "error_code": "invalid_request",
        "details": {"rule": "invalid_entity_type"},
    }
