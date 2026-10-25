"""Machine-readable codes for document-store's bulk error items.

Bulk results carry an ``error_code`` so callers branch on the code, not the
message. ``error_fields`` maps the exceptions document-store's handlers catch
to the error, error_code and details of a per-item result: the typed ones
(``DocumentStoreError``, ``PatchError``, ``FileServiceError``, the reference
validator's errors, the Registry client's ``RegistryError``) by their
meaning, any other ``ValueError`` as ``invalid_request`` and any other
exception as ``internal_error`` — so no error item is ever without a code.
"""

from typing import Any

from fastapi import HTTPException


class DocumentStoreError(ValueError):
    """A document-store operation refused for a reason callers can branch on.

    A ``ValueError``, so existing handlers keep catching it; its message is
    the text a plain ``ValueError`` would have carried.
    """

    def __init__(self, message: str, code: str, rule: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] | None = {"rule": rule} if rule else None


def error_fields(exc: BaseException) -> dict[str, Any]:
    """The error, error_code and details of a per-item bulk result for ``exc``."""
    # Imported here: these modules import this one.
    from .document_service import PatchError
    from .file_service import FileServiceError
    from .reference_validator import ReferenceValidationError, RegistryUnavailableError
    from .registry_client import RegistryError

    message = str(exc)
    if isinstance(exc, DocumentStoreError):
        return {"error": message, "error_code": exc.code, "details": exc.details}
    if isinstance(exc, PatchError):
        return {"error": exc.message, "error_code": exc.code}
    if isinstance(exc, FileServiceError):
        return {"error": message, "error_code": exc.code, "details": exc.details}
    if isinstance(exc, ReferenceValidationError):
        # The code PATCH /documents has always reported for this failure.
        return {"error": f"Cross-namespace reference violation: {exc.violations}",
                "error_code": "reference_violation",
                "details": {"rule": "cross_namespace", "violations": exc.violations}}
    if isinstance(exc, RegistryUnavailableError):
        return {"error": message, "error_code": "registry_error",
                "details": {"rule": "registry_unavailable"}}
    if isinstance(exc, RegistryError):
        return {"error": f"Registry error: {message}", "error_code": "registry_error"}
    if isinstance(exc, HTTPException):
        code = {403: "forbidden", 404: "not_found"}.get(exc.status_code, "invalid_request")
        return {"error": str(exc.detail), "error_code": code}
    if isinstance(exc, ValueError):
        return {"error": message, "error_code": "invalid_request"}
    # Not a refusal of the request: a fault on the server's side.
    return {"error": message, "error_code": "internal_error"}
