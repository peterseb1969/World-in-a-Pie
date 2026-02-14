"""
Reference Validator - Validates cross-namespace references based on isolation mode.

When a namespace has isolation_mode="strict", references to entities
in other namespaces are not allowed. For "open" mode, references to
own namespace and "wip" namespace are allowed.
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
        """Get namespace info from the registry."""
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
                    # No namespace found - treat as open mode
                    self._namespace_cache[namespace] = {"isolation_mode": "open"}
                    return self._namespace_cache[namespace]
        except Exception as e:
            logger.warning(f"Failed to fetch namespace '{namespace}': {e}")

        return None

    def _is_allowed_reference(self, target_ns: str, source_ns_data: dict[str, Any], is_strict: bool) -> bool:
        """
        Check if a reference to the given namespace is allowed.

        Rules:
        - Strict mode: Only own namespace + allowed_external_refs
        - Open mode: Own namespace + 'wip' (always) + allowed_external_refs
        """
        allowed = source_ns_data.get("allowed_external_refs", [])
        if target_ns in allowed:
            return True

        if is_strict:
            return False
        else:
            # Open mode: also allow 'wip' namespace
            return target_ns == "wip"

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
        ns_data = await self._get_namespace(template_namespace)

        if not ns_data:
            return

        is_strict = ns_data.get("isolation_mode") == "strict"
        violations = []

        # Check extends reference
        if extends_template_namespace and extends_template_namespace != template_namespace:
            if not self._is_allowed_reference(extends_template_namespace, ns_data, is_strict):
                violations.append({
                    "type": "extends",
                    "namespace": extends_template_namespace,
                    "message": f"Parent template namespace '{extends_template_namespace}' is not accessible from '{template_namespace}' namespace",
                })

        # Check terminology references
        if terminology_namespaces:
            for term_ns in terminology_namespaces:
                if term_ns != template_namespace:
                    if not self._is_allowed_reference(term_ns, ns_data, is_strict):
                        violations.append({
                            "type": "terminology",
                            "namespace": term_ns,
                            "message": f"Terminology namespace '{term_ns}' is not accessible from '{template_namespace}' namespace",
                        })

        if violations:
            raise ReferenceValidationError(
                f"Template references entities outside allowed namespaces "
                f"(isolation_mode={'strict' if is_strict else 'open'}): {len(violations)} violation(s)",
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
