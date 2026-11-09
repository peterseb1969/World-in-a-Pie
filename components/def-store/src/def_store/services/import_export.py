"""Import/Export service for terminologies and terms."""

import contextlib
import csv
import io
import json
import logging
import time
from collections import Counter
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, ClassVar

from wip_auth.csv_cells import escape_cell, unescape_cell

from ..models.api_models import (
    BulkResultItem,
    CreateTerminologyRequest,
    CreateTermRelationRequest,
    CreateTermRequest,
    DeprecateTermRequest,
)
from ..models.term import Term, TermName
from ..models.term_relation import TermRelation
from ..models.terminology import Terminology, TerminologyMetadata
from .errors import DefStoreError
from .import_plan import (
    IMPORTED_DEPRECATION_REASON,
    ImportItem,
    deprecation_order,
    parent_levels,
    parse_items,
    relation_drop_reason,
)
from .ontology_service import OntologyService
from .terminology_service import TerminologyService

logger = logging.getLogger(__name__)

# The terminology flags an export carries and an import of a new
# terminology applies.
_TERMINOLOGY_FLAGS = ("case_sensitive", "allow_multiple", "mutable", "unique_names")


def _flag_differences(terminology_data: dict[str, Any], existing: Terminology) -> dict[str, Any]:
    """The flags the file sets differently from the existing terminology
    it extends — reported, not applied (an import never modifies one)."""
    return {
        flag: {"file": terminology_data[flag], "stored": getattr(existing, flag)}
        for flag in _TERMINOLOGY_FLAGS
        if flag in terminology_data and terminology_data[flag] != getattr(existing, flag)
    }


async def _term_ids_by_value(terminology_id: str, values: set[str]) -> dict[str, str]:
    """value -> term_id for the named values of one terminology."""
    if not values:
        return {}
    terms = await Term.find({"terminology_id": terminology_id, "value": {"$in": list(values)}}).to_list()
    return {t.value: t.term_id for t in terms}


async def _record(outcome: dict[str, Any], key: str, item: ImportItem, step: Awaitable[Any]) -> None:
    """Run one lifecycle step of an imported term and count it, or record
    why it failed (the term then stays active, and the result says so)."""
    try:
        done = await step
    except ValueError as e:
        outcome["errors"].append({"value": item.request.value, "error": str(e)})
        return
    if not done:
        outcome["errors"].append({"value": item.request.value, "error": "the term was not found"})
        return
    outcome[key] += 1


def _export_term(t: Term, include_metadata: bool, pointer_values: dict[str, str]) -> dict[str, Any]:
    """One term of a JSON export; parent and replacement by value."""
    term_dict: dict[str, Any] = {
        "value": t.value,
        "label": t.label or t.value,
        "description": t.description,
        "sort_order": t.sort_order,
        "status": t.status,
    }
    if t.names:
        # Required `value` always serializes; optional fields only when
        # set, so simple vocabularies export compact
        term_dict["names"] = [n.model_dump(exclude_none=True, exclude_defaults=True) for n in t.names]
    if t.parent_term_id:
        term_dict["parent_term_value"] = pointer_values.get(t.parent_term_id)
    if include_metadata and t.metadata:
        term_dict["metadata"] = t.metadata
    if t.status == "deprecated":
        term_dict["deprecated_reason"] = t.deprecated_reason
        if t.replaced_by_term_id:
            term_dict["replaced_by_term_value"] = pointer_values.get(t.replaced_by_term_id)
    return term_dict


def _terminology_summary(terminology: Terminology, status: str) -> dict[str, Any]:
    return {
        "terminology_id": terminology.terminology_id,
        "value": terminology.value,
        "label": terminology.label,
        "status": status,
    }


def _terms_summary(term_results: list[BulkResultItem], total: int) -> dict[str, Any]:
    return {
        "results": [r.model_dump() for r in term_results],
        "total": total,
        "succeeded": sum(1 for r in term_results if r.status == "created"),
        "skipped": sum(1 for r in term_results if r.status == "skipped"),
        "failed": sum(1 for r in term_results if r.status == "error"),
    }


