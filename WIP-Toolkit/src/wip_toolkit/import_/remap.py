"""ID remapping engine for fresh-mode import.

Rewrites all internal references in templates and documents
using old→new ID maps.
"""

from __future__ import annotations

from typing import Any


class IDRemapper:
    """Manages old→new ID mappings and rewrites entity references."""

    def __init__(self) -> None:
        self.terminology_map: dict[str, str] = {}
        self.term_map: dict[str, str] = {}
        self.template_map: dict[str, str] = {}
        self.document_map: dict[str, str] = {}
        self.file_map: dict[str, str] = {}

    @property
    def total_mappings(self) -> int:
        return (
            len(self.terminology_map)
            + len(self.term_map)
            + len(self.template_map)
            + len(self.document_map)
            + len(self.file_map)
        )

    def add_terminology_mapping(self, old_id: str, new_id: str) -> None:
        self.terminology_map[old_id] = new_id

    def add_term_mapping(self, old_id: str, new_id: str) -> None:
        self.term_map[old_id] = new_id

    def add_template_mapping(self, old_id: str, new_id: str) -> None:
        self.template_map[old_id] = new_id

    def add_document_mapping(self, old_id: str, new_id: str) -> None:
        self.document_map[old_id] = new_id

    def add_file_mapping(self, old_id: str, new_id: str) -> None:
        self.file_map[old_id] = new_id

    def remap_template(self, template: dict[str, Any]) -> dict[str, Any]:
        """Remap all references in a template.

        Fields remapped:
        - extends → template map
        - extends_version → pass through (version number, not ID)
        - fields[].terminology_ref → terminology map
        - fields[].array_terminology_ref → terminology map
        - fields[].template_ref → template map
        - fields[].array_template_ref → template map
        - fields[].target_templates[] → template map
        - fields[].target_terminologies[] → terminology map
        """
        result = dict(template)

        # Remap extends
        if result.get("extends"):
            result["extends"] = self.template_map.get(
                result["extends"], result["extends"]
            )

        # Remap fields
        if result.get("fields"):
            result["fields"] = [
                self._remap_field(f) for f in result["fields"]
            ]

        return result

    def _remap_field(self, field: dict[str, Any]) -> dict[str, Any]:
        """Remap references in a single field definition."""
        result = dict(field)

        # terminology_ref
        if result.get("terminology_ref"):
            result["terminology_ref"] = self.terminology_map.get(
                result["terminology_ref"], result["terminology_ref"]
            )

        # array_terminology_ref
        if result.get("array_terminology_ref"):
            result["array_terminology_ref"] = self.terminology_map.get(
                result["array_terminology_ref"], result["array_terminology_ref"]
            )

        # template_ref
        if result.get("template_ref"):
            result["template_ref"] = self.template_map.get(
                result["template_ref"], result["template_ref"]
            )

        # array_template_ref
        if result.get("array_template_ref"):
            result["array_template_ref"] = self.template_map.get(
                result["array_template_ref"], result["array_template_ref"]
            )

        # target_templates[]
        if result.get("target_templates"):
            result["target_templates"] = [
                self.template_map.get(t, t)
                for t in result["target_templates"]
            ]

        # target_terminologies[]
        if result.get("target_terminologies"):
            result["target_terminologies"] = [
                self.terminology_map.get(t, t)
                for t in result["target_terminologies"]
            ]

        return result

    def remap_document(self, document: dict[str, Any]) -> dict[str, Any]:
        """Remap all references in a document.

        Fields remapped:
        - template_id → template map
        - term_references[].term_id → term map
        - term_references[].terminology_ref → terminology map
        - references[].resolved.document_id → document map
        - references[].resolved.template_id → template map
        - references[].resolved.identity_hash → pass through
        - file_references[].file_id → file map
        """
        result = dict(document)

        # Remap template_id
        if result.get("template_id"):
            result["template_id"] = self.template_map.get(
                result["template_id"], result["template_id"]
            )

        # Remap term_references
        if result.get("term_references"):
            result["term_references"] = [
                self._remap_term_reference(tr)
                for tr in result["term_references"]
            ]

        # Remap references
        if result.get("references"):
            result["references"] = [
                self._remap_document_reference(ref)
                for ref in result["references"]
            ]

        # Remap file_references
        if result.get("file_references"):
            result["file_references"] = [
                self._remap_file_reference(fr)
                for fr in result["file_references"]
            ]

        return result

    def _remap_term_reference(self, term_ref: dict[str, Any]) -> dict[str, Any]:
        """Remap a single term reference."""
        result = dict(term_ref)
        if result.get("term_id"):
            result["term_id"] = self.term_map.get(
                result["term_id"], result["term_id"]
            )
        if result.get("terminology_ref"):
            result["terminology_ref"] = self.terminology_map.get(
                result["terminology_ref"], result["terminology_ref"]
            )
        return result

    def _remap_document_reference(self, ref: dict[str, Any]) -> dict[str, Any]:
        """Remap a single document reference."""
        result = dict(ref)
        resolved = result.get("resolved")
        if resolved:
            resolved = dict(resolved)
            if resolved.get("document_id"):
                resolved["document_id"] = self.document_map.get(
                    resolved["document_id"], resolved["document_id"]
                )
            if resolved.get("template_id"):
                resolved["template_id"] = self.template_map.get(
                    resolved["template_id"], resolved["template_id"]
                )
            # identity_hash passes through unchanged
            result["resolved"] = resolved
        return result

    def _remap_file_reference(self, file_ref: dict[str, Any]) -> dict[str, Any]:
        """Remap a single file reference."""
        result = dict(file_ref)
        if result.get("file_id"):
            result["file_id"] = self.file_map.get(
                result["file_id"], result["file_id"]
            )
        return result

    def all_synonym_pairs(self) -> list[tuple[str, str, str]]:
        """Return all (old_id, new_id, entity_type) pairs for synonym registration."""
        pairs: list[tuple[str, str, str]] = []
        for old, new in self.terminology_map.items():
            pairs.append((old, new, "terminologies"))
        for old, new in self.term_map.items():
            pairs.append((old, new, "terms"))
        for old, new in self.template_map.items():
            pairs.append((old, new, "templates"))
        for old, new in self.document_map.items():
            pairs.append((old, new, "documents"))
        for old, new in self.file_map.items():
            pairs.append((old, new, "files"))
        return pairs
