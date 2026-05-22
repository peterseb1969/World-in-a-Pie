"""Document-store wrapper around the canonical identity-hash library.

The algorithm lives in ``libs/wip-auth/src/wip_auth/document_identity.py``
(canonical home — see CASE-402). ``IdentityService`` preserves the
static-method shape document-store callers expect; each method is a thin
delegate. Tests pin the contract at the lib boundary; behavioral
divergence is structurally impossible.
"""

from typing import Any

from wip_auth.document_identity import (
    compute_hash,
    compute_identity_hash,
    compute_normalized_hash,
    extract_identity_values,
    normalize_value,
)
from wip_auth.document_identity import _get_nested_value as _module_get_nested_value


class IdentityService:
    """Static-method facade for compute_identity_hash & friends.

    Preserved for back-compat with document-store call sites
    (``IdentityService.compute_identity_hash(...)``). New callers should
    import the module-level functions from ``wip_auth.document_identity``
    directly.
    """

    @staticmethod
    def extract_identity_values(
        data: dict[str, Any],
        identity_fields: list[str],
    ) -> dict[str, Any]:
        return extract_identity_values(data, identity_fields)

    @staticmethod
    def _get_nested_value(data: dict[str, Any], field_path: str) -> Any | None:
        return _module_get_nested_value(data, field_path)

    @staticmethod
    def compute_hash(identity_values: dict[str, Any]) -> str:
        return compute_hash(identity_values)

    @staticmethod
    def compute_identity_hash(
        data: dict[str, Any],
        identity_fields: list[str],
    ) -> str:
        return compute_identity_hash(data, identity_fields)

    @staticmethod
    def normalize_value(value: Any) -> Any:
        return normalize_value(value)

    @staticmethod
    def compute_normalized_hash(
        data: dict[str, Any],
        identity_fields: list[str],
    ) -> str:
        return compute_normalized_hash(data, identity_fields)
