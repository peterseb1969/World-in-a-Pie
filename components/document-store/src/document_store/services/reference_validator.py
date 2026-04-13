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

    async def _get_namespace(self, namespace: str) -> dict[str, Any] | None:
        """Get namespace info by namespace prefix."""
        if namespace in self._namespace_cache:
            return self._namespace_cache[namespace]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.registry_url}/api/registry/namespaces/{namespace}",
                    headers={"X-API-Key": self.api_key},
                )
                if response.status_code == 200:
                    ns_data = response.json()
                    self._namespace_cache[namespace] = ns_data
                    return ns_data
                elif response.status_code == 404:
                    # No namespace found - allow all references (open by default)
                    self._namespace_cache[namespace] = {"isolation_mode": "open"}
                    return self._namespace_cache[namespace]
        except Exception as e:
            logger.warning(f"Failed to fetch namespace '{namespace}': {e}")

        return None

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
        # Get the document's namespace info
        namespace_info = await self._get_namespace(document_namespace)

        if not namespace_info:
            # No namespace info - allow all
            return

        is_strict = namespace_info.get("isolation_mode") == "strict"
        violations = []

        # Check template reference
        if template_namespace != document_namespace:
            if not self._is_allowed_reference(template_namespace, namespace_info, is_strict):
                violations.append({
                    "type": "template",
                    "namespace": template_namespace,
                    "message": f"Template namespace '{template_namespace}' is not accessible from '{document_namespace}' namespace",
                })

        # Check term references
        if term_references:
            term_namespaces = set()
            for ref in term_references:
                term_ns = ref.get("namespace")
                if term_ns:
                    term_namespaces.add(term_ns)

            for term_ns in term_namespaces:
                if term_ns != document_namespace:
                    if not self._is_allowed_reference(term_ns, namespace_info, is_strict):
                        violations.append({
                            "type": "term",
                            "namespace": term_ns,
                            "message": f"Term namespace '{term_ns}' is not accessible from '{document_namespace}' namespace",
                        })

        # Check file references
        if file_references:
            file_namespaces = set()
            for ref in file_references:
                file_ns = ref.get("namespace")
                if file_ns:
                    file_namespaces.add(file_ns)

            for file_ns in file_namespaces:
                if file_ns != document_namespace:
                    if not self._is_allowed_reference(file_ns, namespace_info, is_strict):
                        violations.append({
                            "type": "file",
                            "namespace": file_ns,
                            "message": f"File namespace '{file_ns}' is not accessible from '{document_namespace}' namespace",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Document references entities outside the '{document_namespace}' namespace "
                f"(isolation_mode=strict): {len(violations)} violation(s)",
                violations=violations,
            )

    def _is_allowed_external(self, namespace: str, namespace_info: dict[str, Any]) -> bool:
        """Check if an external namespace is in the allowed list."""
        allowed = namespace_info.get("allowed_external_refs", [])
        return namespace in allowed

    def _is_allowed_reference(self, namespace: str, namespace_info: dict[str, Any], is_strict: bool) -> bool:
        """
        Check if a reference to the given namespace is allowed.

        Rules:
        - Strict mode: Only own namespace + allowed_external_refs
        - Open mode: Own namespace + 'wip' (always) + allowed_external_refs
        """
        # Always allow explicitly configured external refs
        if self._is_allowed_external(namespace, namespace_info):
            return True

        if is_strict:
            # Strict mode: only allowed_external_refs (already checked above)
            return False
        else:
            # POLICY RULE: open mode always permits references to the "wip"
            # namespace. This is an intentional policy choice, not a default
            # or fallback — "wip" is the shared platform namespace that all
            # open-mode templates are allowed to reference.
            return namespace == "wip"

    async def validate_template_references(
        self,
        template_namespace: str,
        extends_template_namespace: str | None = None,
        terminology_namespaces: list[str] | None = None,
    ) -> None:
        """
        Validate that template references comply with isolation rules.

        Args:
            template_namespace: Namespace of the template being created/updated
            extends_template_namespace: Namespace of parent template (if any)
            terminology_namespaces: List of terminology namespaces referenced

        Raises:
            ReferenceValidationError: If any references violate isolation rules
        """
        namespace_info = await self._get_namespace(template_namespace)

        if not namespace_info:
            return

        is_strict = namespace_info.get("isolation_mode") == "strict"
        violations = []

        # Check extends reference
        if extends_template_namespace and extends_template_namespace != template_namespace:
            if not self._is_allowed_reference(extends_template_namespace, namespace_info, is_strict):
                violations.append({
                    "type": "extends",
                    "namespace": extends_template_namespace,
                    "message": f"Parent template namespace '{extends_template_namespace}' is not accessible from '{template_namespace}' namespace",
                })

        # Check terminology references
        if terminology_namespaces:
            for term_ns in terminology_namespaces:
                if term_ns != template_namespace:
                    if not self._is_allowed_reference(term_ns, namespace_info, is_strict):
                        violations.append({
                            "type": "terminology",
                            "namespace": term_ns,
                            "message": f"Terminology namespace '{term_ns}' is not accessible from '{template_namespace}' namespace",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Template references entities outside the '{template_namespace}' namespace "
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
