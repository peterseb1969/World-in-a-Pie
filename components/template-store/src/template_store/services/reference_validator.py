"""
Reference Validator - checks a template's cross-namespace references.

Two gates, decided by wip_auth.reference_policy: the template's namespace's
isolation_mode (may it reference other namespaces?) and each target's
inbound_references (may this writer reference it?).
"""

import logging
import time
from typing import Any

from wip_auth import UserIdentity
from wip_auth.identity import get_identity_string
from wip_auth.reference_policy import PermissionUnavailableError, check_reference

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


class InboundReferenceRefused(Exception):
    """A new reference points into a closed namespace the writer cannot read.

    Reported to the writer as an unresolved reference: a closed namespace
    does not confirm that its entities exist to someone who cannot read it.
    """

    def __init__(self, kinds: list[str]):
        super().__init__(
            "Referenced " + " and ".join(kinds) + " not found"
        )
        self.kinds = kinds


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

    async def validate_template_references(
        self,
        template_namespace: str,
        extends_template_namespace: str | None = None,
        terminology_namespaces: list[str] | None = None,
        template_ref_namespaces: list[str] | None = None,
        identity: UserIdentity | None = None,
    ) -> None:
        """
        Check a template's new cross-namespace references against both gates.

        The template's namespace's isolation_mode decides whether it may
        reference another namespace (outbound); each target's
        inbound_references decides whether this writer may reference it
        (inbound). The rules live in wip_auth.reference_policy. Callers pass
        only references the template's previous version did not hold.

        Args:
            template_namespace: Namespace of the template being created/updated
            extends_template_namespace: Namespace of parent template (if any)
            terminology_namespaces: List of terminology namespaces referenced
            template_ref_namespaces: Namespaces of referenced templates
                (template_ref / array_template_ref / target_templates)
            identity: The writer; defaults to the request's identity

        Raises:
            ReferenceValidationError: A reference the namespace's own setting
                forbids
            InboundReferenceRefused: A reference into a closed namespace the
                writer cannot read
            RegistryUnavailableError: If the namespace's settings or the
                writer's permission cannot be read
        """
        ns_data = await self._get_namespace(template_namespace)
        targets: list[tuple[str, str]] = []
        if extends_template_namespace:
            targets.append(("extends", extends_template_namespace))
        targets += [("terminology", ns) for ns in sorted(set(terminology_namespaces or []))]
        targets += [("template", ns) for ns in sorted(set(template_ref_namespaces or []))]

        violations = []
        refused: list[str] = []
        for kind, target_ns in targets:
            if target_ns == template_namespace:
                continue
            try:
                verdict = await check_reference(
                    template_namespace, ns_data,
                    target_ns, self._get_namespace,
                    identity,
                )
            except PermissionUnavailableError as e:
                raise RegistryUnavailableError(str(e)) from e
            if verdict == "outbound":
                label = "Parent template" if kind == "extends" else kind.capitalize()
                violations.append({
                    "type": kind,
                    "namespace": target_ns,
                    "message": (
                        f"{label} namespace '{target_ns}' is not accessible from "
                        f"'{template_namespace}' namespace"
                    ),
                })
            elif verdict == "inbound":
                refused.append(kind)

        if violations:
            raise ReferenceValidationError(
                f"Template references entities outside allowed namespaces "
                f"(isolation_mode={ns_data.get('isolation_mode', 'open')}): "
                f"{len(violations)} violation(s)",
                violations=violations,
            )
        if refused:
            logger.warning(
                "Inbound reference refused: %s writing a template in '%s' cannot read %s",
                get_identity_string(), template_namespace,
                sorted({ns for kind, ns in targets if kind in refused} - {template_namespace}),
            )
            raise InboundReferenceRefused(sorted(set(refused)))


# Singleton instance
_validator: ReferenceValidator | None = None


def get_reference_validator() -> ReferenceValidator:
    """Get the singleton reference validator instance."""
    global _validator
    if _validator is None:
        _validator = ReferenceValidator()
    return _validator
