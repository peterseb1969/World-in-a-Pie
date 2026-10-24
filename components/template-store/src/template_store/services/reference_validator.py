"""
Reference Validator - Validates cross-namespace references based on isolation mode.

When a namespace has isolation_mode="strict", references to entities
in other namespaces are not allowed. For "open" mode, references to
own namespace and "wip" namespace are allowed.
"""

import logging
import time
from typing import Any

from .registry_client import RegistryClient, RegistryError, get_registry_client

logger = logging.getLogger(__name__)

# Staleness window for cached namespace config (isolation_mode,
# allowed_external_refs). Mirrors the template cache's 5 s TTL
# (wip://conventions): namespace-config changes take effect within this
# window without a service restart. The 404-negative expires on the same
# clock, so a namespace created after being first probed becomes visible
# too (CASE-607).
NAMESPACE_CACHE_TTL_SECONDS = 5.0


class ReferenceValidationError(Exception):
    """Raised when a cross-namespace reference violates isolation rules."""

    def __init__(self, message: str, violations: list[dict[str, Any]]):
        super().__init__(message)
        self.violations = violations


class RegistryUnavailableError(Exception):
    """The namespace's isolation settings could not be read from the Registry.

    Isolation is then unknown, so the reference check refuses rather than
    passing: a check that is skipped whenever the Registry is slow or down
    lets a strict namespace accept references it forbids.
    """


class ReferenceValidator:
    """Validates cross-namespace references based on isolation mode."""

    def __init__(self, registry_url: str | None = None, api_key: str | None = None):
        # The service's shared Registry client unless a URL or key is given;
        # it honours set_registry_transport, so tests reach the in-process
        # Registry instead of the network.
        self._registry: RegistryClient | None = (
            RegistryClient(base_url=registry_url, api_key=api_key)
            if registry_url or api_key else None
        )
        self._namespace_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def _get_namespace(self, namespace: str) -> dict[str, Any]:
        """Get namespace info from the registry (cached, TTL-bounded).

        A namespace the Registry does not know is treated as open. Raises
        RegistryUnavailableError when the Registry cannot be asked.
        """
        cached = self._namespace_cache.get(namespace)
        if cached is not None and (time.monotonic() - cached[0]) < NAMESPACE_CACHE_TTL_SECONDS:
            return cached[1]

        registry = self._registry or get_registry_client()
        try:
            ns_data = await registry.get_namespace(namespace)
        except RegistryError as e:
            raise RegistryUnavailableError(
                f"Cannot check namespace isolation for '{namespace}': {e}"
            ) from e
        if ns_data is None:
            ns_data = {"isolation_mode": "open"}
        self._namespace_cache[namespace] = (time.monotonic(), ns_data)
        return ns_data

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
            # POLICY RULE: open mode always permits references to the "wip"
            # namespace. This is an intentional policy choice, not a default
            # or fallback — "wip" is the shared platform namespace that all
            # open-mode templates are allowed to reference.
            return target_ns == "wip"

    async def validate_template_references(
        self,
        template_namespace: str,
        extends_template_namespace: str | None = None,
        terminology_namespaces: list[str] | None = None,
        template_ref_namespaces: list[str] | None = None,
    ) -> None:
        """
        Validate that template references comply with isolation rules.

        Args:
            template_namespace: Namespace of the template being created/updated
            extends_template_namespace: Namespace of parent template (if any)
            terminology_namespaces: List of terminology namespaces referenced
            template_ref_namespaces: Namespaces of referenced templates
                (template_ref / array_template_ref / target_templates)

        Raises:
            ReferenceValidationError: If any references violate isolation rules
            RegistryUnavailableError: If the namespace's settings cannot be read
        """
        ns_data = await self._get_namespace(template_namespace)

        is_strict = ns_data.get("isolation_mode") == "strict"
        violations = []

        # Check extends reference
        if (
            extends_template_namespace
            and extends_template_namespace != template_namespace
            and not self._is_allowed_reference(extends_template_namespace, ns_data, is_strict)
        ):
            violations.append({
                "type": "extends",
                "namespace": extends_template_namespace,
                "message": f"Parent template namespace '{extends_template_namespace}' is not accessible from '{template_namespace}' namespace",
            })

        # Check terminology references
        if terminology_namespaces:
            for term_ns in terminology_namespaces:
                if term_ns != template_namespace and not self._is_allowed_reference(
                    term_ns, ns_data, is_strict
                ):
                    violations.append({
                        "type": "terminology",
                        "namespace": term_ns,
                        "message": f"Terminology namespace '{term_ns}' is not accessible from '{template_namespace}' namespace",
                    })

        # Check template references (template_ref / array_template_ref /
        # target_templates)
        if template_ref_namespaces:
            for tpl_ns in set(template_ref_namespaces):
                if tpl_ns != template_namespace and not self._is_allowed_reference(
                    tpl_ns, ns_data, is_strict
                ):
                    violations.append({
                        "type": "template",
                        "namespace": tpl_ns,
                        "message": f"Template namespace '{tpl_ns}' is not accessible from '{template_namespace}' namespace",
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
