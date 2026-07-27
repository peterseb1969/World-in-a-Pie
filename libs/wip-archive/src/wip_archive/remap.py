"""ID remapping engine for fresh-mode import.

Rewrites all internal references in templates and documents
using old→new ID maps.
"""

from __future__ import annotations

from typing import Any


class IDRemapper:
    """Manages old→new ID mappings and rewrites entity references.

    ``namespace_map`` ({source: target}) rewrites the namespace fields
    embedded in reference snapshots. After a fresh restore NO data may point
    to anything from the original namespaces — history lives in the archive,
    not in the restored rows. A namespace absent from the map passes through
    unchanged, which is correct: a reference into a namespace OUTSIDE the
    archive (e.g. a shared 'wip' term) names an entity that was not
    re-minted, so its snapshot still describes it.
    """

    def __init__(self, namespace_map: dict[str, str] | None = None) -> None:
        self.terminology_map: dict[str, str] = {}
        self.term_map: dict[str, str] = {}
        self.template_map: dict[str, str] = {}
        self.document_map: dict[str, str] = {}
        self.file_map: dict[str, str] = {}
        self.namespace_map: dict[str, str] = dict(namespace_map or {})

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

    def remap_term(self, term: dict[str, Any]) -> dict[str, Any]:
        """Remap the terminology a term belongs to.

        A term's only outward reference is its parent terminology, but it is
        load-bearing: leave it pointing at the source install's terminology ID
        and the imported term is orphaned.
        """
        result = dict(term)
        if result.get("terminology_id"):
            result["terminology_id"] = self.terminology_map.get(
                result["terminology_id"], result["terminology_id"]
            )
        return result

    def remap_term_relation(self, relation: dict[str, Any]) -> dict[str, Any]:
        """Remap both endpoints of an ontology relation, and its type.

        ``relation_type`` holds a term ID *or* a plain value (e.g. ``is_a``)
        depending on how the relation was created, so it is looked up in the
        term map and passes through untouched when it is a value.
        """
        result = dict(relation)
        for field in ("source_term_id", "target_term_id", "relation_type"):
            if result.get(field):
                result[field] = self.term_map.get(result[field], result[field])
        for field in ("source_terminology_id", "target_terminology_id"):
            if result.get(field):
                result[field] = self.terminology_map.get(
                    result[field], result[field]
                )
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

        # Remap IDs embedded in data field values (file IDs, document IDs)
        if result.get("data"):
            result["data"] = self._remap_data_ids(result["data"])

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
        """Remap a single document reference.

        The whole snapshot must describe the re-minted entity: ids through
        the id maps, the denormalized namespace through the namespace map,
        and a lookup_value that was a canonical id follows its entity — an
        old id kept anywhere is a pointer into the original namespace
        (CASE-743: 'fresh means fresh'). A BARE human-readable lookup_value
        stays: it is namespace-agnostic text that resolves in the new
        context. A QUALIFIED one ('<ns>:<value>') is not namespace-agnostic —
        it names a namespace explicitly, so it follows the namespace and id
        maps like any other reference string.
        """
        result = dict(ref)
        lookup = result.get("lookup_value")
        if isinstance(lookup, str):
            result["lookup_value"] = self._remap_reference_string(
                lookup, (self.document_map, self.template_map, self.term_map)
            )
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
            if resolved.get("namespace"):
                resolved["namespace"] = self.namespace_map.get(
                    resolved["namespace"], resolved["namespace"]
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

    def _remap_data_ids(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remap any known IDs (file, document, template, term) in data values.

        The walk is recursive on purpose: a reference id can sit at any depth
        of the payload — an array-of-references field, an object field, an
        array of objects — and every position must follow the same id maps as
        a top-level scalar. A string absent from every map passes through
        unchanged, which keeps the pass-through rule for targets outside the
        archive: they were never re-minted, so no map knows them. Qualified
        strings ('<ns>:<value>') whose namespace half is being remapped are
        rewritten as a whole — see _remap_reference_string.
        """
        all_maps = (self.file_map, self.document_map, self.template_map, self.term_map)

        def walk(value: Any) -> Any:
            if isinstance(value, str):
                return self._remap_reference_string(value, all_maps)
            if isinstance(value, list):
                return [walk(item) for item in value]
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            return value

        return {key: walk(value) for key, value in data.items()}

    def _remap_reference_string(
        self,
        value: str,
        id_maps: tuple[dict[str, str], ...],
    ) -> str:
        """Rewrite one reference string through the id and namespace maps.

        Two forms, matching how the platform stores reference values:

        - BARE ('<id-or-value>'): exact lookup in the id maps; unmapped
          strings pass through (targets outside the archive were never
          re-minted, so no map knows them).
        - QUALIFIED ('<ns>:<rest>', split on the FIRST colon — the same
          purely syntactic split as wip_auth.split_qualified_value): if the
          namespace half names a source namespace this restore re-mints, the
          whole string is rewritten — namespace half through namespace_map,
          rest through the id maps, falling back to the rest unchanged (a
          fresh restore re-mints ids, not values, so value-form rests and the
          'terminology:value' tail of a 3-part term ref are portable as-is).
          The result keeps the qualified SHAPE: the ref may live in a
          different target than it points into, and a bare value resolves
          own-namespace only, so collapsing it would change resolution.
          A namespace half OUTSIDE the map passes the whole string through —
          that entity was not re-minted, so the string still describes it.

        A free-text string that happens to begin '<source-ns>:' is rewritten
        too; that is deliberate — the leak-sweep contract defines any
        source-namespace string in a restored row as a leak, the same
        acceptance class as the exact-match rewrite of a text field that
        equals an old id.
        """
        for m in id_maps:
            if value in m:
                return m[value]
        if ":" in value:
            ns_prefix, rest = value.split(":", 1)
            new_ns = self.namespace_map.get(ns_prefix)
            if new_ns is not None:
                for m in id_maps:
                    if rest in m:
                        rest = m[rest]
                        break
                return f"{new_ns}:{rest}"
        return value
