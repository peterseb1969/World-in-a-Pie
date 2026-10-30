"""
Reference Validator - checks a write's cross-namespace references.

Two gates, decided by wip_auth.reference_policy: the source namespace's
isolation_mode (may it reference other namespaces?) and the target's
inbound_references (may this writer reference it?). This module gathers a
document's new references, places each in its namespace and applies the
policy.
"""

import logging
import time
from typing import Any

import httpx

from wip_auth import UserIdentity
from wip_auth.reference_policy import (
    outbound_allows,
)

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
        """Get namespace info by namespace prefix (cached, TTL-bounded).

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

    async def validate_document_references(
        self,
        document_namespace: str,
        template_namespace: str,
        term_references: list[dict[str, Any]] | None = None,
        file_references: list[dict[str, Any]] | None = None,
        document_references: list[dict[str, Any]] | None = None,
        previous: Any | None = None,
        identity: UserIdentity | None = None,
    ) -> None:
        """
        Check a document's new cross-namespace references against the
        outbound gate: the source namespace's isolation_mode decides whether
        it may reference another namespace (wip_auth.reference_policy).

        The inbound gate (the target's inbound_references) is applied during
        validation instead (inbound_gate): a refused reference must read
        exactly like a missing one, which only the resolvers themselves can
        report. Only references the previous version did not already hold
        are checked:
        a reference carried over unchanged stays valid even after the writer
        loses read on its target, and the document's template, fixed for the
        document's life, is checked when the document is first written.

        Args:
            document_namespace: Namespace of the document being created/updated
            template_namespace: Namespace of the referenced template
            term_references: List of term reference objects
            file_references: List of file reference objects
            document_references: List of resolved reference objects
                (ValidationResult.references shape): document, term,
                terminology and template references
            previous: The version this write replaces (a Document or its
                dict), or None for a new document
            identity: The writer; defaults to the request's identity

        Raises:
            ReferenceValidationError: A reference the source namespace's own
                setting forbids
            RegistryUnavailableError: If namespace settings, target namespaces
                or the writer's permission cannot be read
        """
        carried = _referenced_ids(previous) if previous is not None else set()
        # (type, entity id, namespace if the reference carries it, field path)
        targets: list[tuple[str, str, str | None, str | None]] = []
        seen: set[str] = set()

        def add(kind: str, entity_id: str, namespace: str | None, field: str | None) -> None:
            if entity_id in carried or entity_id in seen:
                return
            seen.add(entity_id)
            targets.append((kind, entity_id, namespace, field))

        if previous is None and template_namespace != document_namespace:
            targets.append(("template", "", template_namespace, None))
        for ref in document_references or []:
            resolved = ref.get("resolved") or {}
            kind = ref.get("reference_type") or ""
            entity_id = resolved.get(_RESOLVED_ID_KEYS.get(kind, ""))
            if entity_id:
                add(
                    kind, entity_id,
                    resolved.get("namespace") if kind == "document" else None,
                    ref.get("field_path"),
                )
        for ref in term_references or []:
            if ref.get("term_id"):
                add("term", ref["term_id"], None, ref.get("field_path"))
        for ref in file_references or []:
            if ref.get("file_id"):
                add("file", ref["file_id"], None, ref.get("field_path"))
        if not targets:
            return

        unplaced = sorted({eid for _, eid, ns, _ in targets if ns is None and eid})
        placed: dict[str, str] = {}
        if unplaced:
            registry = self._registry or get_registry_client()
            try:
                placed = await registry.lookup_entry_namespaces(unplaced, raise_on_error=True)
            except (RegistryError, httpx.HTTPError) as e:
                raise RegistryUnavailableError(
                    f"Cannot check the namespaces of references from '{document_namespace}': {e}"
                ) from e

        source_config = await self._get_namespace(document_namespace)
        violations: list[dict[str, Any]] = []
        for kind, entity_id, namespace, _field in targets:
            target_ns = namespace or placed.get(entity_id)
            # A target the Registry cannot place is not an active entity;
            # validation reports that, not this check.
            if not target_ns or target_ns == document_namespace:
                continue
            if not outbound_allows(document_namespace, source_config, target_ns):
                violations.append({
                    "type": kind,
                    "namespace": target_ns,
                    "message": (
                        f"{kind.capitalize()} namespace '{target_ns}' is not accessible "
                        f"from '{document_namespace}' namespace"
                    ),
                })

        if violations:
            raise ReferenceValidationError(
                f"Document references entities outside the '{document_namespace}' namespace "
                f"(isolation_mode=strict): {len(violations)} violation(s)",
                violations=violations,
            )

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
            RegistryUnavailableError: If the namespace's settings cannot be read
        """
        namespace_info = await self._get_namespace(template_namespace)

        violations = []

        # Check extends reference
        if (
            extends_template_namespace
            and extends_template_namespace != template_namespace
            and not outbound_allows(template_namespace, namespace_info, extends_template_namespace)
        ):
            violations.append({
                "type": "extends",
                "namespace": extends_template_namespace,
                "message": f"Parent template namespace '{extends_template_namespace}' is not accessible from '{template_namespace}' namespace",
            })

        # Check terminology references
        if terminology_namespaces:
            for term_ns in terminology_namespaces:
                if (
                    term_ns != template_namespace
                    and not outbound_allows(template_namespace, namespace_info, term_ns)
                ):
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


# The id a resolved reference carries, per reference_type.
_RESOLVED_ID_KEYS = {
    "document": "document_id",
    "term": "term_id",
    "terminology": "terminology_id",
    "template": "template_id",
}


def _referenced_ids(previous: Any) -> set[str]:
    """Every entity id the previous version already referenced."""
    def field(name: str) -> list[dict[str, Any]]:
        value = previous.get(name) if isinstance(previous, dict) else getattr(previous, name, None)
        return list(value or [])

    ids: set[str] = set()
    for ref in field("references"):
        resolved = ref.get("resolved") or {}
        entity_id = resolved.get(_RESOLVED_ID_KEYS.get(ref.get("reference_type") or "", ""))
        if entity_id:
            ids.add(entity_id)
    for ref in field("term_references"):
        if ref.get("term_id"):
            ids.add(ref["term_id"])
    for ref in field("file_references"):
        if ref.get("file_id"):
            ids.add(ref["file_id"])
    return ids


# Singleton instance
_validator: ReferenceValidator | None = None


def get_reference_validator() -> ReferenceValidator:
    """Get the singleton reference validator instance."""
    global _validator
    if _validator is None:
        _validator = ReferenceValidator()
    return _validator
