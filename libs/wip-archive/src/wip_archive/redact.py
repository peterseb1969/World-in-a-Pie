"""The `redact` surgery: clear named fields from every version of a
template's documents (design doc §6.3 item 4).

**What is redacted.** Dot paths per template (``notes``,
``contact.email``): each named leaf is set to null in every version row,
whatever its status, so no history keeps the value. A path names its field in
every element of every array on the way: ``contacts.email`` clears the email
of each contact, and an array leaf (``tags``) is cleared whole. Bracketed
indices (``contacts[0].email``) are refused: an index names different
elements in different versions, and redacting one element leaves the value in
its neighbours.

**What is refused.** A path whose top-level field is an identity field of
any version: identity values also live in the document's Registry row (its
primary key's hash and a synonym in clear), and changing them is a fork.
Every segment is checked against the templates: the top-level field against
the template, a nested one against the fields of the object's or the array
items' ``template_ref`` / ``array_template_ref`` (which must be in the
archive), and nothing may follow a field of another type. An object field
without a template declares no keys, so a path into it is not checked there.
Last, a path that clears no value in any row is refused, so neither a typo
nor an empty field is ever recorded as a redaction.

**What goes with a value.** A redacted term, reference or file field drops
its snapshots (``term_references``, ``references``, ``file_references``
under that path, array elements included). A file no remaining document
references leaves the archive, blob included; a file another document still
references stays. A row the redaction changes drops its
``metadata.warnings``: validation warnings quote values and paths.

**What is left alone.** The Registry rows: a document's ``search_values``
come from its primary key and synonyms only, never from its data, and the
identity values that are there cannot be redacted. A synonym a caller
registered by hand with the same value is not touched; redaction works on
fields. Timestamps and writers of the rows are kept: redaction rewrites
history in place rather than writing a version, and the ``derived_from``
hop declares it (``params_redacted: true``, the paths without values).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .definitions import UnresolvableTemplate
from .model import AmbiguousNameError, ArchiveModel
from .surgery import SurgeryError, require_same_identity

_INDEX = re.compile(r"\[\d+\]")
# An object field with no template: it declares no keys to check a path by.
_FREE = object()


class RedactionRefused(SurgeryError):
    """The redaction cannot be done as asked; nothing is written."""


def _covers(path: str, snapshot_path: str) -> bool:
    """Whether a snapshot's field path lies under a redacted path. Snapshots
    name array elements (``contacts[0].email``, ``tags[3]``); a redacted path
    covers every element."""
    plain = _INDEX.sub("", snapshot_path)
    return plain == path or plain.startswith(path + ".")


def _null(data: dict[str, Any], path: str) -> int:
    """Set the leaf a dot path names to null, in every element of every array
    on the way; returns how many values there were."""
    parts = path.split(".")

    def walk(node: Any, i: int) -> int:
        if isinstance(node, list):
            return sum(walk(item, i) for item in node)
        if not isinstance(node, dict):
            return 0
        key = parts[i]
        if i < len(parts) - 1:
            return walk(node.get(key), i + 1)
        if node.get(key) is None:
            return 0
        node[key] = None
        return 1

    return walk(data, 0)


def _copy_data(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _copy_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_copy_data(v) for v in data]
    return data


@dataclass
class _Declared:
    """What a template declares across its versions: each field name's
    definitions, the identity fields, and one resolved template."""

    fields: dict[str, list[dict[str, Any]]]
    identity: set[str]
    resolved: dict[str, Any] | None


def _declared(model: ArchiveModel, node: Any) -> _Declared:
    fields: dict[str, list[dict[str, Any]]] = {}
    identity: set[str] = set()
    resolved: dict[str, Any] | None = None
    for version in sorted(node.versions):
        try:
            tpl = model.definitions.resolve_template(node.template_id, version)
        except UnresolvableTemplate:  # its own row is all the archive can say
            row = model.definitions.template(node.template_id, version) or {}
            defs, ids = row.get("fields") or [], row.get("identity_fields") or []
        else:
            defs, ids = tpl.fields, tpl.identity_fields
            resolved = tpl.as_template_dict()
        for f in defs:
            if f.get("name"):
                fields.setdefault(str(f["name"]), []).append(f)
        identity |= set(ids)
    return _Declared(fields, identity, resolved)


def _children(model: ArchiveModel, label: str, f: dict[str, Any]) -> dict[str, list[dict[str, Any]]] | object | None:
    """The fields under an object field or an array of objects: their
    template's (from the archive), _FREE for an object without one, None for
    any other type."""
    kind = f.get("type")
    if kind == "object":
        ref = f.get("template_ref")
    elif kind == "array" and f.get("array_item_type") == "object":
        ref = f.get("array_template_ref")
    else:
        return None
    if not ref:
        return _FREE
    try:
        node = model.resolve_template(str(ref))
    except AmbiguousNameError as e:
        raise RedactionRefused(f"{label}: {e}") from e
    if node is None:
        raise RedactionRefused(
            f"{label}: {f.get('name')!r} takes its fields from template {ref!r}, which is "
            "not in this archive; its fields cannot be checked")
    return _declared(model, node).fields


def _check_path(model: ArchiveModel, label: str, declared: _Declared, path: str) -> None:
    if "[" in path or "]" in path:
        raise RedactionRefused(
            f"{label}.{path}: name a field without an index; a path covers every array "
            "element, and an index names different elements in different versions")
    segments = path.split(".")
    if not all(segments):
        raise RedactionRefused(f"{label}.{path}: an empty path segment")
    head = segments[0]
    if head in declared.identity:
        raise RedactionRefused(
            f"{label}.{path}: {head} is an identity field; its values are also in the "
            "document's Registry row, and changing identity is a fork")
    level: dict[str, list[dict[str, Any]]] | object = declared.fields
    walked = ""
    for segment in segments:
        if level is _FREE:
            return  # an untyped object: its keys are not declared anywhere
        assert isinstance(level, dict)
        defs = level.get(segment)
        if not defs:
            where = f"{label}.{walked}" if walked else label
            raise RedactionRefused(f"{where} declares no field {segment!r}")
        walked = f"{walked}.{segment}" if walked else segment
        if walked == path:
            return
        below = [c for c in (_children(model, f"{label}.{walked}", d) for d in defs) if c is not None]
        if not below:
            raise RedactionRefused(f"{label}.{walked} has no fields: nothing follows it in a path")
        level = _FREE if any(c is _FREE for c in below) else {
            k: v for c in below for k, v in c.items()  # type: ignore[union-attr,attr-defined]
        }


@dataclass
class RedactionPlan:
    """Paths per template id, and the files that leave with them."""

    paths: dict[str, list[str]]
    labels: dict[str, str]
    templates: dict[str, dict[str, Any]]  # template_id -> a resolved template, for identity
    files_dropped: set[str] = field(default_factory=set)
    rows_redacted: int = 0
    values_cleared: int = 0

    def keep_row(self, _ns: str, entity_type: str, row: dict[str, Any]) -> bool:
        if entity_type == "files":
            return row.get("file_id") not in self.files_dropped
        return True

    def redacted(
        self, row: dict[str, Any], per_path: dict[str, int] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """The row with its paths cleared and their snapshots dropped, and
        how many data values that cleared (also counted per path into
        ``per_path`` when given). A changed row drops its metadata's
        warnings, which quote values and paths."""
        paths = self.paths.get(str(row.get("template_id") or ""), [])
        data = _copy_data(row.get("data") or {})
        cleared = 0
        for path in paths:
            n = _null(data, path)
            cleared += n
            if per_path is not None:
                per_path[path] = per_path.get(path, 0) + n

        def keep(snapshot: dict[str, Any]) -> bool:
            sp = snapshot.get("field_path")
            return not (isinstance(sp, str) and any(_covers(p, sp) for p in paths))

        out = {**row, "data": data}
        for key in ("term_references", "references", "file_references"):
            out[key] = [s for s in row.get(key) or [] if keep(s)]
        metadata = row.get("metadata")
        if out != row and isinstance(metadata, dict) and metadata.get("warnings"):
            out["metadata"] = {**metadata, "warnings": []}
        return out, cleared

    def rewrite_row(self, _ns: str, entity_type: str, row: dict[str, Any]) -> list[dict[str, Any]]:
        if entity_type != "documents" or str(row.get("template_id") or "") not in self.paths:
            return [row]
        out, _cleared = self.redacted(row)
        if out == row:
            return [row]
        template = self.templates[str(row["template_id"])]
        require_same_identity(row, out["data"], template, out.get("term_references"),
                              out.get("references"))
        return [out]

    def params(self) -> dict[str, Any]:
        """What the hop records: template names and paths, never values."""
        return {"fields": {self.labels[t]: list(p) for t, p in sorted(self.paths.items())}}

    def summary(self) -> dict[str, Any]:
        return {"rows_redacted": self.rows_redacted, "values_cleared": self.values_cleared,
                "files_dropped": len(self.files_dropped)}


def plan_redaction(reader: Any, model: ArchiveModel, spec: dict[str, list[str]]) -> RedactionPlan:
    """``spec`` maps a template name (id, value, ``ns:VALUE``) to the field
    paths to clear. Raises RedactionRefused for an unknown or ambiguous
    template, an indexed, undeclared or identity path, or a path that clears
    nothing in any row."""
    if not spec:
        raise RedactionRefused("name at least one template and field to redact")
    paths: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    templates: dict[str, dict[str, Any]] = {}
    for name, fields in spec.items():
        try:
            node = model.resolve_template(name)
        except AmbiguousNameError as e:
            raise RedactionRefused(str(e)) from e
        if node is None:
            raise RedactionRefused(f"no template {name!r} in this archive")
        declared = _declared(model, node)
        for path in fields:
            _check_path(model, node.label, declared, path)
        paths[node.template_id] = list(fields)
        labels[node.template_id] = f"{node.namespace}:{node.label}"
        templates[node.template_id] = declared.resolved or {
            "identity_fields": sorted(declared.identity), "fields": []}

    plan = RedactionPlan(paths=paths, labels=labels, templates=templates)
    cleared_by_path: dict[str, dict[str, int]] = {t: dict.fromkeys(p, 0) for t, p in paths.items()}
    remaining: set[str] = set()
    removed: set[str] = set()
    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            before = {f.get("file_id") for f in row.get("file_references") or ()}
            template_id = str(row.get("template_id") or "")
            if template_id in paths:
                out, cleared = plan.redacted(row, cleared_by_path[template_id])
                if out != row:
                    plan.rows_redacted += 1
                    plan.values_cleared += cleared
                after = {f.get("file_id") for f in out.get("file_references") or ()}
                removed |= before - after
                remaining |= after
            else:
                remaining |= before
    empty = [f"{labels[t]}.{p}" for t, counts in cleared_by_path.items() for p, n in counts.items() if n == 0]
    if empty:
        raise RedactionRefused(
            f"{', '.join(empty)} has no value in any document of this archive: nothing to "
            "redact (a misspelt key, or a field never filled)")
    plan.files_dropped = {f for f in removed - remaining if isinstance(f, str)}
    return plan
