"""Terminology service for business logic."""

from datetime import datetime, timezone
from typing import Optional

from ..models.terminology import Terminology, TerminologyMetadata
from ..models.term import Term
from ..models.audit_log import TermAuditLog
from ..models.api_models import (
    CreateTerminologyRequest,
    UpdateTerminologyRequest,
    TerminologyResponse,
    CreateTermRequest,
    UpdateTermRequest,
    DeprecateTermRequest,
    TermResponse,
    BulkOperationResult,
)
from .registry_client import get_registry_client, RegistryError

# Import identity helper from wip-auth
# This returns the authenticated identity, not the client-provided value
from ..api.auth import get_identity_string


class TerminologyService:
    """Service for managing terminologies and terms."""

    # =========================================================================
    # TERMINOLOGY OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_terminology(
        request: CreateTerminologyRequest
    ) -> TerminologyResponse:
        """
        Create a new terminology.

        1. Register with Registry to get ID
        2. Create terminology document in MongoDB

        Args:
            request: Creation request

        Returns:
            Created terminology

        Raises:
            ValueError: If code already exists
            RegistryError: If Registry communication fails
        """
        # Check if code already exists
        existing = await Terminology.find_one({"code": request.code})
        if existing:
            raise ValueError(f"Terminology with code '{request.code}' already exists")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register with Registry to get ID
        client = get_registry_client()
        terminology_id = await client.register_terminology(
            code=request.code,
            name=request.name,
            created_by=actor
        )

        # Create terminology document
        terminology = Terminology(
            terminology_id=terminology_id,
            code=request.code,
            name=request.name,
            description=request.description,
            case_sensitive=request.case_sensitive,
            allow_multiple=request.allow_multiple,
            extensible=request.extensible,
            metadata=request.metadata or TerminologyMetadata(),
            created_by=actor,
        )
        await terminology.insert()

        return TerminologyService._to_terminology_response(terminology)

    @staticmethod
    async def get_terminology(
        terminology_id: Optional[str] = None,
        code: Optional[str] = None
    ) -> Optional[TerminologyResponse]:
        """
        Get a terminology by ID or code.

        Args:
            terminology_id: Terminology ID (e.g., 'TERM-000001')
            code: Terminology code (e.g., 'DOC_STATUS')

        Returns:
            Terminology if found, None otherwise
        """
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif code:
            terminology = await Terminology.find_one({"code": code})
        else:
            return None

        if terminology:
            return TerminologyService._to_terminology_response(terminology)
        return None

    @staticmethod
    async def list_terminologies(
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ) -> tuple[list[TerminologyResponse], int]:
        """
        List terminologies with pagination.

        Args:
            status: Filter by status (active, deprecated, inactive)
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (terminologies, total_count)
        """
        query = {}
        if status:
            query["status"] = status

        total = await Terminology.find(query).count()
        skip = (page - 1) * page_size

        terminologies = await Terminology.find(query) \
            .sort("name") \
            .skip(skip) \
            .limit(page_size) \
            .to_list()

        return (
            [TerminologyService._to_terminology_response(t) for t in terminologies],
            total
        )

    @staticmethod
    async def update_terminology(
        terminology_id: str,
        request: UpdateTerminologyRequest
    ) -> Optional[TerminologyResponse]:
        """
        Update a terminology.

        If code changes, adds a synonym in the Registry.

        Args:
            terminology_id: Terminology to update
            request: Update request

        Returns:
            Updated terminology, or None if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return None

        # Track if code is changing
        old_code = terminology.code
        code_changed = request.code and request.code != old_code

        # If code changes, add synonym in Registry
        if code_changed:
            # Check new code doesn't exist
            existing = await Terminology.find_one({"code": request.code})
            if existing:
                raise ValueError(f"Terminology with code '{request.code}' already exists")

            # Add synonym for new code
            client = get_registry_client()
            await client.add_synonym(
                namespace="wip-terminologies",
                target_id=terminology_id,
                new_code=request.code,
                additional_fields={"name": request.name or terminology.name}
            )

        # Apply updates
        if request.code is not None:
            terminology.code = request.code
        if request.name is not None:
            terminology.name = request.name
        if request.description is not None:
            terminology.description = request.description
        if request.case_sensitive is not None:
            terminology.case_sensitive = request.case_sensitive
        if request.allow_multiple is not None:
            terminology.allow_multiple = request.allow_multiple
        if request.extensible is not None:
            terminology.extensible = request.extensible
        if request.metadata is not None:
            terminology.metadata = request.metadata

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        terminology.updated_at = datetime.now(timezone.utc)
        terminology.updated_by = actor
        await terminology.save()

        return TerminologyService._to_terminology_response(terminology)

    @staticmethod
    async def delete_terminology(
        terminology_id: str,
        updated_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> bool:
        """
        Soft-delete a terminology (set status to inactive).

        Also deactivates all terms in the terminology.

        Args:
            terminology_id: Terminology to delete
            updated_by: Deprecated - uses authenticated identity

        Returns:
            True if deleted, False if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return False

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Deactivate terminology
        terminology.status = "inactive"
        terminology.updated_at = datetime.now(timezone.utc)
        terminology.updated_by = actor
        await terminology.save()

        # Deactivate all terms
        await Term.find({"terminology_id": terminology_id}).update_many({
            "$set": {
                "status": "inactive",
                "updated_at": datetime.now(timezone.utc),
                "updated_by": actor
            }
        })

        return True

    # =========================================================================
    # TERM OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_term(
        terminology_id: str,
        request: CreateTermRequest
    ) -> TermResponse:
        """
        Create a new term in a terminology.

        Args:
            terminology_id: Parent terminology ID
            request: Creation request

        Returns:
            Created term

        Raises:
            ValueError: If terminology not found or code/value exists
        """
        # Verify terminology exists
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            raise ValueError(f"Terminology '{terminology_id}' not found")

        # Check code uniqueness within terminology
        existing = await Term.find_one({
            "terminology_id": terminology_id,
            "code": request.code
        })
        if existing:
            raise ValueError(
                f"Term with code '{request.code}' already exists in terminology"
            )

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register with Registry to get ID
        client = get_registry_client()
        term_id = await client.register_term(
            terminology_id=terminology_id,
            code=request.code,
            value=request.value,
            created_by=actor
        )

        # Create term document
        term = Term(
            term_id=term_id,
            terminology_id=terminology_id,
            terminology_code=terminology.code,
            code=request.code,
            value=request.value,
            aliases=request.aliases,
            label=request.label,
            description=request.description,
            sort_order=request.sort_order,
            parent_term_id=request.parent_term_id,
            translations=request.translations,
            metadata=request.metadata,
            created_by=actor,
        )
        await term.insert()

        # Create audit log entry
        await TerminologyService._create_audit_log(
            term_id=term_id,
            terminology_id=terminology_id,
            action="created",
            changed_by=actor,
            new_values={
                "code": request.code,
                "value": request.value,
                "aliases": request.aliases,
                "label": request.label,
            }
        )

        # Update terminology term count
        terminology.term_count += 1
        terminology.updated_at = datetime.now(timezone.utc)
        await terminology.save()

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def create_terms_bulk(
        terminology_id: str,
        terms: list[CreateTermRequest],
        created_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> list[BulkOperationResult]:
        """
        Create multiple terms in a terminology.

        Args:
            terminology_id: Parent terminology ID
            terms: Terms to create
            created_by: Deprecated - uses authenticated identity

        Returns:
            List of operation results
        """
        # Verify terminology exists
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            raise ValueError(f"Terminology '{terminology_id}' not found")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register all terms with Registry
        client = get_registry_client()
        registry_results = await client.register_terms_bulk(
            terminology_id=terminology_id,
            terms=[{"code": t.code, "value": t.value} for t in terms],
            created_by=actor
        )

        results = []
        created_count = 0

        for i, (term_req, reg_result) in enumerate(zip(terms, registry_results)):
            if reg_result["status"] == "error":
                results.append(BulkOperationResult(
                    index=i,
                    status="error",
                    code=term_req.code,
                    error=reg_result.get("error")
                ))
                continue

            term_id = reg_result["registry_id"]

            # Check if term already exists in our DB
            existing = await Term.find_one({"term_id": term_id})
            if existing:
                results.append(BulkOperationResult(
                    index=i,
                    status="skipped",
                    id=term_id,
                    code=term_req.code,
                    error="Already exists"
                ))
                continue

            # Create term document
            term = Term(
                term_id=term_id,
                terminology_id=terminology_id,
                terminology_code=terminology.code,
                code=term_req.code,
                value=term_req.value,
                aliases=term_req.aliases,
                label=term_req.label,
                description=term_req.description,
                sort_order=term_req.sort_order,
                parent_term_id=term_req.parent_term_id,
                translations=term_req.translations,
                metadata=term_req.metadata,
                created_by=actor,
            )
            await term.insert()

            # Create audit log entry
            await TerminologyService._create_audit_log(
                term_id=term_id,
                terminology_id=terminology_id,
                action="created",
                changed_by=actor,
                new_values={
                    "code": term_req.code,
                    "value": term_req.value,
                    "aliases": term_req.aliases,
                    "label": term_req.label,
                }
            )

            created_count += 1

            results.append(BulkOperationResult(
                index=i,
                status="created",
                id=term_id,
                code=term_req.code
            ))

        # Update terminology term count
        if created_count > 0:
            terminology.term_count += created_count
            terminology.updated_at = datetime.now(timezone.utc)
            await terminology.save()

        return results

    @staticmethod
    async def get_term(
        term_id: Optional[str] = None,
        terminology_id: Optional[str] = None,
        code: Optional[str] = None
    ) -> Optional[TermResponse]:
        """
        Get a term by ID or by terminology+code.

        Args:
            term_id: Term ID
            terminology_id: Terminology ID (with code)
            code: Term code (with terminology_id)

        Returns:
            Term if found, None otherwise
        """
        if term_id:
            term = await Term.find_one({"term_id": term_id})
        elif terminology_id and code:
            term = await Term.find_one({
                "terminology_id": terminology_id,
                "code": code
            })
        else:
            return None

        if term:
            return TerminologyService._to_term_response(term)
        return None

    @staticmethod
    async def list_terms(
        terminology_id: str,
        status: Optional[str] = None,
        include_children: bool = True
    ) -> list[TermResponse]:
        """
        List all terms in a terminology.

        Args:
            terminology_id: Terminology to list terms from
            status: Filter by status
            include_children: Include child terms in hierarchy

        Returns:
            List of terms, sorted by sort_order
        """
        query = {"terminology_id": terminology_id}
        if status:
            query["status"] = status

        terms = await Term.find(query) \
            .sort([("sort_order", 1), ("code", 1)]) \
            .to_list()

        return [TerminologyService._to_term_response(t) for t in terms]

    @staticmethod
    async def update_term(
        term_id: str,
        request: UpdateTermRequest
    ) -> Optional[TermResponse]:
        """
        Update a term.

        If code changes, adds a synonym in the Registry.
        Creates an audit log entry for all changes.
        """
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return None

        # Track changes for audit log
        changed_fields = []
        previous_values = {}
        new_values = {}

        # Track if code is changing
        old_code = term.code
        code_changed = request.code and request.code != old_code

        if code_changed:
            # Check new code doesn't exist
            existing = await Term.find_one({
                "terminology_id": term.terminology_id,
                "code": request.code
            })
            if existing:
                raise ValueError(f"Term with code '{request.code}' already exists")

            # Add synonym
            client = get_registry_client()
            await client.add_synonym(
                namespace="wip-terms",
                target_id=term_id,
                new_code=request.code,
                additional_fields={
                    "terminology_id": term.terminology_id,
                    "value": request.value or term.value
                }
            )

        # Apply updates and track changes
        if request.code is not None and request.code != term.code:
            changed_fields.append("code")
            previous_values["code"] = term.code
            new_values["code"] = request.code
            term.code = request.code

        if request.value is not None and request.value != term.value:
            changed_fields.append("value")
            previous_values["value"] = term.value
            new_values["value"] = request.value
            term.value = request.value

        if request.aliases is not None and request.aliases != term.aliases:
            changed_fields.append("aliases")
            previous_values["aliases"] = term.aliases
            new_values["aliases"] = request.aliases
            term.aliases = request.aliases

        if request.label is not None and request.label != term.label:
            changed_fields.append("label")
            previous_values["label"] = term.label
            new_values["label"] = request.label
            term.label = request.label

        if request.description is not None and request.description != term.description:
            changed_fields.append("description")
            previous_values["description"] = term.description
            new_values["description"] = request.description
            term.description = request.description

        if request.sort_order is not None and request.sort_order != term.sort_order:
            changed_fields.append("sort_order")
            previous_values["sort_order"] = term.sort_order
            new_values["sort_order"] = request.sort_order
            term.sort_order = request.sort_order

        if request.parent_term_id is not None and request.parent_term_id != term.parent_term_id:
            changed_fields.append("parent_term_id")
            previous_values["parent_term_id"] = term.parent_term_id
            new_values["parent_term_id"] = request.parent_term_id
            term.parent_term_id = request.parent_term_id

        if request.translations is not None:
            changed_fields.append("translations")
            previous_values["translations"] = [t.model_dump() for t in term.translations]
            new_values["translations"] = [t.model_dump() for t in request.translations]
            term.translations = request.translations

        if request.metadata is not None:
            changed_fields.append("metadata")
            previous_values["metadata"] = term.metadata.copy()
            term.metadata.update(request.metadata)
            new_values["metadata"] = term.metadata

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        term.updated_at = datetime.now(timezone.utc)
        term.updated_by = actor
        await term.save()

        # Create audit log entry if there were changes
        if changed_fields:
            await TerminologyService._create_audit_log(
                term_id=term_id,
                terminology_id=term.terminology_id,
                action="updated",
                changed_by=actor,
                changed_fields=changed_fields,
                previous_values=previous_values,
                new_values=new_values
            )

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def deprecate_term(
        term_id: str,
        request: DeprecateTermRequest
    ) -> Optional[TermResponse]:
        """
        Deprecate a term (mark as deprecated but keep for historical data).
        """
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return None

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        term.status = "deprecated"
        term.deprecated_reason = request.reason
        term.replaced_by_term_id = request.replaced_by_term_id
        term.updated_at = datetime.now(timezone.utc)
        term.updated_by = actor
        await term.save()

        # Update terminology term count
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def delete_term(
        term_id: str,
        updated_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> bool:
        """Soft-delete a term."""
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return False

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        term.status = "inactive"
        term.updated_at = datetime.now(timezone.utc)
        term.updated_by = actor
        await term.save()

        # Update terminology term count
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        return True

    # =========================================================================
    # VALIDATION
    # =========================================================================

    @staticmethod
    async def validate_value(
        terminology_id: Optional[str] = None,
        terminology_code: Optional[str] = None,
        value: str = ""
    ) -> tuple[bool, Optional[Term], Optional[str], Optional[Term]]:
        """
        Validate a value against a terminology.

        Matches against code, value, OR aliases.

        Args:
            terminology_id: Terminology ID
            terminology_code: Terminology code (alternative to ID)
            value: Value to validate

        Returns:
            Tuple of (is_valid, matched_term, matched_via, suggestion)
            matched_via is 'code', 'value', or 'alias' if matched
        """
        # Find terminology
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif terminology_code:
            terminology = await Terminology.find_one({"code": terminology_code})
        else:
            return (False, None, None, None)

        if not terminology:
            return (False, None, None, None)

        # Prepare value for comparison
        compare_value = value if terminology.case_sensitive else value.lower()

        # Get all active terms in this terminology
        terms = await Term.find({
            "terminology_id": terminology.terminology_id,
            "status": "active"
        }).to_list()

        # Try to find match by code, value, or alias
        for term in terms:
            if terminology.case_sensitive:
                # Check code
                if term.code == value:
                    return (True, term, "code", None)
                # Check value
                if term.value == value:
                    return (True, term, "value", None)
                # Check aliases
                if value in term.aliases:
                    return (True, term, "alias", None)
            else:
                # Case-insensitive matching
                if term.code.lower() == compare_value:
                    return (True, term, "code", None)
                if term.value.lower() == compare_value:
                    return (True, term, "value", None)
                if any(alias.lower() == compare_value for alias in term.aliases):
                    return (True, term, "alias", None)

        # No exact match - try to find suggestion
        # Simple approach: find terms that start with the value
        suggestion = None
        if len(value) >= 2:
            for t in terms:
                t_value = t.value if terminology.case_sensitive else t.value.lower()
                if t_value.startswith(compare_value):
                    suggestion = t
                    break

        return (False, None, None, suggestion)

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _to_terminology_response(t: Terminology) -> TerminologyResponse:
        """Convert Terminology document to response model."""
        return TerminologyResponse(
            terminology_id=t.terminology_id,
            code=t.code,
            name=t.name,
            description=t.description,
            case_sensitive=t.case_sensitive,
            allow_multiple=t.allow_multiple,
            extensible=t.extensible,
            metadata=t.metadata,
            status=t.status,
            term_count=t.term_count,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    def _to_term_response(t: Term) -> TermResponse:
        """Convert Term document to response model."""
        return TermResponse(
            term_id=t.term_id,
            terminology_id=t.terminology_id,
            terminology_code=t.terminology_code,
            code=t.code,
            value=t.value,
            aliases=t.aliases,
            label=t.label,
            description=t.description,
            sort_order=t.sort_order,
            parent_term_id=t.parent_term_id,
            translations=t.translations,
            metadata=t.metadata,
            status=t.status,
            deprecated_reason=t.deprecated_reason,
            replaced_by_term_id=t.replaced_by_term_id,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    async def _create_audit_log(
        term_id: str,
        terminology_id: str,
        action: str,
        changed_by: Optional[str] = None,
        changed_fields: list[str] = None,
        previous_values: dict = None,
        new_values: dict = None,
        comment: Optional[str] = None
    ):
        """Create an audit log entry for a term change."""
        audit_entry = TermAuditLog(
            term_id=term_id,
            terminology_id=terminology_id,
            action=action,
            changed_by=changed_by,
            changed_fields=changed_fields or [],
            previous_values=previous_values or {},
            new_values=new_values or {},
            comment=comment
        )
        await audit_entry.insert()
