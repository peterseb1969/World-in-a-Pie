"""The archive's definitions in full: templates, terminologies, terms and the
identity rows of entities the archive references but does not carry.

`ArchiveModel` keeps a compact summary of each template for graph analysis.
Validating a document needs the whole definition: every field with its
constraints, the cross-field rules, and the template it extends. This index
holds the raw rows — definitions are small next to documents — and resolves
inheritance the way template-store does, so an offline validator sees the same
effective schema the platform validated against.

What the archive cannot answer, it says: a parent template outside the archive
makes the resolution fail with the missing (template_id, version), never a
silent partial schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from wip_archive.archive import ArchiveReader

# Template-store's limit (template_store/services/inheritance_service.py).
MAX_INHERITANCE_DEPTH = 10


class UnresolvableTemplate(Exception):
    """A template's inheritance cannot be resolved from the archive."""

    def __init__(self, reason: str, *, missing: tuple[str, int] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing = missing


@dataclass(frozen=True)
class ResolvedTemplate:
    """A template version with its inheritance chain flattened.

    `fields`, `rules` and `identity_fields` follow template-store's merge:
    child fields override parent fields by name (keeping the parent's position),
    rules concatenate root to child, and the first non-empty `identity_fields`
    walking from the child towards the root wins. Every other property is the
    child's own.
    """

    template_id: str
    version: int
    namespace: str
    value: str
    row: dict[str, Any]
    fields: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]
    identity_fields: tuple[str, ...]
    chain: tuple[tuple[str, int], ...] = ()

    def field(self, name: str) -> dict[str, Any] | None:
        for f in self.fields:
            if f.get("name") == name:
                return f
        return None

    def as_template_dict(self) -> dict[str, Any]:
        """The resolved template in the shape the live validator receives."""
        resolved = dict(self.row)
        resolved["fields"] = [dict(f) for f in self.fields]
        resolved["rules"] = [dict(r) for r in self.rules]
        resolved["identity_fields"] = list(self.identity_fields)
        return resolved


def term_names(row: dict[str, Any]) -> tuple[str, ...]:
    """Every name a term carries, current `names` shape and legacy `aliases`.

    Engine archives carry `names: [{value, type, language, ...}]`; older
    toolkit archives carried a plain `aliases` list. Both are names in
    validation, exactly like the term's value.
    """
    names: list[str] = []
    for name in row.get("names") or ():
        value = name.get("value") if isinstance(name, dict) else name
        if isinstance(value, str) and value:
            names.append(value)
    for alias in row.get("aliases") or ():
        if isinstance(alias, str) and alias and alias not in names:
            names.append(alias)
    return tuple(names)


@dataclass
class DefinitionIndex:
    """Raw definition rows of every carried namespace, plus referents."""

    templates: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    terminologies: dict[str, dict[str, Any]] = field(default_factory=dict)
    terms: dict[str, dict[str, Any]] = field(default_factory=dict)
    terms_by_terminology: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Registry rows of entities referenced but not carried, by entry id.
    referents: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(
        cls, reader: ArchiveReader, namespaces: Iterable[str] | None = None
    ) -> DefinitionIndex:
        index = cls()
        for ns in namespaces if namespaces is not None else reader.list_namespaces():
            for row in reader.read_entities("terminologies", namespace=ns):
                index.add_terminology(row)
            for row in reader.read_entities("terms", namespace=ns):
                index.add_term(row)
            for row in reader.read_entities("templates", namespace=ns):
                index.add_template(row)
        index.add_referents(reader.read_referents())
        return index

    def add_terminology(self, row: dict[str, Any]) -> None:
        tid = row.get("terminology_id")
        if tid:
            self.terminologies[tid] = row

    def add_term(self, row: dict[str, Any]) -> None:
        term_id = row.get("term_id")
        if not term_id:
            return
        self.terms[term_id] = row
        self.terms_by_terminology.setdefault(
            row.get("terminology_id", "") or "", []
        ).append(row)

    def add_template(self, row: dict[str, Any]) -> None:
        template_id = row.get("template_id")
        if template_id:
            self.templates[(template_id, int(row.get("version", 1) or 1))] = row

    def add_referents(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            entry_id = row.get("entry_id")
            if entry_id:
                self.referents[entry_id] = row

    def template(self, template_id: str, version: int) -> dict[str, Any] | None:
        return self.templates.get((template_id, version))

    def resolve_template(self, template_id: str, version: int) -> ResolvedTemplate:
        """Flatten a template version's inheritance chain.

        Mirrors template-store's `InheritanceService`: the parent is looked up
        at the child's pinned `extends_version` (never "latest"), the chain is
        capped at `MAX_INHERITANCE_DEPTH`, and a template reappearing in its own
        chain is circular. Raises `UnresolvableTemplate` when the archive
        cannot answer — including a parent the archive does not carry.
        """
        row = self.template(template_id, version)
        if row is None:
            raise UnresolvableTemplate(
                f"template {template_id} v{version} is not in the archive",
                missing=(template_id, version),
            )

        chain = [row]
        seen = {template_id}
        current = row
        depth = 0
        while current.get("extends"):
            depth += 1
            if depth > MAX_INHERITANCE_DEPTH:
                raise UnresolvableTemplate(
                    f"maximum inheritance depth ({MAX_INHERITANCE_DEPTH}) exceeded"
                )
            parent_id = current["extends"]
            parent_version = current.get("extends_version")
            if parent_version is None:
                raise UnresolvableTemplate(
                    f"template {current.get('template_id')} extends {parent_id} "
                    "without a pinned extends_version"
                )
            parent = self.template(parent_id, int(parent_version))
            if parent is None:
                raise UnresolvableTemplate(
                    f"parent template {parent_id} v{parent_version} is not in the archive",
                    missing=(parent_id, int(parent_version)),
                )
            if parent_id in seen:
                raise UnresolvableTemplate(f"circular inheritance through {parent_id}")
            seen.add(parent_id)
            chain.append(parent)
            current = parent
        chain.reverse()  # root to child

        fields_by_name: dict[str, dict[str, Any]] = {}
        for tpl in chain:
            for f in tpl.get("fields") or ():
                fields_by_name[f.get("name")] = f
        rules: list[dict[str, Any]] = []
        for tpl in chain:
            rules.extend(tpl.get("rules") or ())
        identity: tuple[str, ...] = ()
        for tpl in reversed(chain):
            if tpl.get("identity_fields"):
                identity = tuple(tpl["identity_fields"])
                break

        return ResolvedTemplate(
            template_id=template_id,
            version=version,
            namespace=row.get("namespace", "") or "",
            value=row.get("value", "") or "",
            row=row,
            fields=tuple(fields_by_name.values()),
            rules=tuple(rules),
            identity_fields=identity,
            chain=tuple(
                (t.get("template_id", ""), int(t.get("version", 1) or 1)) for t in chain
            ),
        )
