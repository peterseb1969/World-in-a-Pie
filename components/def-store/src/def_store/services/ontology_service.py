"""Ontology service for managing term relationships and traversal."""

import logging
import math
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from pymongo.errors import DuplicateKeyError

from ..models.term import Term
from ..models.term_relationship import TermRelationship
from ..models.api_models import (
    BulkResultItem,
    CreateRelationshipRequest,
    DeleteRelationshipRequest,
    RelationshipResponse,
    TraversalNode,
    TraversalResponse,
)
from ..api.auth import get_identity_string
from .nats_client import publish_relationship_event, EventType as NatsEventType

logger = logging.getLogger(__name__)


class OntologyService:
    """Service for managing ontology relationships and traversal queries."""

    # Cache for relationship type values (populated on first use)
    _relationship_types: dict[str, str] | None = None

    # =========================================================================
    # RELATIONSHIP CRUD
    # =========================================================================

    @staticmethod
    async def create_relationships(
        namespace: str,
        items: list[CreateRelationshipRequest],
    ) -> list[BulkResultItem]:
        """
        Bulk create relationships between terms.

        Validates that source and target terms exist and relationship_type
        is a valid value from _ONTOLOGY_RELATIONSHIP_TYPES.
        """
        actor = get_identity_string()
        results: list[BulkResultItem] = []

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

                # Create relationship
                rel = TermRelationship(
                    namespace=namespace,
                    source_term_id=item.source_term_id,
                    target_term_id=item.target_term_id,
                    relationship_type=item.relationship_type,
                    relationship_value=item.relationship_type,  # Store value for display
                    source_terminology_id=source_term.terminology_id,
                    target_terminology_id=target_term.terminology_id,
                    metadata=item.metadata,
                    status="active",
                    created_at=datetime.now(timezone.utc),
                    created_by=actor or item.created_by,
                )

                await rel.insert()

                results.append(BulkResultItem(
                    index=i,
                    status="created",
                    value=f"{item.source_term_id} --{item.relationship_type}--> {item.target_term_id}"
                ))

                # Publish event to NATS
                await publish_relationship_event(
                    NatsEventType.RELATIONSHIP_CREATED,
                    {
                        "namespace": namespace,
                        "source_term_id": item.source_term_id,
                        "target_term_id": item.target_term_id,
                        "relationship_type": item.relationship_type,
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
                # Check if the existing relationship is inactive (soft-deleted) — re-activate it
                existing = await TermRelationship.find_one({
                    "namespace": namespace,
                    "source_term_id": item.source_term_id,
                    "target_term_id": item.target_term_id,
                    "relationship_type": item.relationship_type,
                    "status": "inactive",
                })
                if existing:
                    existing.status = "active"
                    await existing.save()
                    results.append(BulkResultItem(
                        index=i,
                        status="created",
                        value=f"{item.source_term_id} --{item.relationship_type}--> {item.target_term_id} (reactivated)"
                    ))

                    # Publish reactivation as a create event
                    await publish_relationship_event(
                        NatsEventType.RELATIONSHIP_CREATED,
                        {
                            "namespace": namespace,
                            "source_term_id": item.source_term_id,
                            "target_term_id": item.target_term_id,
                            "relationship_type": item.relationship_type,
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
                        error=f"Relationship already exists: {item.source_term_id} --{item.relationship_type}--> {item.target_term_id}"
                    ))
            except Exception as e:
                logger.error(f"Error creating relationship at index {i}: {e}")
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    error=str(e)
                ))

        return results

    @staticmethod
    async def delete_relationships(
        namespace: str,
        items: list[DeleteRelationshipRequest],
    ) -> list[BulkResultItem]:
        """Bulk soft-delete relationships (set status=inactive)."""
        results: list[BulkResultItem] = []

        for i, item in enumerate(items):
            try:
                rel = await TermRelationship.find_one({
                    "namespace": namespace,
                    "source_term_id": item.source_term_id,
                    "target_term_id": item.target_term_id,
                    "relationship_type": item.relationship_type,
                })

                if not rel:
                    results.append(BulkResultItem(
                        index=i,
                        status="error",
                        error="Relationship not found"
                    ))
                    continue

                if rel.status == "inactive":
                    results.append(BulkResultItem(
                        index=i,
                        status="skipped",
                        value="Already inactive"
                    ))
                    continue

                rel.status = "inactive"
                await rel.save()

                results.append(BulkResultItem(
                    index=i,
                    status="deleted",
                    value=f"{item.source_term_id} --{item.relationship_type}--> {item.target_term_id}"
                ))

                # Publish delete event to NATS
                await publish_relationship_event(
                    NatsEventType.RELATIONSHIP_DELETED,
                    {
                        "namespace": namespace,
                        "source_term_id": item.source_term_id,
                        "target_term_id": item.target_term_id,
                        "relationship_type": item.relationship_type,
                        "source_terminology_id": rel.source_terminology_id,
                        "target_terminology_id": rel.target_terminology_id,
                        "status": "inactive",
                    },
                )

            except Exception as e:
                logger.error(f"Error deleting relationship at index {i}: {e}")
                results.append(BulkResultItem(
                    index=i,
                    status="error",
                    error=str(e)
                ))

        return results

    @staticmethod
    async def list_relationships(
        term_id: str,
        namespace: str = "wip",
        direction: str = "outgoing",
        relationship_type: Optional[str] = None,
        status: str = "active",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[RelationshipResponse], int]:
        """List relationships for a term with pagination."""
        query: dict = {"namespace": namespace, "status": status}

        if direction == "outgoing":
            query["source_term_id"] = term_id
        elif direction == "incoming":
            query["target_term_id"] = term_id
        else:  # both
            query["$or"] = [
                {"source_term_id": term_id},
                {"target_term_id": term_id},
            ]

        if relationship_type:
            query["relationship_type"] = relationship_type

        total = await TermRelationship.find(query).count()
        skip = (page - 1) * page_size

        rels = await TermRelationship.find(query).skip(skip).limit(page_size).to_list()

        items = [OntologyService._to_relationship_response(r) for r in rels]
        return items, total

    @staticmethod
    async def list_all_relationships(
        namespace: str = "wip",
        relationship_type: Optional[str] = None,
        status: str = "active",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[RelationshipResponse], int]:
        """List all relationships in a namespace with pagination."""
        query: dict = {"namespace": namespace, "status": status}

        if relationship_type:
            query["relationship_type"] = relationship_type

        total = await TermRelationship.find(query).count()
        skip = (page - 1) * page_size

        rels = await TermRelationship.find(query).skip(skip).limit(page_size).to_list()

        items = [OntologyService._to_relationship_response(r) for r in rels]
        return items, total

    # =========================================================================
    # TRAVERSAL QUERIES
    # =========================================================================

    @staticmethod
    async def get_ancestors(
        term_id: str,
        namespace: str,
        relationship_type: str = "is_a",
        max_depth: int = 10,
    ) -> TraversalResponse:
        """
        BFS traversal upward — follow outgoing relationships of the given type.

        For is_a, also includes parent_term_id links for backward compatibility.
        """
        max_depth = min(max_depth, 50)
        return await OntologyService._traverse(
            start_term_id=term_id,
            namespace=namespace,
            relationship_type=relationship_type,
            direction="ancestors",
            max_depth=max_depth,
        )

    @staticmethod
    async def get_descendants(
        term_id: str,
        namespace: str,
        relationship_type: str = "is_a",
        max_depth: int = 10,
    ) -> TraversalResponse:
        """
        BFS traversal downward — follow incoming relationships of the given type.

        For is_a, also includes children via parent_term_id.
        """
        max_depth = min(max_depth, 50)
        return await OntologyService._traverse(
            start_term_id=term_id,
            namespace=namespace,
            relationship_type=relationship_type,
            direction="descendants",
            max_depth=max_depth,
        )

    @staticmethod
    async def get_parents(
        term_id: str,
        namespace: str,
    ) -> list[RelationshipResponse]:
        """Direct parents only: outgoing is_a relationships + parent_term_id."""
        results: list[RelationshipResponse] = []
        seen_targets: set[str] = set()

        # From TermRelationship (is_a outgoing)
        rels = await TermRelationship.find({
            "namespace": namespace,
            "source_term_id": term_id,
            "relationship_type": "is_a",
            "status": "active",
        }).to_list()

        for r in rels:
            results.append(OntologyService._to_relationship_response(r))
            seen_targets.add(r.target_term_id)

        # From parent_term_id (backward compatibility)
        term = await Term.find_one({"namespace": namespace, "term_id": term_id})
        if term and term.parent_term_id and term.parent_term_id not in seen_targets:
            results.append(RelationshipResponse(
                namespace=namespace,
                source_term_id=term_id,
                target_term_id=term.parent_term_id,
                relationship_type="is_a",
                relationship_value="is_a (parent_term_id)",
                source_terminology_id=term.terminology_id,
                status="active",
                created_at=term.created_at,
            ))

        return results

    @staticmethod
    async def get_children(
        term_id: str,
        namespace: str,
    ) -> list[RelationshipResponse]:
        """Direct children only: incoming is_a relationships + children via parent_term_id."""
        results: list[RelationshipResponse] = []
        seen_sources: set[str] = set()

        # From TermRelationship (is_a incoming)
        rels = await TermRelationship.find({
            "namespace": namespace,
            "target_term_id": term_id,
            "relationship_type": "is_a",
            "status": "active",
        }).to_list()

        for r in rels:
            results.append(OntologyService._to_relationship_response(r))
            seen_sources.add(r.source_term_id)

        # From parent_term_id (backward compatibility)
        children = await Term.find({
            "namespace": namespace,
            "parent_term_id": term_id,
            "status": "active",
        }).to_list()

        for child in children:
            if child.term_id not in seen_sources:
                results.append(RelationshipResponse(
                    namespace=namespace,
                    source_term_id=child.term_id,
                    target_term_id=term_id,
                    relationship_type="is_a",
                    relationship_value="is_a (parent_term_id)",
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
        relationship_type: str,
        direction: str,
        max_depth: int,
    ) -> TraversalResponse:
        """
        Generic BFS traversal.

        For ancestors: follow source→target (outgoing relationships).
        For descendants: follow target→source (incoming relationships).
        """
        visited: set[str] = {start_term_id}
        frontier: deque[tuple[str, int, list[str]]] = deque()
        # (term_id, depth, path_from_start)
        frontier.append((start_term_id, 0, [start_term_id]))

        nodes: list[TraversalNode] = []
        max_depth_reached = False
        use_parent_term_id = (relationship_type == "is_a")

        while frontier:
            current_id, depth, path = frontier.popleft()

            if depth >= max_depth:
                max_depth_reached = True
                continue

            # Batch query: find next level neighbors
            next_ids: set[str] = set()

            if direction == "ancestors":
                # Outgoing: current is source, targets are ancestors
                rels = await TermRelationship.find({
                    "namespace": namespace,
                    "source_term_id": current_id,
                    "relationship_type": relationship_type,
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
                rels = await TermRelationship.find({
                    "namespace": namespace,
                    "target_term_id": current_id,
                    "relationship_type": relationship_type,
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
                    new_path = path + [next_id]
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
            relationship_type=relationship_type,
            direction=direction,
            nodes=nodes,
            total=len(nodes),
            max_depth_reached=max_depth_reached,
        )

    @staticmethod
    def _to_relationship_response(rel: TermRelationship) -> RelationshipResponse:
        """Convert a TermRelationship document to an API response."""
        return RelationshipResponse(
            namespace=rel.namespace,
            source_term_id=rel.source_term_id,
            target_term_id=rel.target_term_id,
            relationship_type=rel.relationship_type,
            relationship_value=rel.relationship_value,
            source_terminology_id=rel.source_terminology_id,
            target_terminology_id=rel.target_terminology_id,
            metadata=rel.metadata,
            status=rel.status,
            created_at=rel.created_at,
            created_by=rel.created_by,
        )
