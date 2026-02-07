"""
Reference Validator - Validates cross-namespace references based on isolation mode.

When a namespace group has isolation_mode="strict", references to entities
in other namespace groups are not allowed. For "open" mode, references to
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
        self._group_cache: dict[str, dict[str, Any]] = {}

    async def _get_namespace_group(self, namespace: str) -> dict[str, Any] | None:
        """Get namespace group info for a namespace ID."""
        # Extract prefix from namespace (e.g., "dev-templates" -> "dev")
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
                    # No group found - treat as open mode
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

    def _is_allowed_reference(self, prefix: str, group: dict[str, Any], is_strict: bool) -> bool:
        """
        Check if a reference to the given prefix is allowed.

        Rules:
        - Strict mode: Only own prefix + allowed_external_refs
        - Open mode: Own prefix + 'wip' (always) + allowed_external_refs
        """
        allowed = group.get("allowed_external_refs", [])
        if prefix in allowed:
            return True

        if is_strict:
            return False
        else:
            # Open mode: also allow 'wip' prefix
            return prefix == "wip"

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
            terminology_namespaces: List of terminology namespace IDs referenced

        Raises:
            ReferenceValidationError: If any references violate isolation rules
        """
        group = await self._get_namespace_group(template_namespace)

        if not group:
            return

        template_prefix = self._get_prefix(template_namespace)
        is_strict = group.get("isolation_mode") == "strict"
        violations = []

        # Check extends reference
        if extends_template_namespace:
            extends_prefix = self._get_prefix(extends_template_namespace)
            if extends_prefix and extends_prefix != template_prefix:
                if not self._is_allowed_reference(extends_prefix, group, is_strict):
                    violations.append({
                        "type": "extends",
                        "namespace": extends_template_namespace,
                        "message": f"Parent template namespace '{extends_template_namespace}' is not accessible from '{template_prefix}' group",
                    })

        # Check terminology references
        if terminology_namespaces:
            for term_ns in terminology_namespaces:
                term_prefix = self._get_prefix(term_ns)
                if term_prefix and term_prefix != template_prefix:
                    if not self._is_allowed_reference(term_prefix, group, is_strict):
                        violations.append({
                            "type": "terminology",
                            "namespace": term_ns,
                            "message": f"Terminology namespace '{term_ns}' is not accessible from '{template_prefix}' group",
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
