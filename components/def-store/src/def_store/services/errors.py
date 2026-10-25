"""Machine-readable errors for terminology, term and relation operations.

Bulk results carry an ``error_code`` so callers branch on the code, not the
message. A ``DefStoreError`` names its code (and, where one code covers
several rules, the rule that failed, as ``details.rule``). It is a
``ValueError`` so every existing handler keeps catching it, and its message
is the text a plain ``ValueError`` would have carried.

``error_fields`` also codes def-store's older typed exceptions
(``EntityExistsError``, ``NameCollisionError``, ``TermNameError``, …) and the
Registry client's ``RegistryError``. Any other ``ValueError`` reaching a bulk
handler is reported as ``invalid_request`` and any other exception as
``internal_error``, so no error item is ever without a code.
"""

from typing import Any

from fastapi import HTTPException


class DefStoreError(ValueError):
    """A def-store operation refused for a reason callers can branch on."""

    def __init__(self, message: str, code: str, rule: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] | None = {"rule": rule} if rule else None


def error_fields(exc: Exception) -> dict[str, Any]:
    """The error, error_code and details of a per-item bulk result for ``exc``."""
    # Imported here: these modules import this one.
    from .registry_client import RegistryError
    from .term_names import TermNameError
    from .terminology_service import (
        AmbiguousTerminologyValueError,
        EntityExistsError,
        NameCollisionError,
        RegistryEntryNotRemovedError,
    )

    message = str(exc)
    if isinstance(exc, DefStoreError):
        return {"error": message, "error_code": exc.code, "details": exc.details}
    if isinstance(exc, EntityExistsError):
        return {"error": message, "error_code": "already_exists"}
    if isinstance(exc, NameCollisionError):
        return {"error": message, "error_code": "name_collision",
                "details": {"name_collisions": exc.collisions}}
    if isinstance(exc, TermNameError):
        return {"error": message, "error_code": "invalid_names"}
    if isinstance(exc, AmbiguousTerminologyValueError):
        return {"error": message, "error_code": "invalid_request",
                "details": {"rule": "ambiguous_value", "namespaces": exc.namespaces}}
    if isinstance(exc, RegistryEntryNotRemovedError):
        return {"error": message, "error_code": "registry_entry_not_removed"}
    if isinstance(exc, RegistryError):
        return {"error": f"Registry error: {message}", "error_code": "registry_error"}
    if isinstance(exc, HTTPException):
        code = {403: "forbidden", 404: "not_found"}.get(exc.status_code, "invalid_request")
        return {"error": str(exc.detail), "error_code": code}
    if isinstance(exc, ValueError):
        return {"error": message, "error_code": "invalid_request"}
    # Not a refusal of the request: a fault on the server's side.
    return {"error": message, "error_code": "internal_error"}
