"""`export-csv`: an archive's documents as one CSV per template.

The off-ramp from an archive to a spreadsheet, with no instance needed. It
is not a transform: it writes CSV files, not an archive, so it records no
``derived_from`` hop.

**Files.** ``<dir>/<namespace>/<TEMPLATE>.csv``, one per template that has
documents, named by the template's value (its id when the archive does not
carry the template).

**Rows.** Each document's latest version; with ``all_versions``, every
version row.

**Columns.** ``document_id``, ``version``, ``status``, then the data fields
flattened by dot path: a nested object's fields become ``parent.child``;
an array is written as JSON text. Top-level fields follow the order of the
template's latest version; nested and undeclared paths follow sorted. A
term field is written as the value the document holds plus a
``<path>__term_id`` column right after it: the term id, or a JSON list in
item order when the field is an array (``tags[0]``, ``tags[1]`` …). Empty
cells mean the field is absent or null.

**Reserved names.** ``document_id``, ``version``, ``status`` and every
``…__term_id`` name are the export's own columns. A data path with one of
those names is written as ``data:<path>`` (a field ``status`` becomes the
column ``data:status``), so neither overwrites the other. Should that name
be taken too, the export is refused (``ExportCollisionError``).
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .archive import ArchiveReader
from .csv_cells import escape_cell
from .definitions import UnresolvableTemplate
from .model import ArchiveModel

_BASE = ("document_id", "version", "status")
_INDEX = re.compile(r"\[(\d+)\]")


class ExportExistsError(FileExistsError):
    """A CSV the export would write exists and overwriting was not asked for."""


class ExportCollisionError(ValueError):
    """Two values of one row would be written to the same column."""


_DATA_PREFIX = "data:"


def _data_column(path: str) -> str:
    """The column a data path is written to: its own name, or ``data:<path>``
    when the name is one of the export's own columns."""
    if path in _BASE or path.endswith("__term_id"):
        return _DATA_PREFIX + path
    return path


