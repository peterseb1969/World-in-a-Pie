"""Referential integrity closure algorithm.

Scans exported entities for external references and fetches dependencies
until the export is self-contained.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from ..client import WIPClient
from .collector import EntityCollector

console = Console(stderr=True)

MAX_CLOSURE_ITERATIONS = 10


def compute_closure(
    client: WIPClient,
    primary_namespace: str,
    terminologies: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Compute referential integrity closure.

    Scans templates for external references and fetches dependencies
    until no new external refs are found.

    Returns:
        Tuple of (extra_terminologies, extra_terms, extra_templates, warnings)
    """
    # Build sets of known IDs
    known_terminology_ids = {t["terminology_id"] for t in terminologies}
    known_template_ids = {t["template_id"] for t in templates}

    extra_terminologies: list[dict] = []
    extra_terms: list[dict] = []
    extra_templates: list[dict] = []
    warnings: list[str] = []

    collector = EntityCollector(client, primary_namespace)

    for iteration in range(1, MAX_CLOSURE_ITERATIONS + 1):
        # Find external references in all templates (including newly added ones)
        all_templates = templates + extra_templates
        ext_term_ids, ext_tpl_ids = _scan_template_references(
            all_templates, known_terminology_ids, known_template_ids,
        )

        if not ext_term_ids and not ext_tpl_ids:
            console.print(f"  Closure complete after {iteration - 1} iteration(s)")
            break

        console.print(
            f"  Closure iteration {iteration}: "
            f"{len(ext_term_ids)} external terminologies, "
            f"{len(ext_tpl_ids)} external templates"
        )

        # Fetch external terminologies and their terms
        for term_id in ext_term_ids:
            terminology = collector.fetch_terminology_by_id(term_id)
            if terminology:
                terminology["_source"] = "closure"
                extra_terminologies.append(terminology)
                known_terminology_ids.add(term_id)

                # Fetch terms for this terminology
                terms_for_terminology = collector.fetch_terms(term_id)
                for term in terms_for_terminology:
                    term["_source"] = "closure"
                extra_terms.extend(terms_for_terminology)
            else:
                warnings.append(f"External terminology {term_id} not found")

        # Fetch external templates (all versions)
        for tpl_id in ext_tpl_ids:
            tpl_versions = collector.fetch_template_versions_by_id(tpl_id)
            if tpl_versions:
                for tpl in tpl_versions:
                    tpl["_source"] = "closure"
                extra_templates.extend(tpl_versions)
                known_template_ids.add(tpl_id)
            else:
                warnings.append(f"External template {tpl_id} not found")
    else:
        warnings.append(
            f"Closure did not converge after {MAX_CLOSURE_ITERATIONS} iterations"
        )

    # Check documents for external document references (warn only)
    _check_document_references(documents, known_template_ids, warnings)

    return extra_terminologies, extra_terms, extra_templates, warnings


def _scan_template_references(
    templates: list[dict[str, Any]],
    known_terminology_ids: set[str],
    known_template_ids: set[str],
) -> tuple[set[str], set[str]]:
    """Scan templates for external references not in known sets.

    Returns (external_terminology_ids, external_template_ids).
    """
    ext_terminology_ids: set[str] = set()
    ext_template_ids: set[str] = set()

    for tpl in templates:
        # extends → external template
        extends = tpl.get("extends")
        if extends and extends not in known_template_ids:
            ext_template_ids.add(extends)

        # Scan fields
        for field in tpl.get("fields", []):
            # terminology_ref
            tref = field.get("terminology_ref")
            if tref and tref not in known_terminology_ids:
                ext_terminology_ids.add(tref)

            # array_terminology_ref
            atref = field.get("array_terminology_ref")
            if atref and atref not in known_terminology_ids:
                ext_terminology_ids.add(atref)

            # template_ref
            tmpl_ref = field.get("template_ref")
            if tmpl_ref and tmpl_ref not in known_template_ids:
                ext_template_ids.add(tmpl_ref)

            # array_template_ref
            atmpl_ref = field.get("array_template_ref")
            if atmpl_ref and atmpl_ref not in known_template_ids:
                ext_template_ids.add(atmpl_ref)

            # target_templates[]
            for tt in field.get("target_templates") or []:
                if tt not in known_template_ids:
                    ext_template_ids.add(tt)

            # target_terminologies[]
            for tterm in field.get("target_terminologies") or []:
                if tterm not in known_terminology_ids:
                    ext_terminology_ids.add(tterm)

    return ext_terminology_ids, ext_template_ids


def _check_document_references(
    documents: list[dict[str, Any]],
    known_template_ids: set[str],
    warnings: list[str],
) -> None:
    """Check documents for external references (warnings only)."""
    external_doc_refs: set[str] = set()
    external_tpl_refs: set[str] = set()

    for doc in documents:
        # Check template_id
        tpl_id = doc.get("template_id")
        if tpl_id and tpl_id not in known_template_ids:
            external_tpl_refs.add(tpl_id)

        # Check references[]
        for ref in doc.get("references") or []:
            resolved = ref.get("resolved") or {}
            doc_id = resolved.get("document_id")
            if doc_id:
                external_doc_refs.add(doc_id)

    if external_tpl_refs:
        warnings.append(
            f"Documents reference {len(external_tpl_refs)} external template(s): "
            f"{', '.join(sorted(external_tpl_refs)[:5])}"
        )
    if external_doc_refs:
        warnings.append(
            f"Documents reference {len(external_doc_refs)} external document(s) "
            f"(not followed to avoid expanding to entire dataset)"
        )