def _level_refusal(item: ImportItem, parents: dict[str, str], mutable: bool) -> BulkResultItem | None:
    """Why an item cannot be created, or None — and then its parent value
    is resolved to the parent's fresh id on its request."""
    error: tuple[str, str] | None = None
    if item.parent_value is not None and item.parent_value not in parents:
        error = (f"parent_term_value '{item.parent_value}' names no term of this terminology", "reference_violation")
    elif item.status == "inactive" and mutable:
        error = (
            "An inactive term cannot be imported into a mutable terminology: "
            "a mutable terminology removes a deleted term instead of keeping it inactive",
            "invalid_state",
        )
    if error is not None:
        return BulkResultItem(
            index=item.index, status="error", value=item.request.value, error=error[0], error_code=error[1],
        )
    if item.parent_value is not None:
        item.request.parent_term_id = parents[item.parent_value]
    return None


def _deprecation(item: ImportItem, replacements: dict[str, str], outcome: dict[str, Any]) -> DeprecateTermRequest:
    """The deprecation of an imported term: the file's reason and its
    replacement, resolved by value in the imported terminology (a legacy
    replaced_by_term_id passes as it is). A replacement value that names
    no term is recorded, and the term is deprecated without one."""
    replaced_by = item.replaced_by_id
    if item.replaced_by_value:
        replaced_by = replacements.get(item.replaced_by_value)
        if replaced_by is None:
            outcome["errors"].append({
                "value": item.request.value,
                "error": f"replaced_by_term_value '{item.replaced_by_value}' names no term of this "
                         "terminology; deprecated without a replacement",
            })
    return DeprecateTermRequest(reason=item.reason or IMPORTED_DEPRECATION_REASON, replaced_by_term_id=replaced_by)


async def _create_relations(
    namespace: str, requests: list[CreateTermRelationRequest], batch_size: int,
    counts: dict[str, int], error_samples: list[str],
) -> None:
    """Create the relations in batches, counting each outcome."""
    for i in range(0, len(requests), batch_size):
        for r in await OntologyService.create_term_relations(namespace, requests[i:i + batch_size]):
            if r.status in ("created", "skipped"):
                counts[r.status] += 1
                continue
            counts["errors"] += 1
            if len(error_samples) < 5 and r.error:
                error_samples.append(r.error)


async def _pointer_values(terms: list[Term]) -> dict[str, str]:
    """term_id -> value for every term the exported terms point at as
    parent or replacement, whether or not it is exported itself."""
    values = {t.term_id: t.value for t in terms}
    wanted = {p for t in terms for p in (t.parent_term_id, t.replaced_by_term_id) if p and p not in values}
    if wanted:
        for t in await Term.find({"term_id": {"$in": list(wanted)}}).to_list():
            values[t.term_id] = t.value
    return values


def _csv_term(row: dict[str, str]) -> dict[str, Any]:
    """One CSV row as a term item (see import_plan.parse_item)."""
    term: dict[str, Any] = {
        "value": (row.get("value") or "").strip(),
        "label": (row.get("label") or "").strip() or None,
        "description": (row.get("description") or "").strip() or None,
        "sort_order": int(row.get("sort_order") or 0),
    }
    status = (row.get("status") or "").strip()
    if status:
        term["status"] = status
    if row.get("metadata"):
        with contextlib.suppress(json.JSONDecodeError):
            term["metadata"] = json.loads(row["metadata"])
    return term


def _relation_batch_size(options: dict[str, Any]) -> int:
    """The import's relation batch size, bounded like the query doors'
    (an options dict passes no query validation; 0 would never advance)."""
    size = options.get("relation_batch_size", 500)
    if not isinstance(size, int) or not 1 <= size <= 10_000:
        raise DefStoreError("relation_batch_size must be 1 to 10000", "invalid_request", rule="batch_size")
    return size


