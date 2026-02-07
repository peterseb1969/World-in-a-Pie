"""
Reference Validator - Validates cross-namespace references based on isolation mode.

When a namespace group has isolation_mode="strict", references to entities
in other namespace groups are not allowed. This service validates that
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
        self._group_cache: dict[str, dict[str, Any]] = {}

    async def _get_namespace_group(self, namespace: str) -> dict[str, Any] | None:
        """Get namespace group info for a namespace ID."""
        # Extract prefix from namespace (e.g., "dev-documents" -> "dev")
        if "-" not in namespace:
            return None

        prefix = namespace.rsplit("-", 1)[0]

        if prefix in self._group_cache:
            return self._group_cache[prefix]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.registry_url}/api/registry/namespace-groups/{prefix}",
                    headers={"X-API-Key": self.api_key},
                )
                if response.status_code == 200:
                    group = response.json()
                    self._group_cache[prefix] = group
                    return group
                elif response.status_code == 404:
                    # No group found - allow all references (open by default)
                    self._group_cache[prefix] = {"isolation_mode": "open"}
                    return self._group_cache[prefix]
        except Exception as e:
            logger.warning(f"Failed to fetch namespace group for {namespace}: {e}")

        return None

    def _get_prefix(self, namespace: str) -> str | None:
        """Extract prefix from namespace ID."""
        if "-" not in namespace:
            return None
        return namespace.rsplit("-", 1)[0]

    async def validate_document_references(
        self,
        document_namespace: str,
        template_namespace: str,
        term_references: list[dict[str, Any]] | None = None,
        file_references: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Validate that all references in a document comply with isolation rules.

        Args:
            document_namespace: Namespace of the document being created/updated
            template_namespace: Namespace of the referenced template
            term_references: List of term reference objects
            file_references: List of file reference objects

        Raises:
            ReferenceValidationError: If any references violate isolation rules
        """
        # Get the document's namespace group
        group = await self._get_namespace_group(document_namespace)

        if not group:
            # No group info - allow all
            return

        if group.get("isolation_mode") != "strict":
            # Open mode - allow all cross-namespace references
            return

        doc_prefix = self._get_prefix(document_namespace)
        violations = []

        # Check template reference
        template_prefix = self._get_prefix(template_namespace)
        if template_prefix and template_prefix != doc_prefix:
            if not self._is_allowed_external(template_prefix, group):
                violations.append({
                    "type": "template",
                    "namespace": template_namespace,
                    "message": f"Template namespace '{template_namespace}' is outside the '{doc_prefix}' group",
                })

        # Check term references
        if term_references:
            # Get unique term namespaces
            term_namespaces = set()
            for ref in term_references:
                # Term references may have a namespace field or we infer from term_id
                term_ns = ref.get("namespace") or ref.get("term_namespace")
                if term_ns:
                    term_namespaces.add(term_ns)

            for term_ns in term_namespaces:
                term_prefix = self._get_prefix(term_ns)
                if term_prefix and term_prefix != doc_prefix:
                    if not self._is_allowed_external(term_prefix, group):
                        violations.append({
                            "type": "term",
                            "namespace": term_ns,
                            "message": f"Term namespace '{term_ns}' is outside the '{doc_prefix}' group",
                        })

        # Check file references
        if file_references:
            file_namespaces = set()
            for ref in file_references:
                file_ns = ref.get("namespace") or ref.get("file_namespace")
                if file_ns:
                    file_namespaces.add(file_ns)

            for file_ns in file_namespaces:
                file_prefix = self._get_prefix(file_ns)
                if file_prefix and file_prefix != doc_prefix:
                    if not self._is_allowed_external(file_prefix, group):
                        violations.append({
                            "type": "file",
                            "namespace": file_ns,
                            "message": f"File namespace '{file_ns}' is outside the '{doc_prefix}' group",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Document references entities outside the '{doc_prefix}' namespace group "
                f"(isolation_mode=strict): {len(violations)} violation(s)",
                violations=violations,
            )

    def _is_allowed_external(self, prefix: str, group: dict[str, Any]) -> bool:
        """Check if an external prefix is in the allowed list."""
        allowed = group.get("allowed_external_refs", [])
        return prefix in allowed

    async def validate_template_references(
        self,
        template_namespace: str,
        extends_template_namespace: str | None = None,
        terminology_references: list[str] | None = None,
    ) -> None:
        """
        Validate that template references comply with isolation rules.

        Args:
            template_namespace: Namespace of the template being created/updated
            extends_template_namespace: Namespace of parent template (if any)
            terminology_references: List of terminology namespace IDs referenced

        Raises:
            ReferenceValidationError: If any references violate isolation rules
        """
        group = await self._get_namespace_group(template_namespace)

        if not group:
            return

        if group.get("isolation_mode") != "strict":
            return

        template_prefix = self._get_prefix(template_namespace)
        violations = []

        # Check extends reference
        if extends_template_namespace:
            extends_prefix = self._get_prefix(extends_template_namespace)
            if extends_prefix and extends_prefix != template_prefix:
                if not self._is_allowed_external(extends_prefix, group):
                    violations.append({
                        "type": "extends",
                        "namespace": extends_template_namespace,
                        "message": f"Parent template namespace '{extends_template_namespace}' is outside the '{template_prefix}' group",
                    })

        # Check terminology references
        if terminology_references:
            for term_ns in terminology_references:
                term_prefix = self._get_prefix(term_ns)
                if term_prefix and term_prefix != template_prefix:
                    if not self._is_allowed_external(term_prefix, group):
                        violations.append({
                            "type": "terminology",
                            "namespace": term_ns,
                            "message": f"Terminology namespace '{term_ns}' is outside the '{template_prefix}' group",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Template references entities outside the '{template_prefix}' namespace group "
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
