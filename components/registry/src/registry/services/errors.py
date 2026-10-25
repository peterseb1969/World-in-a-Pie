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


def error_fields(exc: BaseException) -> dict[str, Any]:
    """The error and error_code of a per-item result for ``exc``."""
    code = "invalid_request" if isinstance(exc, ValueError) else "internal_error"
    return {"error": str(exc), "error_code": code}
