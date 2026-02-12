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

    async def _get_namespace(self, pool_id: str) -> dict[str, Any] | None:
        """Get namespace info for an ID pool."""
        # Extract prefix from pool_id (e.g., "dev-templates" -> "dev")
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
                    # No namespace found - treat as open mode
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

    def _is_allowed_reference(self, prefix: str, namespace: dict[str, Any], is_strict: bool) -> bool:
        """
        Check if a reference to the given prefix is allowed.

        Rules:
        - Strict mode: Only own prefix + allowed_external_refs
        - Open mode: Own prefix + 'wip' (always) + allowed_external_refs
        """
        allowed = namespace.get("allowed_external_refs", [])
        if prefix in allowed:
            return True

        if is_strict:
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
            for term_pool in terminology_pool_ids:
                term_prefix = self._get_prefix(term_pool)
                if term_prefix and term_prefix != template_prefix:
                    if not self._is_allowed_reference(term_prefix, namespace, is_strict):
                        violations.append({
                            "type": "terminology",
                            "pool_id": term_pool,
                            "message": f"Terminology pool '{term_pool}' is not accessible from '{template_prefix}' namespace",
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