class ImportExportService:
    """Service for importing and exporting terminologies."""

    # =========================================================================
    # EXPORT
    # =========================================================================

    @staticmethod
    async def export_terminology(
        terminology_id: str | None = None,
        terminology_value: str | None = None,
        format: str = "json",
        include_metadata: bool = True,
        include_inactive: bool = False,
        include_relations: bool = False,
        languages: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Export a terminology with all its terms.

        Args:
            terminology_id: Terminology ID
            terminology_value: Terminology value (alternative)
            format: Export format (json, csv)
            include_metadata: Include metadata in export
            include_inactive: Include inactive/deprecated terms
            include_relations: Include ontology relations
            languages: Languages to include for translations

        Returns:
            Export data in requested format
        """
        # Get terminology — an unscoped value that exists in several
        # namespaces raises rather than exporting an arbitrary one
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif terminology_value:
            terminology = await TerminologyService.find_by_value(terminology_value, None)
        else:
            raise ValueError("Must provide terminology_id or terminology_value")

        if not terminology:
            raise DefStoreError("Terminology not found", "not_found")

        # Get terms
        query = {"terminology_id": terminology.terminology_id}
        if not include_inactive:
            query["status"] = "active"

        terms = await Term.find(query).sort("sort_order").to_list()

        # Filter language-bearing names if languages specified
        if languages:
            for term in terms:
                term.names = [
                    n for n in term.names
                    if n.language is None or n.language in languages
                ]

        # Get relations if requested
        relations: list[TermRelation] = []
        if include_relations and format != "csv":
            rel_query: dict[str, Any] = {
                "namespace": terminology.namespace,
                "source_terminology_id": terminology.terminology_id,
            }
            if not include_inactive:
                rel_query["status"] = "active"
            relations = await TermRelation.find(rel_query).to_list()

            # Also get relations where target is in this terminology
            # but source is from another (cross-terminology links)
            cross_query: dict[str, Any] = {
                "namespace": terminology.namespace,
                "target_terminology_id": terminology.terminology_id,
                "source_terminology_id": {"$ne": terminology.terminology_id},
            }
            if not include_inactive:
                cross_query["status"] = "active"
            cross_rels = await TermRelation.find(cross_query).to_list()
            relations.extend(cross_rels)

        if format == "csv":
            return ImportExportService._export_csv(terminology, terms, include_metadata)
        return ImportExportService._export_json(
            terminology, terms, include_metadata, relations,
            pointer_values=await _pointer_values(terms),
        )

    @staticmethod
    def _export_json(
        terminology: Terminology,
        terms: list[Term],
        include_metadata: bool,
        relations: list["TermRelation"] | None = None,
        pointer_values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Export as JSON.

        A term's parent and replacement are written by value
        (parent_term_value, replaced_by_term_value), never by id: an import
        mints fresh ids, so only the value names the same term there.
        `pointer_values` maps every pointed-at term id to its value, the
        exported terms' and any other term's alike.
        """
        pointer_values = pointer_values or {}
        # Build term_id → value lookup for relation denormalization
        term_id_to_value: dict[str, str] = {}
        term_data = []
        for t in terms:
            term_id_to_value[t.term_id] = t.value
            term_data.append(_export_term(t, include_metadata, pointer_values))

        result: dict[str, Any] = {
            "terminology": {
                "value": terminology.value,
                "label": terminology.label,
                "description": terminology.description,
                "namespace": terminology.namespace,
                **{flag: getattr(terminology, flag) for flag in _TERMINOLOGY_FLAGS},
            },
            "terms": term_data,
            "export_date": datetime.now(UTC).isoformat(),
            "format": "json",
            "version": "2.1"
        }

        if include_metadata:
            result["terminology"]["metadata"] = {
                "source": terminology.metadata.source,
                "source_url": terminology.metadata.source_url,
                "version": terminology.metadata.version,
                "language": terminology.metadata.language,
                "custom": terminology.metadata.custom
            }

        # Include relations if provided
        if relations:
            rel_data = []
            for r in relations:
                rel_dict: dict[str, Any] = {
                    "source_term_value": term_id_to_value.get(r.source_term_id, r.source_term_id),
                    "target_term_value": term_id_to_value.get(r.target_term_id, r.target_term_id),
                    "relation_type": r.relation_type,
                }
                if r.metadata:
                    rel_dict["metadata"] = r.metadata
                if r.source_terminology_id != terminology.terminology_id:
                    rel_dict["source_terminology_id"] = r.source_terminology_id
                if r.target_terminology_id != terminology.terminology_id:
                    rel_dict["target_terminology_id"] = r.target_terminology_id
                rel_data.append(rel_dict)
            result["relations"] = rel_data

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

            # A term value like "=HYPERLINK(...)" must not run as a formula
            # in a spreadsheet; the CSV import below reverses the escape.
            writer.writerow({k: escape_cell(v) for k, v in row.items()})

        return {
            "terminology": {
                "value": terminology.value,
                "label": terminology.label,
            },
            "csv_content": output.getvalue(),
            "export_date": datetime.now(UTC).isoformat(),
            "format": "csv"
        }

    @staticmethod
    async def export_all_terminologies(
        format: str = "json",
        include_inactive: bool = False,
        namespaces: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Export all terminologies.

        Args:
            namespaces: When set, restrict the export to terminologies in
                these namespaces. None means no namespace filter
                (caller is superadmin or has equivalent access).
                Added by CASE-384 so the API layer can pass the caller's
                accessible-namespaces list without leaking entities the
                caller has no permission on.
        """
        query: dict[str, Any] = {} if include_inactive else {"status": "active"}
        if namespaces is not None:
            query["namespace"] = {"$in": namespaces}
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
        options: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """
        Import a terminology with terms.

        Every term item is checked before anything is written; a malformed
        one refuses the whole import. A new terminology takes the file's
        flags (case_sensitive, allow_multiple, mutable, unique_names). An
        existing one is extended, never modified: its own fields stay as
        they are (status "exists", with any flag the file sets differently
        listed under "differs"), and a retired one is refused — restore it
        first. Terms come back with their lifecycle: each is created
        active, parents before children, and a term the file marks
        deprecated or inactive is then deprecated (keeping its reason and
        replacement) or deactivated — reported under "lifecycle_result".

        Args:
            data: Import data
            format: Data format (json, csv)
            options: Import options
                - skip_duplicates: Skip terms that already exist
                - update_existing: Extend-only — existing terminology/terms are
                  left unchanged (reported "exists"/"skipped"), not modified.
                  In-place update is a future --force feature.
                - batch_size / registry_batch_size / relation_batch_size
            namespace: Destination namespace, resolved by the API layer
                (query param / JSON body / single-namespace-key derivation)
                and already permission-checked there. When set it wins;
                the CSV payload never carries one.

        Returns:
            Import results
        """
        options = options or {}
        if format == "csv":
            return await ImportExportService._import_csv(data, options, namespace)

        terminology_data = ImportExportService._terminology_block(data, namespace)
        items = parse_items(data.get("terms", []))
        levels = parent_levels(items)
        target_namespace = terminology_data["namespace"]

        terminology, terminology_result = await ImportExportService._target_terminology(terminology_data)
        term_results = await ImportExportService._create_term_levels(terminology, levels, options)
        lifecycle = await ImportExportService._apply_lifecycle(terminology, items, term_results)

        result: dict[str, Any] = {
            "terminology": terminology_result,
            "terms_result": _terms_summary(term_results, len(items)),
        }
        if lifecycle is not None:
            result["lifecycle_result"] = lifecycle
        relations_data = data.get("relations") or []
        if relations_data:
            result["relations_result"] = await ImportExportService._import_term_relations(
                relations_data,
                terminology_id=terminology.terminology_id,
                namespace=target_namespace,
                options=options,
            )
        return result

    @staticmethod
    def _terminology_block(data: Any, namespace: str | None) -> dict[str, Any]:
        """The file's terminology block, checked, with its destination
        namespace set (the API layer's wins)."""
        if not isinstance(data, dict):
            raise ValueError("Import data must be an object")
        terminology_data = data.get("terminology")
        if not isinstance(terminology_data, dict) or not terminology_data:
            raise ValueError("Missing 'terminology' field in import data")
        if not terminology_data.get("value"):
            raise ValueError("Missing 'terminology.value' field in import data")
        if not terminology_data.get("label"):
            raise ValueError("Missing 'terminology.label' field in import data")
        if namespace:
            terminology_data["namespace"] = namespace
        if not terminology_data.get("namespace"):
            # The API layer always resolves one; direct service callers must
            # supply it too.
            raise ValueError("Missing terminology namespace for import")
        return terminology_data

    @staticmethod
    async def _target_terminology(terminology_data: dict[str, Any]) -> tuple[Terminology, dict[str, Any]]:
        """The terminology the import writes into, and how the result
        reports it: an existing one (refused when retired, never modified,
        its differing flags listed) or a new one created from the file."""
        existing = await Terminology.find_one({
            "value": terminology_data["value"],
            "namespace": terminology_data["namespace"],
        })
        if existing is None:
            created = await ImportExportService._create_terminology(terminology_data)
            return created, _terminology_summary(created, "created")
        TerminologyService._require_active(existing)
        summary = _terminology_summary(existing, "exists")
        differs = _flag_differences(terminology_data, existing)
        if differs:
            summary["differs"] = differs
        return existing, summary

    @staticmethod
    async def _create_terminology(terminology_data: dict[str, Any]) -> Terminology:
        """Create the imported terminology with the file's flags."""
        metadata = terminology_data.get("metadata") or {}
        create_req = CreateTerminologyRequest(
            value=terminology_data["value"],
            label=terminology_data["label"],
            description=terminology_data.get("description"),
            namespace=terminology_data["namespace"],
            metadata=TerminologyMetadata(**metadata) if metadata else None,
            **{flag: terminology_data[flag] for flag in _TERMINOLOGY_FLAGS if flag in terminology_data},
        )
        response = await TerminologyService.create_terminology(create_req, terminology_data["namespace"])
        created = await Terminology.find_one({"terminology_id": response.terminology_id})
        if created is None:
            raise DefStoreError("The imported terminology vanished after it was created", "internal_error")
        return created

    @staticmethod
    async def _create_term_levels(
        terminology: Terminology, levels: list[list[ImportItem]], options: dict[str, Any],
    ) -> list[BulkResultItem]:
        """Create the items level by level, so each parent exists (with its
        fresh id) before its children name it. Results come back in file
        order, indexed by the item's place in the file."""
        results: dict[int, BulkResultItem] = {}
        for level in levels:
            parents = await _term_ids_by_value(
                terminology.terminology_id, {i.parent_value for i in level if i.parent_value},
            )
            to_create: list[ImportItem] = []
            for item in level:
                refusal = _level_refusal(item, parents, terminology.mutable)
                if refusal is None:
                    to_create.append(item)
                else:
                    results[item.index] = refusal
            if not to_create:
                continue
            created = await TerminologyService.create_terms_bulk(
                terminology_id=terminology.terminology_id,
                terms=[i.request for i in to_create],
                skip_duplicates=options.get("skip_duplicates", True),
                update_existing=options.get("update_existing", False),
                batch_size=options.get("batch_size", 1000),
                registry_batch_size=options.get("registry_batch_size", 100),
            )
            for item, result in zip(to_create, created, strict=True):
                result.index = item.index
                results[item.index] = result
        return [results[i] for i in sorted(results)]

    @staticmethod
    async def _apply_lifecycle(
        terminology: Terminology, items: list[ImportItem], term_results: list[BulkResultItem],
    ) -> dict[str, Any] | None:
        """Deprecate, then deactivate, each term this import created that
        the file marks deprecated or inactive. A term the import found
        already there is left as it is (extend-only). None when the file
        marks no created term."""
        created = {r.value: r.id for r in term_results if r.status == "created" and r.id}
        pending = [i for i in items if i.status != "active" and i.request.value in created]
        if not pending:
            return None
        outcome: dict[str, Any] = {"deprecated": 0, "deactivated": 0, "errors": []}
        replacements = await _term_ids_by_value(
            terminology.terminology_id, {i.replaced_by_value for i in pending if i.replaced_by_value},
        )
        for item in deprecation_order(pending):
            await _record(outcome, "deprecated", item, TerminologyService.deprecate_term(
                created[item.request.value], _deprecation(item, replacements, outcome),
            ))
        for item in (i for i in pending if i.status == "inactive"):
            await _record(outcome, "deactivated", item, TerminologyService.delete_term(created[item.request.value]))
        return outcome

    @staticmethod
    async def _import_csv(
        data: dict[str, Any],
        options: dict[str, Any],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Import from CSV format.

        The flat CSV payload carries no namespace by design — the caller
        resolves the destination namespace (API layer: query param or
        single-namespace-key derivation) and passes it here. Columns:
        value, label, description, sort_order, status, metadata. A CSV with
        rows of which none carries a value is refused, naming the headers
        it has; a CSV with no rows at all imports an empty terminology,
        like JSON `terms: []`.
        """
        terminology_value = data.get("terminology_value")
        terminology_label = data.get("terminology_label", terminology_value)
        csv_content = data.get("csv_content", "")

        if not terminology_value:
            raise ValueError("terminology_value is required for CSV import")

        reader = csv.DictReader(io.StringIO(csv_content))
        terms_data = []
        rows = 0
        for raw in reader:
            rows += 1
            term = _csv_term({unescape_cell(k): unescape_cell(v) for k, v in raw.items()})
            if term["value"]:
                terms_data.append(term)
        if rows and not terms_data:
            raise ValueError(
                f"The CSV has {rows} rows but none has a value: its columns are "
                f"{reader.fieldnames}, and a term needs a 'value' column"
            )

        json_data = {
            "terminology": {
                "value": terminology_value,
                "label": terminology_label,
            },
            "terms": terms_data
        }

        return await ImportExportService.import_terminology(
            json_data, "json", options, namespace=namespace
        )

    @staticmethod
    async def _import_term_relations(
        relations_data: list[Any],
        terminology_id: str,
        namespace: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Import relations from export format (source_term_value/target_term_value).

        Both ends must be terms of the imported terminology. A relation
        that cannot be created is counted under "dropped", with its reason
        in "dropped_samples", so created + skipped + errors + dropped is
        always the total.
        """
        options = options or {}
        relation_batch_size = _relation_batch_size(options)
        value_to_id = {
            t.value: t.term_id
            for t in await Term.find({"namespace": namespace, "terminology_id": terminology_id}).to_list()
        }

        counts = {"created": 0, "skipped": 0, "errors": 0, "dropped": 0}
        error_samples: list[str] = []
        dropped_samples: list[dict[str, Any]] = []
        requests: list[CreateTermRelationRequest] = []
        for index, rd in enumerate(relations_data):
            reason = relation_drop_reason(rd, terminology_id, value_to_id)
            if reason is not None:
                counts["dropped"] += 1
                if len(dropped_samples) < 5:
                    dropped_samples.append({"index": index, "reason": reason})
                continue
            requests.append(CreateTermRelationRequest(
                source_term_id=value_to_id[rd["source_term_value"]],
                target_term_id=value_to_id[rd["target_term_value"]],
                relation_type=rd["relation_type"],
                metadata=rd.get("metadata") or {},
            ))

        await ImportExportService._ensure_relation_types({r.relation_type for r in requests})
        await _create_relations(namespace, requests, relation_batch_size, counts, error_samples)

        return {
            "total": len(relations_data),
            **counts,
            "error_samples": error_samples,
            "dropped_samples": dropped_samples,
        }

    @staticmethod
    async def import_from_url(
        url: str,
        format: str = "json",
        options: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """
        Import terminology from a URL.

        Args:
            url: URL to fetch data from
            format: Expected format (json, csv)
            options: Import options
            namespace: Destination namespace (required for CSV; for JSON it
                wins over the payload's terminology.namespace)

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

        return await ImportExportService.import_terminology(data, format, options, namespace=namespace)

    # =========================================================================
    # OBO GRAPH JSON IMPORT
    # =========================================================================

    # Predicate URI → WIP relation type
    OBO_PREDICATE_MAP: ClassVar[dict[str, str]] = {
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
    OBO_SKIP_PREDICATES: ClassVar[set[str]] = {"subPropertyOf"}

    @staticmethod
    def _uri_to_value(uri: str) -> str:
        """Convert OBO URI to compact value: HP_0000001 → HP:0000001."""
        fragment = uri.rsplit("/", 1)[-1]
        return fragment.replace("_", ":", 1)

    @staticmethod
    def _detect_prefix(graph: dict) -> str | None:
        """Auto-detect the OBO term prefix from the graph's DATA.

        The prefix scopes which nodes become terms (only ids matching
        ``.../obo/<PREFIX>_...``). It MUST come from the nodes, not the
        filename: a subset/slim/renamed file (goslim_generic.json,
        envoPolar.json) has a filename that is not its term prefix, and
        deriving the filter from the filename filtered out every real node
        and imported nothing while reporting success. Detect the majority
        prefix among CLASS nodes instead — robust to filename, to subsets,
        and to the OWL/OBO/JSON format trio (same graph, same nodes).

        Falls back to the graph-id filename only for a graph whose nodes
        carry no OBO-style prefix at all (a non-OBO graph), where the
        filename is the last available hint.
        """
        counts: Counter[str] = Counter()
        for n in graph.get("nodes", []):
            if n.get("type") != "CLASS":
                continue
            uri = n.get("id", "")
            if not uri.startswith("http://purl.obolibrary.org/obo/"):
                continue
            fragment = uri.rsplit("/", 1)[-1]
            if "_" in fragment:
                counts[fragment.split("_", 1)[0]] += 1
        if counts:
            return counts.most_common(1)[0][0]

        # No OBO-prefixed CLASS node — fall back to the filename hint.
        graph_id = graph.get("id", "")
        filename = graph_id.rsplit("/", 1)[-1]
        base = filename.split(".")[0].split("-")[0].upper()
        return base if base else None

    @classmethod
    def _map_predicate(cls, pred: str) -> str | None:
        """Map OBO predicate to WIP relation type."""
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
        max_synonyms: int | None = None,
    ) -> dict[str, Any]:
        """Parse OBO Graph JSON into nodes and edges.

        max_synonyms=None (the default) keeps every synonym. When a cap is
        passed, the count of synonyms it drops is reported in the result's
        stats — never dropped silently.
        """
        graph = data["graphs"][0]
        meta = graph.get("meta", {})
        dropped_synonyms = 0

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

            # Synonyms become names, keeping the OBO predicate (exact /
            # related / broad / narrow scope) as the name's source instead
            # of discarding it. Machine-generated synonym lists carry
            # duplicates and label-echoes, so dedupe here rather than
            # letting per-term validation reject whole terms.
            raw_synonyms = [s for s in node_meta.get("synonyms", []) if s.get("val")]
            if max_synonyms is not None and len(raw_synonyms) > max_synonyms:
                dropped_synonyms += len(raw_synonyms) - max_synonyms
                raw_synonyms = raw_synonyms[:max_synonyms]
            names: list[dict] = []
            seen_names = {value}
            for s in raw_synonyms:
                if s["val"] in seen_names:
                    continue
                seen_names.add(s["val"])
                pred = s.get("pred")
                names.append({
                    "value": s["val"],
                    "source": f"OBO:{pred}" if pred else "OBO",
                })

            xrefs = [x.get("val") for x in node_meta.get("xrefs", []) if x.get("val")]

            term_metadata: dict[str, Any] = {}
            if xrefs:
                term_metadata["xrefs"] = xrefs

            nodes[uri] = {
                "value": value,
                "label": label,
                "description": description,
                "names": names,
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
                "relation_type": rel_type,
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
                "dropped_synonyms": dropped_synonyms,
            },
        }

    @staticmethod
    async def _ensure_relation_types(needed_types: set[str]) -> None:
        """
        Ensure all needed relation types exist in _ONTOLOGY_RELATIONSHIP_TYPES.

        Auto-creates any missing types as new terms in the system terminology.
        Invalidates the OntologyService cache after adding new types.
        """
        from .system_terminologies import TERM_RELATION_TYPES_TERMINOLOGY_VALUE
        from .system_vocabulary import find_system_terminology

        valid_types = await OntologyService.get_valid_relation_types()
        missing = needed_types - set(valid_types.keys())

        if not missing:
            return

        logger.info(f"Auto-creating {len(missing)} missing relation types: {missing}")

        # Find the _ONTOLOGY_RELATIONSHIP_TYPES terminology
        terminology = await find_system_terminology(TERM_RELATION_TYPES_TERMINOLOGY_VALUE)
        if not terminology:
            logger.error(
                f"Cannot auto-create relation types: "
                f"{TERM_RELATION_TYPES_TERMINOLOGY_VALUE} terminology not found"
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
                description="Auto-created from ontology import",
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
            logger.info(f"Created {created} new relation types")

            # Invalidate cache so create_relations picks up the new types
            OntologyService.invalidate_relation_type_cache()

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
                     batch_size, registry_batch_size, relation_batch_size,
                     namespace, skip_duplicates, update_existing)

        Returns:
            Import summary with terminology, term, and relation stats.
        """
        t0 = time.perf_counter()

        namespace = options["namespace"]
        batch_size = options.get("batch_size", 1000)
        registry_batch_size = options.get("registry_batch_size", 50)
        relation_batch_size = _relation_batch_size(options)
        skip_duplicates = options.get("skip_duplicates", True)
        update_existing = options.get("update_existing", False)

        # Parse
        parsed = ImportExportService._parse_obo_graph(
            data,
            prefix_filter=options.get("prefix_filter"),
            include_deprecated=options.get("include_deprecated", False),
            max_synonyms=options.get("max_synonyms"),
        )

        nodes = parsed["nodes"]
        edges = parsed["edges"]
        meta = parsed["ontology_meta"]

        # An import that matched nothing must fail loud, never return a
        # green "terminology created / 0 terms" — a terminology shell with
        # no terms is worse than an error (a downstream bootstrap reads it
        # as present). Refuse BEFORE creating the terminology so no orphan
        # shell is left behind. A genuinely empty graph carries no nodes at
        # all; the diagnostic case is a graph WITH nodes where the prefix
        # filter matched none of them (the CASE-775 shape).
        graph_node_count = sum(
            1 for n in data["graphs"][0].get("nodes", []) if n.get("type") == "CLASS"
        )
        if not nodes and graph_node_count:
            observed: Counter[str] = Counter()
            for n in data["graphs"][0].get("nodes", []):
                if n.get("type") != "CLASS":
                    continue
                frag = n.get("id", "").rsplit("/", 1)[-1]
                if "_" in frag:
                    observed[frag.split("_", 1)[0]] += 1
            raise ValueError(
                f"Import matched 0 of {graph_node_count} class nodes: the "
                f"prefix filter '{parsed['prefix']}' selected nothing. "
                f"Observed node prefixes: {dict(observed.most_common())}. "
                "Pass prefix_filter explicitly, or check the graph."
            )

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
            # A retired terminology takes no new terms: restore it first.
            TerminologyService._require_active(existing)
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
            )
            terminology_response = await TerminologyService.create_terminology(create_req, namespace)
            terminology_id = terminology_response.terminology_id
            terminology_status = "created"

        # Import terms in batches
        term_requests = []
        for info in nodes.values():
            term_requests.append(CreateTermRequest(
                value=info["value"],
                label=info["label"],
                description=info["description"],
                names=[TermName(**n) for n in info["names"]],
                metadata=info["metadata"],
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
            if r.id and r.value:
                value_to_id[r.value] = r.id

        # Resolve IDs for skipped terms
        if len(value_to_id) < len(nodes):
            async for term in Term.find({
                "namespace": namespace,
                "terminology_id": terminology_id,
            }):
                value_to_id[term.value] = term.term_id

        # Build URI→term_id mapping and import relations
        uri_to_id: dict[str, str] = {}
        for uri, info in nodes.items():
            tid = value_to_id.get(info["value"])
            if tid:
                uri_to_id[uri] = tid

        logger.info(
            f"ID mappings: value_to_id={len(value_to_id)}, "
            f"uri_to_id={len(uri_to_id)}, nodes={len(nodes)}, edges={len(edges)}"
        )

        # Ensure all relation types exist in _ONTOLOGY_RELATIONSHIP_TYPES
        edge_rel_types = {e["relation_type"] for e in edges}
        await ImportExportService._ensure_relation_types(edge_rel_types)

        rel_created = 0
        rel_skipped = 0
        rel_errors = 0
        rel_error_samples: list[str] = []

        for i in range(0, len(edges), relation_batch_size):
            batch_edges = edges[i:i + relation_batch_size]
            rel_requests: list[CreateTermRelationRequest] = []
            for e in batch_edges:
                src_id = uri_to_id.get(e["source_uri"])
                tgt_id = uri_to_id.get(e["target_uri"])
                if src_id and tgt_id:
                    rel_requests.append(CreateTermRelationRequest(
                        source_term_id=src_id,
                        target_term_id=tgt_id,
                        relation_type=e["relation_type"],
                    ))

            if rel_requests:
                results = await OntologyService.create_term_relations(namespace, rel_requests)
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
                    f"First relation batch: {len(rel_requests)} requests, "
                    f"created={rel_created}, errors={rel_errors}, "
                    f"samples={rel_error_samples}"
                )

        elapsed = time.perf_counter() - t0
        terms_created = sum(1 for r in term_results if r.status == "created")
        terms_skipped = sum(1 for r in term_results if r.status == "skipped")
        terms_errors = sum(1 for r in term_results if r.status == "error")

        if rel_error_samples:
            logger.warning(f"Relation error samples: {rel_error_samples}")

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
                # Non-zero only when a max_synonyms cap was passed — the
                # cap is opt-in and its cost is reported, never silent
                "dropped_synonyms": parsed["stats"]["dropped_synonyms"],
            },
            "relations": {
                "total": len(edges),
                "created": rel_created,
                "skipped": rel_skipped,
                "errors": rel_errors,
                "predicate_distribution": parsed["stats"]["predicate_distribution"],
                "error_samples": rel_error_samples[:5] if rel_error_samples else [],
            },
            "elapsed_seconds": round(elapsed, 1),
        }
