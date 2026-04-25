"""Ontology service for managing term relations and traversal."""

import logging
from collections import deque
from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from ..api.auth import get_identity_string
from ..models.api_models import (
    BulkResultItem,
    CreateTermRelationRequest,
    DeleteTermRelationRequest,
    TermRelationResponse,
    TraversalNode,
    TraversalResponse,
)
from ..models.term import Term
from ..models.term_relation import TermRelation
from .nats_client import EventType as NatsEventType
from .nats_client import publish_term_relation_event
from .registry_client import get_registry_client

logger = logging.getLogger(__name__)


class OntologyService:
    """Service for managing ontology relations and traversal queries."""

    # Cache for relation type values (populated on first use)
    _relation_types: dict[str, str] | None = None

    # =========================================================================
    # RELATIONSHIP TYPE VALIDATION
    # =========================================================================

    @classmethod
    async def get_valid_relation_types(cls) -> dict[str, str]:
        """
        Load and cache valid relation types from _ONTOLOGY_RELATIONSHIP_TYPES.

        Returns:
            Dict mapping term value → term label (e.g. {"is_a": "Is a"}).
        """
        if cls._relation_types is not None:
            return cls._relation_types

        from ..models.terminology import Terminology

        terminology = await Terminology.find_one({
            "value": "_ONTOLOGY_RELATIONSHIP_TYPES"
        })
        if not terminology:
            logger.warning("_ONTOLOGY_RELATIONSHIP_TYPES terminology not found")
            return {}

        types: dict[str, str] = {}
        async for term in Term.find({
            "terminology_id": terminology.terminology_id,
            "status": "active",
        }):
            types[term.value] = term.label or term.value

        cls._relation_types = types
        logger.info(f"Loaded {len(types)} valid relation types")
        return cls._relation_types

    @classmethod
    def invalidate_relation_type_cache(cls) -> None:
        """Invalidate the cache so next call reloads from DB."""
        cls._relation_types = None

    # =========================================================================
    # RELATIONSHIP CRUD
    # =========================================================================

    @staticmethod
    async def create_term_relations(
        namespace: str,
        items: list[CreateTermRelationRequest],
    ) -> list[BulkResultItem]:
        """
        Bulk create relations between terms.

        Validates that source and target terms exist and relation_type
        is a valid value from _ONTOLOGY_RELATIONSHIP_TYPES.
        """
        actor = get_identity_string()
        results: list[BulkResultItem] = []

        # Load valid relation types
        valid_types = await OntologyService.get_valid_relation_types()

        # Pre-fetch all referenced term IDs for validation
        all_term_ids = set()
        for item in items:
            all_term_ids.add(item.source_term_id)
            all_term_ids.add(item.target_term_id)

        existing_terms = {}
        if all_term_ids:
            async for term in Term.find(
                {"namespace": namespace, "term_id": {"$in": list(all_term_ids)}}
            ):
                existing_terms[term.term_id] = term

        for i, item in enumerate(items):
            try:
                # Validate relation type
                if not valid_types:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error="Cannot validate relation type: "
                              "_ONTOLOGY_RELATIONSHIP_TYPES terminology not found. "
                              "Restart def-store to bootstrap system terminologies."
                    ))
                    continue
                if item.relation_type not in valid_types:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error=f"Unknown relation type '{item.relation_type}'. "
                              f"Valid types: {', '.join(sorted(valid_types.keys()))}"
                    ))
                    continue

                # Validate source term exists
                source_term = existing_terms.get(item.source_term_id)
                if not source_term:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error=f"Source term '{item.source_term_id}' not found in namespace '{namespace}'"
                    ))
                    continue

                # Validate target term exists
                target_term = existing_terms.get(item.target_term_id)
                if not target_term:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error=f"Target term '{item.target_term_id}' not found in namespace '{namespace}'"
                    ))
                    continue

                # Validate self-referencing
                if item.source_term_id == item.target_term_id:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error="Source and target term cannot be the same"
                    ))
                    continue

                # Create relation
                rel = TermRelation(
                    namespace=namespace,
                    source_term_id=item.source_term_id,
                    target_term_id=item.target_term_id,
                    relation_type=item.relation_type,
                    relation_value=item.relation_type,  # Store value for display
                    source_terminology_id=source_term.terminology_id,
                    target_terminology_id=target_term.terminology_id,
                    metadata=item.metadata,
                    status="active",
                    created_at=datetime.now(UTC),
                    created_by=actor or item.created_by,
                )

                await rel.insert()

                results.append(BulkResultItem(
                    index=i,
                    status="created",
                    value=f"{item.source_term_id} --{item.relation_type}--> {item.target_term_id}"
                ))

                # Publish event to NATS
                await publish_term_relation_event(
                    NatsEventType.TERM_RELATION_CREATED,
                    {
                        "namespace": namespace,
                        "source_term_id": item.source_term_id,
                        "target_term_id": item.target_term_id,
                        "relation_type": item.relation_type,
                        "source_terminology_id": source_term.terminology_id,
                        "target_terminology_id": target_term.terminology_id,
                        "source_term_value": source_term.value,
                        "target_term_value": target_term.value,
                        "metadata": item.metadata or {},
                        "status": "active",
                        "created_by": actor or item.created_by,
                    },
                    changed_by=actor or item.created_by,
                )

            except DuplicateKeyError:
                # Check if the existing relation is inactive (soft-deleted) — re-activate it
                existing = await TermRelation.find_one({
                    "namespace": namespace,
                    "source_term_id": item.source_term_id,
                    "target_term_id": item.target_term_id,
                    "relation_type": item.relation_type,
                    "status": "inactive",
                })
                if existing:
                    existing.status = "active"
                    await existing.save()
                    results.append(BulkResultItem(
                        index=i,
                        status="created",
                        value=f"{item.source_term_id} --{item.relation_type}--> {item.target_term_id} (reactivated)"
                    ))

                    # Publish reactivation as a create event
                    await publish_term_relation_event(
                        NatsEventType.TERM_RELATION_CREATED,
                        {
                            "namespace": namespace,
                            "source_term_id": item.source_term_id,
                            "target_term_id": item.target_term_id,
                            "relation_type": item.relation_type,
                            "source_terminology_id": existing.source_terminology_id,
                            "target_terminology_id": existing.target_terminology_id,
                            "metadata": existing.metadata or {},
                            "status": "active",
                            "created_by": actor,
                        },
                        changed_by=actor,
                    )
                else:
                    results.append(BulkResultItem(
                        index=i,
                        status="skipped",
                        error=f"Relation already exists: {item.source_term_id} --{item.relation_type}--> {item.target_term_id}"
                    ))
            except Exception as e:
                logger.error(f"Error creating relation at index {i}: {e}")
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    error=str(e)
                ))

        return results

    @staticmethod
    async def delete_term_relations(
        namespace: str,
        items: list[DeleteTermRelationRequest],
    ) -> list[BulkResultItem]:
        """Bulk delete relations. Hard-deletes if requested and namespace allows it."""
        results: list[BulkResultItem] = []

        # Pre-check deletion_mode if any items request hard-delete
        deletion_mode = None
        if any(item.hard_delete for item in items):
            client = get_registry_client()
            deletion_mode = await client.get_namespace_deletion_mode(namespace)

        for i, item in enumerate(items):
            try:
                if item.hard_delete and deletion_mode != "full":
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error=f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')",
                    ))
                    continue

                rel = await TermRelation.find_one({
                    "namespace": namespace,
                    "source_term_id": item.source_term_id,
                    "target_term_id": item.target_term_id,
                    "relation_type": item.relation_type,
                })

                if not rel:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error="Relation not found"
                    ))
                    continue

                event_data = {
                    "namespace": namespace,
                    "source_term_id": item.source_term_id,
                    "target_term_id": item.target_term_id,
                    "relation_type": item.relation_type,
                    "source_terminology_id": rel.source_terminology_id,
                    "target_terminology_id": rel.target_terminology_id,
                }

                if item.hard_delete:
                    # HARD DELETE: permanently remove from MongoDB
                    await rel.delete()
                    event_data["hard_delete"] = True

                    results.append(BulkResultItem(
                        index=i,
                        status="deleted",
                        value=f"{item.source_term_id} --{item.relation_type}--> {item.target_term_id}"
                    ))
                else:
                    # SOFT DELETE: set status to inactive
                    if rel.status == "inactive":
                        results.append(BulkResultItem(
                            index=i,
                            status="skipped",
                            value="Already inactive"
                        ))
                        continue

                    rel.status = "inactive"
                    await rel.save()
                    event_data["status"] = "inactive"

                    results.append(BulkResultItem(
                        index=i,
                        status="deleted",
                        value=f"{item.source_term_id} --{item.relation_type}--> {item.target_term_id}"
                    ))

                # Publish delete event to NATS
                await publish_term_relation_event(
                    NatsEventType.TERM_RELATION_DELETED,
                    event_data,
                )

            except Exception as e:
                logger.error(f"Error deleting relation at index {i}: {e}")
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    error=str(e)
                ))

        return results

    @staticmethod
    async def list_term_relations(
        term_id: str,
        ns_filter: dict | None = None,
        direction: str = "outgoing",
        relation_type: str | None = None,
        status: str = "active",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[TermRelationResponse], int]:
        """List relations for a term with pagination."""
        query: dict = {"status": status}
        if ns_filter:
            query.update(ns_filter)

        if direction == "outgoing":
            query["source_term_id"] = term_id
        elif direction == "incoming":
            query["target_term_id"] = term_id
        else:  # both
            query["$or"] = [
                {"source_term_id": term_id},
                {"target_term_id": term_id},
            ]

        if relation_type:
            query["relation_type"] = relation_type

        total = await TermRelation.find(query).count()
        skip = (page - 1) * page_size

        rels = await TermRelation.find(query).skip(skip).limit(page_size).to_list()

        term_lookup = await OntologyService._build_term_lookup(rels)
        items = [OntologyService._to_term_relation_response(r, term_lookup) for r in rels]
        return items, total

    @staticmethod
    async def list_all_term_relations(
        ns_filter: dict | None = None,
        relation_type: str | None = None,
        source_terminology_id: str | None = None,
        status: str = "active",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[TermRelationResponse], int]:
        """List all relations with pagination (cross-namespace when filter allows)."""
        query: dict = {"status": status}
        if ns_filter:
            query.update(ns_filter)

        if source_terminology_id:
            query["source_terminology_id"] = source_terminology_id

        if relation_type:
            query["relation_type"] = relation_type

        total = await TermRelation.find(query).count()
        skip = (page - 1) * page_size

        rels = await TermRelation.find(query).skip(skip).limit(page_size).to_list()

        term_lookup = await OntologyService._build_term_lookup(rels)
        items = [OntologyService._to_term_relation_response(r, term_lookup) for r in rels]
        return items, total

    # =========================================================================
    # TRAVERSAL QUERIES
    # =========================================================================

    @staticmethod
    async def get_ancestors(
        term_id: str,
        namespace: str,
        relation_type: str = "is_a",
        max_depth: int = 10,
    ) -> TraversalResponse:
        """
        BFS traversal upward — follow outgoing relations of the given type.

        For is_a, also includes parent_term_id links for backward compatibility.
        """
        max_depth = min(max_depth, 50)
        return await OntologyService._traverse(
            start_term_id=term_id,
            namespace=namespace,
            relation_type=relation_type,
            direction="ancestors",
            max_depth=max_depth,
        )

    @staticmethod
    async def get_descendants(
        term_id: str,
        namespace: str,
        relation_type: str = "is_a",
        max_depth: int = 10,
    ) -> TraversalResponse:
        """
        BFS traversal downward — follow incoming relations of the given type.

        For is_a, also includes children via parent_term_id.
        """
        max_depth = min(max_depth, 50)
        return await OntologyService._traverse(
            start_term_id=term_id,
            namespace=namespace,
            relation_type=relation_type,
            direction="descendants",
            max_depth=max_depth,
        )

    @staticmethod
    async def get_parents(
        term_id: str,
        namespace: str,
    ) -> list[TermRelationResponse]:
        """Direct parents only: outgoing is_a relations + parent_term_id."""
        results: list[TermRelationResponse] = []
        seen_targets: set[str] = set()

        # From TermRelation (is_a outgoing)
        rels = await TermRelation.find({
            "namespace": namespace,
            "source_term_id": term_id,
            "relation_type": "is_a",
            "status": "active",
        }).to_list()

        for r in rels:
            results.append(OntologyService._to_term_relation_response(r))
            seen_targets.add(r.target_term_id)

        # From parent_term_id (backward compatibility)
        term = await Term.find_one({"namespace": namespace, "term_id": term_id})
        if term and term.parent_term_id and term.parent_term_id not in seen_targets:
            results.append(TermRelationResponse(
                namespace=namespace,
                source_term_id=term_id,
                target_term_id=term.parent_term_id,
                relation_type="is_a",
                relation_value="is_a (parent_term_id)",
                source_terminology_id=term.terminology_id,
                status="active",
                created_at=term.created_at,
            ))

        return results

    @staticmethod
    async def get_children(
        term_id: str,
        namespace: str,
    ) -> list[TermRelationResponse]:
        """Direct children only: incoming is_a relations + children via parent_term_id."""
        results: list[TermRelationResponse] = []
        seen_sources: set[str] = set()

        # From TermRelation (is_a incoming)
        rels = await TermRelation.find({
            "namespace": namespace,
            "target_term_id": term_id,
            "relation_type": "is_a",
            "status": "active",
        }).to_list()

        for r in rels:
            results.append(OntologyService._to_term_relation_response(r))
            seen_sources.add(r.source_term_id)

        # From parent_term_id (backward compatibility)
        children = await Term.find({
            "namespace": namespace,
            "parent_term_id": term_id,
            "status": "active",
        }).to_list()

        for child in children:
            if child.term_id not in seen_sources:
                results.append(TermRelationResponse(
                    namespace=namespace,
                    source_term_id=child.term_id,
                    target_term_id=term_id,
                    relation_type="is_a",
                    relation_value="is_a (parent_term_id)",
                    source_terminology_id=child.terminology_id,
                    status="active",
                    created_at=child.created_at,
                ))

        return results

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    @staticmethod
    async def _traverse(
        start_term_id: str,
        namespace: str,
        relation_type: str,
        direction: str,
        max_depth: int,
    ) -> TraversalResponse:
        """
        Generic BFS traversal.

        For ancestors: follow source→target (outgoing relations).
        For descendants: follow target→source (incoming relations).
        """
        visited: set[str] = {start_term_id}
        frontier: deque[tuple[str, int, list[str]]] = deque()
        # (term_id, depth, path_from_start)
        frontier.append((start_term_id, 0, [start_term_id]))

        nodes: list[TraversalNode] = []
        max_depth_reached = False
        use_parent_term_id = (relation_type == "is_a")

        while frontier:
            current_id, depth, path = frontier.popleft()

            if depth >= max_depth:
                max_depth_reached = True
                continue

            # Batch query: find next level neighbors
            next_ids: set[str] = set()

            if direction == "ancestors":
                # Outgoing: current is source, targets are ancestors
                rels = await TermRelation.find({
                    "namespace": namespace,
                    "source_term_id": current_id,
                    "relation_type": relation_type,
                    "status": "active",
                }).to_list()
                for r in rels:
                    next_ids.add(r.target_term_id)

                # Also check parent_term_id for is_a
                if use_parent_term_id:
                    term = await Term.find_one({
                        "namespace": namespace,
                        "term_id": current_id,
                    })
                    if term and term.parent_term_id:
                        next_ids.add(term.parent_term_id)

            else:  # descendants
                # Incoming: current is target, sources are descendants
                rels = await TermRelation.find({
                    "namespace": namespace,
                    "target_term_id": current_id,
                    "relation_type": relation_type,
                    "status": "active",
                }).to_list()
                for r in rels:
                    next_ids.add(r.source_term_id)

                # Also check children via parent_term_id for is_a
                if use_parent_term_id:
                    children = await Term.find({
                        "namespace": namespace,
                        "parent_term_id": current_id,
                        "status": "active",
                    }).to_list()
                    for child in children:
                        next_ids.add(child.term_id)

            # Add unvisited neighbors to frontier
            for next_id in next_ids:
                if next_id not in visited:
                    visited.add(next_id)
                    new_path = [*path, next_id]
                    nodes.append(TraversalNode(
                        term_id=next_id,
                        depth=depth + 1,
                        path=new_path,
                    ))
                    frontier.append((next_id, depth + 1, new_path))

        # Denormalize: batch-fetch term values for all discovered nodes
        if nodes:
            term_ids = [n.term_id for n in nodes]
            terms_by_id: dict[str, Term] = {}
            async for term in Term.find({
                "namespace": namespace,
                "term_id": {"$in": term_ids},
            }):
                terms_by_id[term.term_id] = term

            for node in nodes:
                term = terms_by_id.get(node.term_id)
                if term:
                    node.value = term.value
                    node.terminology_id = term.terminology_id

        return TraversalResponse(
            term_id=start_term_id,
            relation_type=relation_type,
            direction=direction,
            nodes=nodes,
            total=len(nodes),
            max_depth_reached=max_depth_reached,
        )

    @staticmethod
    def _to_term_relation_response(
        rel: TermRelation,
        term_lookup: dict[str, Term] | None = None,
    ) -> TermRelationResponse:
        """Convert a TermRelation document to an API response."""
        source_term_value = None
        source_term_label = None
        target_term_value = None
        target_term_label = None

        if term_lookup:
            src = term_lookup.get(rel.source_term_id)
            if src:
                source_term_value = src.value
                source_term_label = src.label
            tgt = term_lookup.get(rel.target_term_id)
            if tgt:
                target_term_value = tgt.value
                target_term_label = tgt.label

        return TermRelationResponse(
            namespace=rel.namespace,
            source_term_id=rel.source_term_id,
            target_term_id=rel.target_term_id,
            relation_type=rel.relation_type,
            relation_value=rel.relation_value,
            source_terminology_id=rel.source_terminology_id,
            target_terminology_id=rel.target_terminology_id,
            source_term_value=source_term_value,
            source_term_label=source_term_label,
            target_term_value=target_term_value,
            target_term_label=target_term_label,
            metadata=rel.metadata,
            status=rel.status,
            created_at=rel.created_at,
            created_by=rel.created_by,
        )

    @staticmethod
    async def _build_term_lookup(rels: list[TermRelation]) -> dict[str, Term]:
        """Batch-fetch term documents for all term IDs referenced in relations."""
        term_ids = set()
        for r in rels:
            term_ids.add(r.source_term_id)
            term_ids.add(r.target_term_id)
        if not term_ids:
            return {}
        terms = await Term.find({"term_id": {"$in": list(term_ids)}}).to_list()
        return {t.term_id: t for t in terms}
