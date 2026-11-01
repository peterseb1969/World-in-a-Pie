"""The `redact` surgery: clear named fields from every version of a
template's documents (design doc §6.3 item 4).

**What is redacted.** Field paths per template (``notes``,
``contact.email``): each named leaf is set to null in every version row,
whatever its status, so no history keeps the value. A path whose top-level
field is an identity field of any version is refused: identity values also
live in the document's Registry row (its primary key's hash and a synonym in
clear), and changing them is a fork. A path the template never declared is
refused too, so a typo cannot pass for a redaction.

**What goes with a value.** A redacted term, reference or file field drops
its snapshots (``term_references``, ``references``, ``file_references``
under that path). A file no remaining document references leaves the
archive, blob included; a file another document still references stays.

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

_HEAD = re.compile(r"[.\[]")


class RedactionRefused(SurgeryError):
    """The redaction cannot be done as asked; nothing is written."""


def _covers(path: str, snapshot_path: str) -> bool:
    """Whether a snapshot's field path lies under a redacted path."""
    return snapshot_path == path or snapshot_path.startswith((path + ".", path + "["))


def _null(data: dict[str, Any], path: str) -> bool:
    """Set the leaf at a dot path to null; True when there was a value."""
    parts = path.split(".")
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict) or not isinstance(current.get(part), dict):
            return False
        current = current[part]
    if not isinstance(current, dict) or current.get(parts[-1]) is None:
        return False
    current[parts[-1]] = None
    return True


def _copy_data(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _copy_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_copy_data(v) for v in data]
    return data


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

    def redacted(self, row: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """The row with its paths cleared and their snapshots dropped, and
        how many data values that cleared."""
        paths = self.paths.get(str(row.get("template_id") or ""), [])
        data = _copy_data(row.get("data") or {})
        cleared = sum(1 for path in paths if _null(data, path))

        def keep(snapshot: dict[str, Any]) -> bool:
            sp = snapshot.get("field_path")
            return not (isinstance(sp, str) and any(_covers(p, sp) for p in paths))

        out = {**row, "data": data}
        for key in ("term_references", "references", "file_references"):
            out[key] = [s for s in row.get(key) or [] if keep(s)]
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
    template, an undeclared path, or an identity field."""
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
        declared: set[str] = set()
        identity: set[str] = set()
        resolved: dict[str, Any] | None = None
        for version in sorted(node.versions):
            try:
                tpl = model.definitions.resolve_template(node.template_id, version)
            except UnresolvableTemplate:  # its own row is all the archive can say
                row = model.definitions.template(node.template_id, version) or {}
                declared |= {str(f["name"]) for f in row.get("fields") or [] if f.get("name")}
                identity |= set(row.get("identity_fields") or [])
                continue
            declared |= {str(f["name"]) for f in tpl.fields if f.get("name")}
            identity |= set(tpl.identity_fields)
            resolved = tpl.as_template_dict()
        for path in fields:
            head = _HEAD.split(path, maxsplit=1)[0]
            if head in identity:
                raise RedactionRefused(
                    f"{node.label}.{path}: {head} is an identity field; its values are also in the "
                    "document's Registry row, and changing identity is a fork")
            if head not in declared:
                raise RedactionRefused(f"{node.label} declares no field {head!r}")
        paths[node.template_id] = list(fields)
        labels[node.template_id] = f"{node.namespace}:{node.label}"
        templates[node.template_id] = resolved or {"identity_fields": sorted(identity), "fields": []}

    plan = RedactionPlan(paths=paths, labels=labels, templates=templates)
    remaining: set[str] = set()
    removed: set[str] = set()
    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            before = {f.get("file_id") for f in row.get("file_references") or ()}
            if str(row.get("template_id") or "") in paths:
                out, cleared = plan.redacted(row)
                if out != row:
                    plan.rows_redacted += 1
                    plan.values_cleared += cleared
                after = {f.get("file_id") for f in out.get("file_references") or ()}
                removed |= before - after
                remaining |= after
            else:
                remaining |= before
    plan.files_dropped = {f for f in removed - remaining if isinstance(f, str)}
    return plan
