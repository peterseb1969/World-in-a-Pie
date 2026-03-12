"""Import/Export service for terminologies and terms."""

import csv
import io
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from ..models.terminology import Terminology, TerminologyMetadata
from ..models.term import Term, TermTranslation
from ..models.term_relationship import TermRelationship
from ..models.api_models import (
    CreateTerminologyRequest,
    CreateTermRequest,
    TerminologyResponse,
    TermResponse,
    ExportTerminologyResponse,
    BulkResultItem,
    CreateRelationshipRequest,
)
from .terminology_service import TerminologyService
from .ontology_service import OntologyService

logger = logging.getLogger(__name__)


class ImportExportService:
    """Service for importing and exporting terminologies."""

    # =========================================================================
    # EXPORT
    # =========================================================================

    @staticmethod
    async def export_terminology(
        terminology_id: Optional[str] = None,
        terminology_value: Optional[str] = None,
        format: str = "json",
        include_metadata: bool = True,
        include_inactive: bool = False,
        include_relationships: bool = False,
        languages: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """
        Export a terminology with all its terms.

        Args:
            terminology_id: Terminology ID
            terminology_value: Terminology value (alternative)
            format: Export format (json, csv)
            include_metadata: Include metadata in export
            include_inactive: Include inactive/deprecated terms
            include_relationships: Include ontology relationships
            languages: Languages to include for translations

        Returns:
            Export data in requested format
        """
        # Get terminology
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif terminology_value:
            terminology = await Terminology.find_one({"value": terminology_value})
        else:
            raise ValueError("Must provide terminology_id or terminology_value")

        if not terminology:
            raise ValueError("Terminology not found")

        # Get terms
        query = {"terminology_id": terminology.terminology_id}
        if not include_inactive:
            query["status"] = "active"

        terms = await Term.find(query).sort("sort_order").to_list()

        # Filter translations if languages specified
        if languages:
            for term in terms:
                term.translations = [
                    t for t in term.translations
                    if t.language in languages
                ]

        # Get relationships if requested
        relationships: list[TermRelationship] = []
        if include_relationships and format != "csv":
            rel_query: dict[str, Any] = {
                "namespace": terminology.namespace,
                "source_terminology_id": terminology.terminology_id,
            }
            if not include_inactive:
                rel_query["status"] = "active"
            relationships = await TermRelationship.find(rel_query).to_list()

            # Also get relationships where target is in this terminology
            # but source is from another (cross-terminology links)
            cross_query: dict[str, Any] = {
                "namespace": terminology.namespace,
                "target_terminology_id": terminology.terminology_id,
                "source_terminology_id": {"$ne": terminology.terminology_id},
            }
            if not include_inactive:
                cross_query["status"] = "active"
            cross_rels = await TermRelationship.find(cross_query).to_list()
            relationships.extend(cross_rels)

        if format == "csv":
            return ImportExportService._export_csv(terminology, terms, include_metadata)
        else:
            return ImportExportService._export_json(
                terminology, terms, include_metadata, relationships
            )

    @staticmethod
    def _export_json(
        terminology: Terminology,
        terms: list[Term],
        include_metadata: bool,
        relationships: Optional[list["TermRelationship"]] = None,
    ) -> dict[str, Any]:
        """Export as JSON."""
        # Build term_id → value lookup for relationship denormalization
        term_id_to_value: dict[str, str] = {}
        term_data = []
        for t in terms:
            term_id_to_value[t.term_id] = t.value
            term_dict: dict[str, Any] = {
                "value": t.value,
                "label": t.label or t.value,
                "description": t.description,
                "sort_order": t.sort_order,
                "status": t.status,
            }
            if t.aliases:
                term_dict["aliases"] = t.aliases
            if t.parent_term_id:
                term_dict["parent_term_id"] = t.parent_term_id
            if t.translations:
                term_dict["translations"] = [
                    {"language": tr.language, "label": tr.label, "description": tr.description}
                    for tr in t.translations
                ]
            if include_metadata and t.metadata:
                term_dict["metadata"] = t.metadata
            if t.status == "deprecated":
                term_dict["deprecated_reason"] = t.deprecated_reason
                term_dict["replaced_by_term_id"] = t.replaced_by_term_id

            term_data.append(term_dict)

        result: dict[str, Any] = {
            "terminology": {
                "value": terminology.value,
                "label": terminology.label,
                "description": terminology.description,
                "case_sensitive": terminology.case_sensitive,
                "allow_multiple": terminology.allow_multiple,
                "extensible": terminology.extensible,
            },
            "terms": term_data,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "format": "json",
            "version": "2.0"
        }

        if include_metadata:
            result["terminology"]["metadata"] = {
                "source": terminology.metadata.source,
                "source_url": terminology.metadata.source_url,
                "version": terminology.metadata.version,
                "language": terminology.metadata.language,
                "custom": terminology.metadata.custom
            }

        # Include relationships if provided
        if relationships:
            rel_data = []
            for r in relationships:
                rel_dict: dict[str, Any] = {
                    "source_term_value": term_id_to_value.get(r.source_term_id, r.source_term_id),
                    "target_term_value": term_id_to_value.get(r.target_term_id, r.target_term_id),
                    "relationship_type": r.relationship_type,
                }
                if r.metadata:
                    rel_dict["metadata"] = r.metadata
                if r.source_terminology_id != terminology.terminology_id:
                    rel_dict["source_terminology_id"] = r.source_terminology_id
                if r.target_terminology_id != terminology.terminology_id:
                    rel_dict["target_terminology_id"] = r.target_terminology_id
                rel_data.append(rel_dict)
            result["relationships"] = rel_data

        return result

    @staticmethod
    def _export_csv(
        terminology: Terminology,
        terms: list[Term],
        include_metadata: bool
    ) -> dict[str, Any]:
        """Export as CSV."""
        output = io.StringIO()

        # Define columns
        columns = ["value", "label", "description", "sort_order", "status"]
        if include_metadata:
            columns.append("metadata")

        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()

        for t in terms:
            row = {
                "value": t.value,
                "label": t.label or t.value,
                "description": t.description or "",
                "sort_order": t.sort_order,
                "status": t.status,
            }
            if include_metadata:
                row["metadata"] = json.dumps(t.metadata) if t.metadata else ""

            writer.writerow(row)

        return {
            "terminology": {
                "value": terminology.value,
                "label": terminology.label,
            },
            "csv_content": output.getvalue(),
            "export_date": datetime.now(timezone.utc).isoformat(),
            "format": "csv"
        }

    @staticmethod
    async def export_all_terminologies(
        format: str = "json",
        include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        """Export all terminologies."""
        query = {} if include_inactive else {"status": "active"}
        terminologies = await Terminology.find(query).to_list()

        results = []
        for t in terminologies:
            export = await ImportExportService.export_terminology(
                terminology_id=t.terminology_id,
                format=format,
                include_inactive=include_inactive
            )
            results.append(export)

        return results

    # =========================================================================
    # IMPORT
    # =========================================================================

    @staticmethod
    async def import_terminology(
        data: dict[str, Any],
        format: str = "json",
        options: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Import a terminology with terms.

        Args:
            data: Import data
            format: Data format (json, csv)
            options: Import options
                - skip_duplicates: Skip terms that already exist
                - update_existing: Update existing terms
                - created_by: User performing import

        Returns:
            Import results
        """
        options = options or {}
        skip_duplicates = options.get("skip_duplicates", True)
        update_existing = options.get("update_existing", False)
        created_by = options.get("created_by")

        if format == "csv":
            return await ImportExportService._import_csv(data, options)

        # JSON import
        terminology_data = data.get("terminology")
        if not terminology_data:
            raise ValueError("Missing 'terminology' field in import data")

        if not terminology_data.get("value"):
            raise ValueError("Missing 'terminology.value' field in import data")

        if not terminology_data.get("label"):
            raise ValueError("Missing 'terminology.label' field in import data")

        terms_data = data.get("terms", [])

        # Check if terminology exists
        existing_terminology = await Terminology.find_one({"value": terminology_data.get("value")})

        if existing_terminology:
            if not update_existing:
                terminology_id = existing_terminology.terminology_id
                terminology_status = "exists"
            else:
                # Update existing terminology
                # TODO: Implement update logic
                terminology_id = existing_terminology.terminology_id
                terminology_status = "updated"
        else:
            # Create new terminology
            metadata = terminology_data.get("metadata", {})
            create_req = CreateTerminologyRequest(
                value=terminology_data["value"],
                label=terminology_data["label"],
                description=terminology_data.get("description"),
                namespace=terminology_data.get("namespace", "wip"),
                case_sensitive=terminology_data.get("case_sensitive", False),
                allow_multiple=terminology_data.get("allow_multiple", False),
                extensible=terminology_data.get("extensible", False),
                metadata=TerminologyMetadata(**metadata) if metadata else None,
                created_by=created_by
            )
            terminology_response = await TerminologyService.create_terminology(create_req)
            terminology_id = terminology_response.terminology_id
            terminology_status = "created"

        # Build CreateTermRequest objects for batch operation
        term_requests = []
        for i, term_data in enumerate(terms_data):
            translations = [
                TermTranslation(**tr)
                for tr in term_data.get("translations", [])
            ]
            term_requests.append(CreateTermRequest(
                value=term_data["value"],
                aliases=term_data.get("aliases", []),
                label=term_data.get("label"),
                description=term_data.get("description"),
                sort_order=term_data.get("sort_order", i),
                parent_term_id=term_data.get("parent_term_id"),
                translations=translations,
                metadata=term_data.get("metadata", {}),
                created_by=created_by
            ))

        # Delegate to batch method for efficient bulk import
        # Get batch size options (for large imports)
        batch_size = options.get("batch_size", 1000)
        registry_batch_size = options.get("registry_batch_size", 100)

        term_results = await TerminologyService.create_terms_bulk(
            terminology_id=terminology_id,
            terms=term_requests,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
            batch_size=batch_size,
            registry_batch_size=registry_batch_size,
        )

        created_count = sum(1 for r in term_results if r.status == "created")
        skipped_count = sum(1 for r in term_results if r.status == "skipped")
        error_count = sum(1 for r in term_results if r.status == "error")

        # Get terminology label (from existing or from import data)
        terminology_label = (
            existing_terminology.label if existing_terminology
            else terminology_data.get("label")
        )

        # Import relationships if present
        relationships_data = data.get("relationships", [])
        rel_result = None
        if relationships_data:
            namespace = terminology_data.get("namespace", "wip")
            rel_result = await ImportExportService._import_relationships(
                relationships_data,
                terminology_id=terminology_id,
                namespace=namespace,
                term_results=term_results,
                options=options,
            )

        result: dict[str, Any] = {
            "terminology": {
                "terminology_id": terminology_id,
                "value": terminology_data.get("value"),
                "label": terminology_label,
                "status": terminology_status
            },
            "terms_result": {
                "results": [r.model_dump() for r in term_results],
                "total": len(terms_data),
                "succeeded": created_count,
                "skipped": skipped_count,
                "failed": error_count
            }
        }
        if rel_result:
            result["relationships_result"] = rel_result
        return result

    @staticmethod
    async def _import_csv(
        data: dict[str, Any],
        options: dict[str, Any]
    ) -> dict[str, Any]:
        """Import from CSV format."""
        terminology_value = data.get("terminology_value")
        terminology_label = data.get("terminology_label", terminology_value)
        csv_content = data.get("csv_content", "")
        created_by = options.get("created_by")

        if not terminology_value:
            raise ValueError("terminology_value is required for CSV import")

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_content))
        terms_data = []

        for row in reader:
            term = {
                "value": row.get("value", "").strip(),
                "label": row.get("label", "").strip() or None,
                "description": row.get("description", "").strip() or None,
                "sort_order": int(row.get("sort_order", 0) or 0),
            }
            if row.get("metadata"):
                try:
                    term["metadata"] = json.loads(row["metadata"])
                except json.JSONDecodeError:
                    pass

            if term["value"]:
                terms_data.append(term)

        # Convert to JSON format and use JSON import
        json_data = {
            "terminology": {
                "value": terminology_value,
                "label": terminology_label,
            },
            "terms": terms_data
        }

        return await ImportExportService.import_terminology(json_data, "json", options)

    @staticmethod
    async def _import_relationships(
        relationships_data: list[dict[str, Any]],
        terminology_id: str,
        namespace: str,
        term_results: list[BulkResultItem],
        options: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Import relationships from export format (source_term_value/target_term_value).

        Resolves term values to IDs and creates relationships in batches.
        """
        options = options or {}
        relationship_batch_size = options.get("relationship_batch_size", 500)

        # Build value→term_id from creation results
        value_to_id: dict[str, str] = {}
        for r in term_results:
            if r.id and r.value:
                value_to_id[r.value] = r.id

        # Resolve skipped terms from DB
        if len(value_to_id) < len(term_results):
            async for term in Term.find({
                "namespace": namespace,
                "terminology_id": terminology_id,
            }):
                value_to_id[term.value] = term.term_id

        # Ensure relationship types exist
        rel_types = {r["relationship_type"] for r in relationships_data if r.get("relationship_type")}
        await ImportExportService._ensure_relationship_types(rel_types)

        # Build and batch-create relationships
        rel_created = 0
        rel_skipped = 0
        rel_errors = 0
        rel_error_samples: list[str] = []

        for i in range(0, len(relationships_data), relationship_batch_size):
            batch = relationships_data[i:i + relationship_batch_size]
            rel_requests: list[CreateRelationshipRequest] = []
            for rd in batch:
                src_id = value_to_id.get(rd.get("source_term_value", ""))
                tgt_id = value_to_id.get(rd.get("target_term_value", ""))
                if src_id and tgt_id:
                    rel_requests.append(CreateRelationshipRequest(
                        source_term_id=src_id,
                        target_term_id=tgt_id,
                        relationship_type=rd["relationship_type"],
                        metadata=rd.get("metadata"),
                    ))

            if rel_requests:
                results = await OntologyService.create_relationships(namespace, rel_requests)
                for r in results:
                    if r.status == "created":
                        rel_created += 1
                    elif r.status == "skipped":
                        rel_skipped += 1
                    else:
                        rel_errors += 1
                        if len(rel_error_samples) < 5 and r.error:
                            rel_error_samples.append(r.error)

        return {
            "total": len(relationships_data),
            "created": rel_created,
            "skipped": rel_skipped,
            "errors": rel_errors,
            "error_samples": rel_error_samples[:5] if rel_error_samples else [],
        }

    @staticmethod
    async def import_from_url(
        url: str,
        format: str = "json",
        options: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Import terminology from a URL.

        Args:
            url: URL to fetch data from
            format: Expected format (json, csv)
            options: Import options

        Returns:
            Import results
        """
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()

            if format == "json":
                data = response.json()
            else:
                # CSV - wrap in expected structure
                data = {
                    "csv_content": response.text,
                    "terminology_value": options.get("terminology_value") if options else None,
                    "terminology_label": options.get("terminology_label") if options else None,
                }

        return await ImportExportService.import_terminology(data, format, options)

    # =========================================================================
    # OBO GRAPH JSON IMPORT
    # =========================================================================

    # Predicate URI → WIP relationship type
    OBO_PREDICATE_MAP: dict[str, str] = {
        "is_a": "is_a",
        "http://purl.obolibrary.org/obo/BFO_0000050": "part_of",
        "http://purl.obolibrary.org/obo/BFO_0000051": "has_part",
        "http://purl.obolibrary.org/obo/RO_0002211": "regulates",
        "http://purl.obolibrary.org/obo/RO_0002212": "negatively_regulates",
        "http://purl.obolibrary.org/obo/RO_0002213": "positively_regulates",
        "http://purl.obolibrary.org/obo/RO_0002215": "capable_of",
        "http://purl.obolibrary.org/obo/RO_0002216": "capable_of_part_of",
        "http://purl.obolibrary.org/obo/RO_0002233": "has_input",
        "http://purl.obolibrary.org/obo/RO_0002234": "has_output",
        "http://purl.obolibrary.org/obo/RO_0002331": "involved_in",
        "http://purl.obolibrary.org/obo/RO_0002332": "regulates_activity_of",
    }
    OBO_SKIP_PREDICATES = {"subPropertyOf"}

    @staticmethod
    def _uri_to_value(uri: str) -> str:
        """Convert OBO URI to compact value: HP_0000001 → HP:0000001."""
        fragment = uri.rsplit("/", 1)[-1]
        return fragment.replace("_", ":", 1)

    @staticmethod
    def _detect_prefix(graph: dict) -> str | None:
        """Auto-detect OBO prefix from graph ID."""
        graph_id = graph.get("id", "")
        filename = graph_id.rsplit("/", 1)[-1]
        base = filename.split(".")[0].split("-")[0].upper()
        return base if base else None

    @classmethod
    def _map_predicate(cls, pred: str) -> str | None:
        """Map OBO predicate to WIP relationship type."""
        if pred in cls.OBO_SKIP_PREDICATES:
            return None
        if pred in cls.OBO_PREDICATE_MAP:
            return cls.OBO_PREDICATE_MAP[pred]
        if "/" in pred:
            fragment = pred.rsplit("/", 1)[-1]
            return fragment.replace("_", ":", 1)
        return pred

    @classmethod
    def _parse_obo_graph(
        cls,
        data: dict,
        prefix_filter: str | None = None,
        include_deprecated: bool = False,
        max_synonyms: int = 10,
    ) -> dict[str, Any]:
        """Parse OBO Graph JSON into nodes and edges."""
        graph = data["graphs"][0]
        meta = graph.get("meta", {})

        if not prefix_filter:
            prefix_filter = cls._detect_prefix(graph)

        uri_prefix = f"http://purl.obolibrary.org/obo/{prefix_filter}_" if prefix_filter else None

        # Ontology metadata
        ontology_meta: dict[str, str] = {}
        for bpv in meta.get("basicPropertyValues", []):
            pred = bpv.get("pred", "")
            val = bpv.get("val", "")
            if "title" in pred:
                ontology_meta["title"] = val
            elif "description" in pred:
                ontology_meta["description"] = val
            elif "versionInfo" in pred:
                ontology_meta["version"] = val

        # Parse nodes
        nodes: dict[str, dict] = {}
        for n in graph.get("nodes", []):
            if n.get("type") != "CLASS":
                continue
            uri = n["id"]
            if uri_prefix and not uri.startswith(uri_prefix):
                continue
            node_meta = n.get("meta", {})
            if node_meta.get("deprecated", False) and not include_deprecated:
                continue

            value = cls._uri_to_value(uri)
            label = n.get("lbl", value)
            definition = node_meta.get("definition", {})
            description = definition.get("val") if definition else None
            aliases = [s["val"] for s in node_meta.get("synonyms", []) if s.get("val")][:max_synonyms]
            xrefs = [x.get("val") for x in node_meta.get("xrefs", []) if x.get("val")]

            term_metadata: dict[str, Any] = {}
            if xrefs:
                term_metadata["xrefs"] = xrefs

            nodes[uri] = {
                "value": value,
                "label": label,
                "description": description,
                "aliases": aliases,
                "metadata": term_metadata,
            }

        # Parse edges
        edges: list[dict] = []
        pred_counts: Counter = Counter()
        for e in graph.get("edges", []):
            sub, obj, pred = e.get("sub"), e.get("obj"), e.get("pred")
            if sub not in nodes or obj not in nodes:
                continue
            rel_type = cls._map_predicate(pred)
            if rel_type is None:
                continue
            pred_counts[rel_type] += 1
            edges.append({
                "source_uri": sub,
                "target_uri": obj,
                "relationship_type": rel_type,
            })

        return {
            "ontology_meta": ontology_meta,
            "prefix": prefix_filter,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "nodes_parsed": len(nodes),
                "edges_parsed": len(edges),
                "predicate_distribution": dict(pred_counts.most_common()),
            },
        }

    @staticmethod
    async def _ensure_relationship_types(needed_types: set[str]) -> None:
        """
        Ensure all needed relationship types exist in _ONTOLOGY_RELATIONSHIP_TYPES.

        Auto-creates any missing types as new terms in the system terminology.
        Invalidates the OntologyService cache after adding new types.
        """
        from .system_terminologies import RELATIONSHIP_TYPES_TERMINOLOGY_VALUE

        valid_types = await OntologyService.get_valid_relationship_types()
        missing = needed_types - set(valid_types.keys())

        if not missing:
            return

        logger.info(f"Auto-creating {len(missing)} missing relationship types: {missing}")

        # Find the _ONTOLOGY_RELATIONSHIP_TYPES terminology
        terminology = await Terminology.find_one({
            "value": RELATIONSHIP_TYPES_TERMINOLOGY_VALUE,
        })
        if not terminology:
            logger.error(
                f"Cannot auto-create relationship types: "
                f"{RELATIONSHIP_TYPES_TERMINOLOGY_VALUE} terminology not found"
            )
            return

        # Get current max sort_order
        max_sort = 0
        async for term in Term.find({
            "terminology_id": terminology.terminology_id,
        }).sort("-sort_order").limit(1):
            max_sort = term.sort_order

        # Create missing types as terms
        term_requests = []
        for i, rel_type in enumerate(sorted(missing)):
            # Convert value to a human-readable label
            label = rel_type.replace("_", " ").replace(":", " ").title()
            term_requests.append(CreateTermRequest(
                value=rel_type,
                label=label,
                description=f"Auto-created from ontology import",
                sort_order=max_sort + i + 1,
                created_by="system:ontology-import",
            ))

        if term_requests:
            results = await TerminologyService.create_terms_bulk(
                terminology_id=terminology.terminology_id,
                terms=term_requests,
                skip_duplicates=True,
            )
            created = sum(1 for r in results if r.status == "created")
            logger.info(f"Created {created} new relationship types")

            # Invalidate cache so create_relationships picks up the new types
            OntologyService.invalidate_relationship_type_cache()

    @staticmethod
    async def import_ontology(
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Import an OBO Graph JSON ontology.

        Args:
            data: OBO Graph JSON data (with "graphs" array)
            options: Import options (terminology_value, terminology_label,
                     prefix_filter, include_deprecated, max_synonyms,
                     batch_size, registry_batch_size, relationship_batch_size,
                     namespace, skip_duplicates, update_existing, created_by)

        Returns:
            Import summary with terminology, term, and relationship stats.
        """
        t0 = time.perf_counter()

        namespace = options.get("namespace", "wip")
        created_by = options.get("created_by")
        batch_size = options.get("batch_size", 1000)
        registry_batch_size = options.get("registry_batch_size", 50)
        relationship_batch_size = options.get("relationship_batch_size", 500)
        skip_duplicates = options.get("skip_duplicates", True)
        update_existing = options.get("update_existing", False)

        # Parse
        parsed = ImportExportService._parse_obo_graph(
            data,
            prefix_filter=options.get("prefix_filter"),
            include_deprecated=options.get("include_deprecated", False),
            max_synonyms=options.get("max_synonyms", 10),
        )

        nodes = parsed["nodes"]
        edges = parsed["edges"]
        meta = parsed["ontology_meta"]

        terminology_value = options.get("terminology_value") or parsed["prefix"]
        terminology_label = options.get("terminology_label") or meta.get("title") or terminology_value

        if not terminology_value:
            raise ValueError("Could not auto-detect terminology value. Provide terminology_value.")

        logger.info(
            f"Parsed OBO graph: {len(nodes)} nodes, {len(edges)} edges, "
            f"prefix={parsed['prefix']}"
        )

        # Create or find terminology
        existing = await Terminology.find_one({
            "namespace": namespace,
            "value": terminology_value,
        })

        if existing:
            terminology_id = existing.terminology_id
            terminology_status = "exists"
        else:
            create_req = CreateTerminologyRequest(
                value=terminology_value,
                label=terminology_label,
                description=meta.get("description"),
                namespace=namespace,
                metadata=TerminologyMetadata(
                    source=meta.get("title", terminology_value),
                    version=meta.get("version"),
                    custom={"format": "OBO Graph JSON"},
                ),
                created_by=created_by,
            )
            terminology_response = await TerminologyService.create_terminology(create_req)
            terminology_id = terminology_response.terminology_id
            terminology_status = "created"

        # Import terms in batches
        term_requests = []
        for info in nodes.values():
            term_requests.append(CreateTermRequest(
                value=info["value"],
                label=info["label"],
                description=info["description"],
                aliases=info["aliases"],
                metadata=info["metadata"],
                created_by=created_by,
            ))

        term_results = await TerminologyService.create_terms_bulk(
            terminology_id=terminology_id,
            terms=term_requests,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
            batch_size=batch_size,
            registry_batch_size=registry_batch_size,
        )

        # Build value→term_id mapping
        value_to_id: dict[str, str] = {}
        for r in term_results:
            if r.id:
                value_to_id[r.value] = r.id

        # Resolve IDs for skipped terms
        if len(value_to_id) < len(nodes):
            async for term in Term.find({
                "namespace": namespace,
                "terminology_id": terminology_id,
            }):
                value_to_id[term.value] = term.term_id

        # Build URI→term_id mapping and import relationships
        uri_to_id: dict[str, str] = {}
        for uri, info in nodes.items():
            tid = value_to_id.get(info["value"])
            if tid:
                uri_to_id[uri] = tid

        logger.info(
            f"ID mappings: value_to_id={len(value_to_id)}, "
            f"uri_to_id={len(uri_to_id)}, nodes={len(nodes)}, edges={len(edges)}"
        )

        # Ensure all relationship types exist in _ONTOLOGY_RELATIONSHIP_TYPES
        edge_rel_types = {e["relationship_type"] for e in edges}
        await ImportExportService._ensure_relationship_types(edge_rel_types)

        rel_created = 0
        rel_skipped = 0
        rel_errors = 0
        rel_error_samples: list[str] = []

        for i in range(0, len(edges), relationship_batch_size):
            batch_edges = edges[i:i + relationship_batch_size]
            rel_requests: list[CreateRelationshipRequest] = []
            for e in batch_edges:
                src_id = uri_to_id.get(e["source_uri"])
                tgt_id = uri_to_id.get(e["target_uri"])
                if src_id and tgt_id:
                    rel_requests.append(CreateRelationshipRequest(
                        source_term_id=src_id,
                        target_term_id=tgt_id,
                        relationship_type=e["relationship_type"],
                    ))

            if rel_requests:
                results = await OntologyService.create_relationships(namespace, rel_requests)
                for r in results:
                    if r.status == "created":
                        rel_created += 1
                    elif r.status == "skipped":
                        rel_skipped += 1
                    else:
                        rel_errors += 1
                        if len(rel_error_samples) < 5 and r.error:
                            rel_error_samples.append(r.error)

            if i == 0:
                logger.info(
                    f"First relationship batch: {len(rel_requests)} requests, "
                    f"created={rel_created}, errors={rel_errors}, "
                    f"samples={rel_error_samples}"
                )

        elapsed = time.perf_counter() - t0
        terms_created = sum(1 for r in term_results if r.status == "created")
        terms_skipped = sum(1 for r in term_results if r.status == "skipped")
        terms_errors = sum(1 for r in term_results if r.status == "error")

        if rel_error_samples:
            logger.warning(f"Relationship error samples: {rel_error_samples}")

        return {
            "terminology": {
                "terminology_id": terminology_id,
                "value": terminology_value,
                "label": terminology_label,
                "status": terminology_status,
            },
            "terms": {
                "total": len(nodes),
                "created": terms_created,
                "skipped": terms_skipped,
                "errors": terms_errors,
            },
            "relationships": {
                "total": len(edges),
                "created": rel_created,
                "skipped": rel_skipped,
                "errors": rel_errors,
                "predicate_distribution": parsed["stats"]["predicate_distribution"],
                "error_samples": rel_error_samples[:5] if rel_error_samples else [],
            },
            "elapsed_seconds": round(elapsed, 1),
        }
