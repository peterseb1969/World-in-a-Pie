"""
Reference Validator - Validates cross-namespace references based on isolation mode.

When a namespace has isolation_mode="strict", references to entities
in other namespaces are not allowed. This service validates that
all references in a document comply with the isolation rules.
"""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ReferenceValidationError(Exception):
    """Raised when a cross-namespace reference violates isolation rules."""

    def __init__(self, message: str, violations: list[dict[str, Any]]):
        super().__init__(message)
        self.violations = violations


class ReferenceValidator:
    """Validates cross-namespace references based on isolation mode."""

    def __init__(self, registry_url: str | None = None, api_key: str | None = None):
        self.registry_url = registry_url or os.getenv("REGISTRY_URL", "http://localhost:8001")
        self.api_key = api_key or os.getenv("WIP_AUTH_LEGACY_API_KEY", "")
        self._namespace_cache: dict[str, dict[str, Any]] = {}

    async def _get_namespace(self, pool_id: str) -> dict[str, Any] | None:
        """Get namespace info for an ID pool."""
        # Extract prefix from pool_id (e.g., "dev-documents" -> "dev")
        if "-" not in pool_id:
            return None

        prefix = pool_id.rsplit("-", 1)[0]

        if prefix in self._namespace_cache:
            return self._namespace_cache[prefix]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.registry_url}/api/registry/namespaces/{prefix}",
                    headers={"X-API-Key": self.api_key},
                )
                if response.status_code == 200:
                    namespace = response.json()
                    self._namespace_cache[prefix] = namespace
                    return namespace
                elif response.status_code == 404:
                    # No namespace found - allow all references (open by default)
                    self._namespace_cache[prefix] = {"isolation_mode": "open"}
                    return self._namespace_cache[prefix]
        except Exception as e:
            logger.warning(f"Failed to fetch namespace for {pool_id}: {e}")

        return None

    def _get_prefix(self, pool_id: str) -> str | None:
        """Extract prefix from pool ID."""
        if "-" not in pool_id:
            return None
        return pool_id.rsplit("-", 1)[0]

    async def validate_document_references(
        self,
        document_pool_id: str,
        template_pool_id: str,
        term_references: list[dict[str, Any]] | None = None,
        file_references: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Validate that all references in a document comply with isolation rules.

        Args:
            document_pool_id: Pool ID of the document being created/updated
            template_pool_id: Pool ID of the referenced template
            term_references: List of term reference objects
            file_references: List of file reference objects

        Raises:
            ReferenceValidationError: If any references violate isolation rules
        """
        # Get the document's namespace
        namespace = await self._get_namespace(document_pool_id)

        if not namespace:
            # No namespace info - allow all
            return

        doc_prefix = self._get_prefix(document_pool_id)
        is_strict = namespace.get("isolation_mode") == "strict"
        violations = []

        # Check template reference
        template_prefix = self._get_prefix(template_pool_id)
        if template_prefix and template_prefix != doc_prefix:
            if not self._is_allowed_reference(template_prefix, namespace, is_strict):
                violations.append({
                    "type": "template",
                    "pool_id": template_pool_id,
                    "message": f"Template pool '{template_pool_id}' is not accessible from '{doc_prefix}' namespace",
                })

        # Check term references
        if term_references:
            # Get unique term namespaces
            term_namespaces = set()
            for ref in term_references:
                # Term references may have a pool_id field or we infer from term_id
                term_ns = ref.get("pool_id") or ref.get("term_pool_id")
                if term_ns:
                    term_namespaces.add(term_ns)

            for term_ns in term_namespaces:
                term_prefix = self._get_prefix(term_ns)
                if term_prefix and term_prefix != doc_prefix:
                    if not self._is_allowed_reference(term_prefix, namespace, is_strict):
                        violations.append({
                            "type": "term",
                            "pool_id": term_ns,
                            "message": f"Term pool '{term_ns}' is not accessible from '{doc_prefix}' namespace",
                        })

        # Check file references
        if file_references:
            file_namespaces = set()
            for ref in file_references:
                file_ns = ref.get("pool_id") or ref.get("file_pool_id")
                if file_ns:
                    file_namespaces.add(file_ns)

            for file_ns in file_namespaces:
                file_prefix = self._get_prefix(file_ns)
                if file_prefix and file_prefix != doc_prefix:
                    if not self._is_allowed_reference(file_prefix, namespace, is_strict):
                        violations.append({
                            "type": "file",
                            "pool_id": file_ns,
                            "message": f"File pool '{file_ns}' is not accessible from '{doc_prefix}' namespace",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Document references entities outside the '{doc_prefix}' namespace "
                f"(isolation_mode=strict): {len(violations)} violation(s)",
                violations=violations,
            )

    def _is_allowed_external(self, prefix: str, namespace: dict[str, Any]) -> bool:
        """Check if an external prefix is in the allowed list."""
        allowed = namespace.get("allowed_external_refs", [])
        return prefix in allowed

    def _is_allowed_reference(self, prefix: str, namespace: dict[str, Any], is_strict: bool) -> bool:
        """
        Check if a reference to the given prefix is allowed.

        Rules:
        - Strict mode: Only own prefix + allowed_external_refs
        - Open mode: Own prefix + 'wip' (always) + allowed_external_refs
        """
        # Always allow explicitly configured external refs
        if self._is_allowed_external(prefix, namespace):
            return True

        if is_strict:
            # Strict mode: only allowed_external_refs (already checked above)
            return False
        else:
            # Open mode: also allow 'wip' prefix
            return prefix == "wip"

    async def validate_template_references(
        self,
        template_pool_id: str,
        extends_template_pool_id: str | None = None,
        terminology_pool_ids: list[str] | None = None,
    ) -> None:
        """
        Validate that template references comply with isolation rules.

        Args:
            template_pool_id: Pool ID of the template being created/updated
            extends_template_pool_id: Pool ID of parent template (if any)
            terminology_pool_ids: List of terminology pool IDs referenced

        Raises:
            ReferenceValidationError: If any references violate isolation rules
        """
        namespace = await self._get_namespace(template_pool_id)

        if not namespace:
            return

        template_prefix = self._get_prefix(template_pool_id)
        is_strict = namespace.get("isolation_mode") == "strict"
        violations = []

        # Check extends reference
        if extends_template_pool_id:
            extends_prefix = self._get_prefix(extends_template_pool_id)
            if extends_prefix and extends_prefix != template_prefix:
                if not self._is_allowed_reference(extends_prefix, namespace, is_strict):
                    violations.append({
                        "type": "extends",
                        "pool_id": extends_template_pool_id,
                        "message": f"Parent template pool '{extends_template_pool_id}' is not accessible from '{template_prefix}' namespace",
                    })

        # Check terminology references
        if terminology_pool_ids:
            for term_ns in terminology_pool_ids:
                term_prefix = self._get_prefix(term_ns)
                if term_prefix and term_prefix != template_prefix:
                    if not self._is_allowed_reference(term_prefix, namespace, is_strict):
                        violations.append({
                            "type": "terminology",
                            "pool_id": term_ns,
                            "message": f"Terminology pool '{term_ns}' is not accessible from '{template_prefix}' namespace",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Template references entities outside the '{template_prefix}' namespace "
                f"(isolation_mode=strict): {len(violations)} violation(s)",
                violations=violations,
            )


# Singleton instance
_validator: ReferenceValidator | None = None


def get_reference_validator() -> ReferenceValidator:
    """Get the singleton reference validator instance."""
    global _validator
    if _validator is None:
        _validator = ReferenceValidator()
    return _validator
