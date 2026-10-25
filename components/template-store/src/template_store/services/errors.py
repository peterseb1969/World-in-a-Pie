"""Machine-readable errors for template operations.

Bulk results carry an ``error_code`` so callers branch on the code, not the
message. A ``TemplateError`` names its code (and, where one code covers many
rules, the rule that failed, as ``details.rule``). It is a ``ValueError`` so
every existing handler keeps catching it, and its message is the same text a
plain ``ValueError`` would have carried.

Codes raised here: ``validation_failed``, ``not_found``, ``already_exists``,
``reference_violation``, ``cross_namespace_reference``,
``identity_fields_immutable``, ``immutable_property``, ``invalid_state`` and
``invalid_request``. Any other ``ValueError`` reaching a bulk handler is
reported as ``invalid_request`` and any other exception as ``internal_error``,
so no error item is ever without a code.
"""

from typing import Any


class TemplateError(ValueError):
    """A template operation refused for a reason callers can branch on."""

    def __init__(self, message: str, code: str, rule: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] | None = {"rule": rule} if rule else None


def error_fields(exc: Exception) -> dict[str, Any]:
    """The error, error_code and details of a per-item bulk result for ``exc``."""
    if isinstance(exc, TemplateError):
        return {"error": str(exc), "error_code": exc.code, "details": exc.details}
    if isinstance(exc, ValueError):
        return {"error": str(exc), "error_code": "invalid_request"}
    # Not a refusal of the request: a fault on the server's side.
    return {"error": str(exc), "error_code": "internal_error"}
