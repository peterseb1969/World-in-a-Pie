"""Service for resolving template inheritance."""

from typing import cast

from ..models.field import FieldDefinition
from ..models.rule import ValidationRule
from ..models.template import Template

MAX_INHERITANCE_DEPTH = 10


class InheritanceError(Exception):
    """A template's inheritance chain cannot be resolved.

    ``rule`` names why: ``unpinned_parent`` (extends without an
    extends_version), ``missing_parent`` (the pinned parent version does not
    exist), ``circular_inheritance`` or ``max_depth``.
    """

    def __init__(self, message: str, rule: str) -> None:
        super().__init__(message)
        self.rule = rule


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

        The resolved template is the child with three properties merged
        along the chain:
        - fields: parent fields first, child fields override by name
        - rules: concatenated root to child
        - identity_fields: the child's if non-empty, else the nearest ancestor's
        Every other property (usage, versioned, endpoint lists, header_fields,
        reporting, extends_version, renames, ...) is the child's own; a
        relationship or versioned:false template stays one after resolution.

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

        # A copy of the child with the merged properties replaced; never
        # saved. Building a new Template from a list of arguments dropped
        # every property the list missed.
        return cast(Template, template.model_copy(update={
            "identity_fields": resolved_identity_fields,
            "fields": resolved_fields,
            "rules": resolved_rules,
        }))

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
        ancestors = await InheritanceService.walk_ancestors(
            template.template_id, template.extends, template.extends_version
        )
        # Root-to-child order
        return [*reversed(ancestors), template]

    @staticmethod
    async def walk_ancestors(
        template_id: str | None,
        extends: str | None,
        extends_version: int | None,
        overrides: dict[tuple[str, int], Template] | None = None,
    ) -> list[Template]:
        """Walk a template's pinned parent chain, nearest parent first.

        The one walk every check uses: the read path (resolution) and the
        write paths (create, new version, activation), so a chain a write
        accepts is a chain a read can resolve. ``template_id`` is the
        template being walked from (None for one not yet registered);
        ``overrides`` supplies (template_id, version) entries to use instead
        of the stored ones — an activation set whose references are
        normalized in memory but not yet saved.

        Raises:
            InheritanceError: unpinned or missing parent, a cycle, or a chain
            deeper than MAX_INHERITANCE_DEPTH.
        """
        ancestors: list[Template] = []
        seen_ids = {template_id} if template_id else set()
        current_id, current_extends, current_version = template_id, extends, extends_version
        depth = 0

        while current_extends:
            depth += 1
            if depth > MAX_INHERITANCE_DEPTH:
                raise InheritanceError(
                    f"Maximum inheritance depth ({MAX_INHERITANCE_DEPTH}) exceeded",
                    "max_depth",
                )

            # Schema inheritance resolves to an explicit parent version, never
            # "latest", so a child's resolved schema cannot shift when the
            # parent ships a new version. A template without extends_version
            # is a data-integrity error, not a cue to float to latest.
            if current_version is None:
                raise InheritanceError(
                    f"Template '{current_id}' extends "
                    f"'{current_extends}' without a pinned extends_version "
                    "(schema inheritance must pin an explicit version)",
                    "unpinned_parent",
                )
            parent = (overrides or {}).get((current_extends, current_version))
            if parent is None:
                parent = await Template.find_one({
                    "template_id": current_extends,
                    "version": current_version,
                })

            if not parent:
                raise InheritanceError(
                    f"Parent template '{current_extends}' not found "
                    f"at version {current_version}",
                    "missing_parent",
                )

            # A cycle is by template_id, not (template_id, version)
            if parent.template_id in seen_ids:
                raise InheritanceError(
                    f"Circular inheritance detected: {parent.template_id}",
                    "circular_inheritance",
                )

            seen_ids.add(parent.template_id)
            ancestors.append(parent)
            current_id = parent.template_id
            current_extends, current_version = parent.extends, parent.extends_version

        return ancestors

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