def _flatten(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                yield from _flatten(value, path + ".")
            else:
                yield path, value
    elif prefix:
        yield prefix.rstrip("."), data


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    # A string a spreadsheet would run as a formula is escaped (csv_cells).
    return escape_cell(value)


def _term_columns(row: dict[str, Any]) -> dict[str, Any]:
    """``<path>__term_id`` values from the stored term references."""
    grouped: dict[str, list[tuple[tuple[int, ...], str]]] = {}
    for ref in row.get("term_references") or ():
        path, term_id = ref.get("field_path"), ref.get("term_id")
        if not isinstance(path, str) or not isinstance(term_id, str):
            continue
        indices = tuple(int(i) for i in _INDEX.findall(path))
        grouped.setdefault(_INDEX.sub("", path), []).append((indices, term_id))
    out: dict[str, Any] = {}
    for path, refs in grouped.items():
        if len(refs) == 1 and not refs[0][0]:
            out[f"{path}__term_id"] = refs[0][1]
        else:
            out[f"{path}__term_id"] = [t for _i, t in sorted(refs)]
    return out


def _row_values(row: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for path, value in _flatten(row.get("data") or {}):
        column = _data_column(path)
        if column in values:
            raise ExportCollisionError(
                f"document {row.get('document_id')} v{row.get('version')}: data fields "
                f"{path!r} and {column!r} would both be written to column {column!r}"
            )
        values[column] = value
    for column, value in _term_columns(row).items():
        if column in values:
            raise ExportCollisionError(
                f"document {row.get('document_id')} v{row.get('version')}: the term column "
                f"{column!r} collides with a data field of that name"
            )
        values[column] = value
    return values


@dataclass
class CsvFile:
    """One written (or, in a dry run, planned) CSV."""

    namespace: str
    template: str
    path: Path
    rows: int = 0
    columns: list[str] = field(default_factory=list)


def _order(columns: set[str], declared: list[str]) -> list[str]:
    """Declared top-level fields first, in template order, each followed by
    its nested paths and its term-id column; then everything else, sorted."""
    ordered: list[str] = []
    placed: set[str] = set()

    def belongs(column: str, name: str) -> bool:
        if column.startswith(_DATA_PREFIX):
            column = column[len(_DATA_PREFIX):]
        return column == name or column.startswith(name + ".") \
            or column == f"{name}__term_id" or column.startswith(name + "__")

    for name in declared:
        group = sorted(c for c in columns if belongs(c, name) and c not in placed)
        # The value before its term id; nested paths in sorted order.
        group.sort(key=lambda c: (c.endswith("__term_id"), c))
        ordered += group
        placed.update(group)
    rest = sorted(columns - placed, key=lambda c: (c.removesuffix("__term_id"), c))
    return ordered + rest


def export_csv(
    reader: ArchiveReader,
    model: ArchiveModel,
    out_dir: str | Path,
    *,
    all_versions: bool = False,
    overwrite: bool = False,
    dry_run: bool = False,
) -> list[CsvFile]:
    out_dir = Path(out_dir)

    def keep(row: dict[str, Any]) -> bool:
        if all_versions:
            return True
        entry = model.documents.get(row.get("document_id") or "")
        return entry is not None and row.get("version") == entry.max_version

    # Pass 1: which files, their row counts and their columns.
    files: dict[tuple[str, str], CsvFile] = {}
    columns: dict[tuple[str, str], set[str]] = {}
    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            if not keep(row):
                continue
            template_id = str(row.get("template_id") or "")
            key = (ns, template_id)
            if key not in files:
                tpl = model.templates.get(template_id)
                name = tpl.value if tpl is not None and tpl.value else template_id
                files[key] = CsvFile(ns, name, out_dir / ns / f"{name}.csv")
                columns[key] = set()
            files[key].rows += 1
            columns[key].update(_row_values(row))

    for key, csv_file in files.items():
        tpl = model.templates.get(key[1])
        declared: list[str] = []
        if tpl is not None and tpl.latest_version is not None:
            # Inheritance resolved, so a parent's fields keep their place;
            # unresolvable (a parent outside the archive): data order only.
            try:
                resolved = model.definitions.resolve_template(key[1], tpl.latest_version.version)
                declared = [str(f["name"]) for f in resolved.fields if isinstance(f.get("name"), str)]
            except UnresolvableTemplate:
                declared = []
        csv_file.columns = [*_BASE, *_order(columns[key], declared)]

    existing = [str(f.path) for f in files.values() if f.path.exists()]
    if existing and not overwrite and not dry_run:
        raise ExportExistsError(f"CSV file(s) exist: {', '.join(existing)}")
    if dry_run:
        return sorted(files.values(), key=lambda f: (f.namespace, f.template))

    # Pass 2: the rows, written as they stream past.
    handles: dict[tuple[str, str], Any] = {}
    writers: dict[tuple[str, str], csv.DictWriter] = {}
    try:
        for key, csv_file in files.items():
            csv_file.path.parent.mkdir(parents=True, exist_ok=True)
            handles[key] = csv_file.path.open("w", newline="", encoding="utf-8")
            writers[key] = csv.DictWriter(handles[key], fieldnames=csv_file.columns)
            # Column names come from template field names: escaped like cells.
            writers[key].writerow({c: escape_cell(c) for c in csv_file.columns})
        for ns in model.namespaces:
            for row in reader.read_entities("documents", namespace=ns):
                if not keep(row):
                    continue
                key = (ns, str(row.get("template_id") or ""))
                values = {c: _cell(v) for c, v in _row_values(row).items()}
                values.update({b: _cell(row.get(b)) for b in _BASE})
                writers[key].writerow(values)
    finally:
        for handle in handles.values():
            handle.close()
    return sorted(files.values(), key=lambda f: (f.namespace, f.template))
