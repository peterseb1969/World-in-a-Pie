"""Service for resolving template inheritance."""

from typing import Optional

from ..models.template import Template
from ..models.field import FieldDefinition
from ..models.rule import ValidationRule


MAX_INHERITANCE_DEPTH = 10


class InheritanceError(Exception):
    """Error during inheritance resolution."""
    pass


class InheritanceService:
    """
    Service for resolving template inheritance.

    Templates can extend other templates, inheriting fields and rules.
    Child templates can override parent fields by name.
    """

    @staticmethod
    async def resolve_template(template: Template) -> Template:
        """
        Resolve a template's inheritance chain and return a flattened template.

        The resolved template contains:
        - All fields from parent(s), with child overrides applied
        - All rules from parent(s) merged with child rules
        - Child's identity_fields (if specified) replace parent's

        Args:
            template: The template to resolve

        Returns:
            A new Template object with inheritance resolved

        Raises:
            InheritanceError: If circular inheritance or max depth exceeded
        """
        if not template.extends:
            # No inheritance, return as-is
            return template

        # Build inheritance chain
        chain = await InheritanceService._build_inheritance_chain(template)

        # Merge fields from all templates (parent to child)
        resolved_fields = InheritanceService._merge_fields(chain)

        # Merge rules from all templates
        resolved_rules = InheritanceService._merge_rules(chain)

        # Identity fields: use child's if specified, otherwise parent's
        resolved_identity_fields = InheritanceService._resolve_identity_fields(chain)

        # Create resolved template (copy of original with resolved data)
        return Template(
            id=template.id,
            template_id=template.template_id,
            value=template.value,
            label=template.label,
            description=template.description,
            version=template.version,
            extends=template.extends,  # Keep original extends reference
            identity_fields=resolved_identity_fields,
            fields=resolved_fields,
            rules=resolved_rules,
            metadata=template.metadata,
            status=template.status,
            created_at=template.created_at,
            created_by=template.created_by,
            updated_at=template.updated_at,
            updated_by=template.updated_by,
        )

    @staticmethod
    async def _build_inheritance_chain(template: Template) -> list[Template]:
        """
        Build the inheritance chain from root to child.

        Args:
            template: The child template

        Returns:
            List of templates from root ancestor to child

        Raises:
            InheritanceError: If circular inheritance detected or max depth exceeded
        """
        chain = [template]
        seen_ids = {template.template_id}
        current = template
        depth = 0

        while current.extends:
            depth += 1
            if depth > MAX_INHERITANCE_DEPTH:
                raise InheritanceError(
                    f"Maximum inheritance depth ({MAX_INHERITANCE_DEPTH}) exceeded"
                )

            # Look up parent template
            parent = await Template.find_one({"template_id": current.extends})
            if not parent:
                raise InheritanceError(
                    f"Parent template '{current.extends}' not found"
                )

            # Check for circular inheritance
            if parent.template_id in seen_ids:
                raise InheritanceError(
                    f"Circular inheritance detected: {parent.template_id}"
                )

            seen_ids.add(parent.template_id)
            chain.append(parent)
            current = parent

        # Reverse to get root-to-child order
        chain.reverse()
        return chain

    @staticmethod
    def _merge_fields(chain: list[Template]) -> list[FieldDefinition]:
        """
        Merge fields from inheritance chain.

        Later templates (children) override earlier templates (parents)
        for fields with the same name. Each field is tagged with
        inherited=True/False and inherited_from (the source template_id).

        Args:
            chain: Templates from root to child

        Returns:
            Merged list of field definitions with inheritance info
        """
        fields_by_name: dict[str, FieldDefinition] = {}
        field_source: dict[str, str] = {}  # field_name -> template_id

        for template in chain:
            for field in template.fields:
                # Child overrides parent by name
                fields_by_name[field.name] = field
                field_source[field.name] = template.template_id

        # The child template is the last in the chain
        child_template_id = chain[-1].template_id

        # Tag each field with inheritance info
        result = []
        for field in fields_by_name.values():
            source_id = field_source[field.name]
            is_inherited = source_id != child_template_id
            tagged = field.model_copy(update={
                "inherited": is_inherited,
                "inherited_from": source_id if is_inherited else None,
            })
            result.append(tagged)

        return result

    @staticmethod
    def _merge_rules(chain: list[Template]) -> list[ValidationRule]:
        """
        Merge rules from inheritance chain.

        All rules are included (no deduplication).

        Args:
            chain: Templates from root to child

        Returns:
            Merged list of validation rules
        """
        rules = []
        for template in chain:
            rules.extend(template.rules)
        return rules

    @staticmethod
    def _resolve_identity_fields(chain: list[Template]) -> list[str]:
        """
        Resolve identity fields from inheritance chain.

        The first template (from child to parent) with non-empty
        identity_fields wins.

        Args:
            chain: Templates from root to child

        Returns:
            Resolved identity fields
        """
        # Walk from child to root (reversed chain)
        for template in reversed(chain):
            if template.identity_fields:
                return template.identity_fields

        return []

    @staticmethod
    async def check_circular_inheritance(
        template_id: str,
        new_extends: Optional[str]
    ) -> bool:
        """
        Check if setting extends would create circular inheritance.

        Args:
            template_id: The template being modified
            new_extends: The proposed parent template ID

        Returns:
            True if circular inheritance would result
        """
        if not new_extends:
            return False

        if template_id == new_extends:
            return True

        # Walk up the proposed parent's chain
        seen_ids = {template_id}
        current_id = new_extends
        depth = 0

        while current_id:
            depth += 1
            if depth > MAX_INHERITANCE_DEPTH:
                return True  # Treat excessive depth as circular

            if current_id in seen_ids:
                return True

            seen_ids.add(current_id)

            parent = await Template.find_one({"template_id": current_id})
            if not parent:
                break

            current_id = parent.extends

        return False

    @staticmethod
    async def get_children(template_id: str) -> list[Template]:
        """
        Get all templates that directly extend this template.

        Args:
            template_id: The parent template ID

        Returns:
            List of child templates
        """
        return await Template.find({"extends": template_id}).to_list()

    @staticmethod
    async def get_descendants(template_id: str) -> list[Template]:
        """
        Get all templates that extend this template (directly or indirectly).

        Args:
            template_id: The ancestor template ID

        Returns:
            List of descendant templates
        """
        descendants = []
        to_process = [template_id]
        seen = set()

        while to_process:
            current_id = to_process.pop()
            if current_id in seen:
                continue
            seen.add(current_id)

            children = await Template.find({"extends": current_id}).to_list()
            for child in children:
                descendants.append(child)
                to_process.append(child.template_id)

        return descendants
