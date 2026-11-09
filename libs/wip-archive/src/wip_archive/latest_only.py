"""The `latest-only` plan: each document's newest version, and the versions
something still names.

A document's history is its version rows. ``latest-only`` keeps the highest
version of every document, whatever its status, so a document retired in
its newest version stays retired. Two kinds of rows survive besides:

- **A version a pinned reference names.** A reference written with
  ``version_strategy: "pinned"`` records the version it saw
  (``resolved.version``), and the platform treats that pair as a reference
  to that version: hard-deleting the version is refused while such a
  reference exists. Dropping the version would therefore break a reference
  the platform guards, so the named version travels. A kept older version
  can pin further versions; those travel too.
- **Definitions are untouched.** Every template version stays, including
  versions only dropped rows were pinned to: definitions are small, and a
  template's history is not the documents' to decide.

A file row referenced only by dropped versions leaves with them (its blob
too); a file row nothing in the input referenced stays as it was. The output
manifest records ``include_all_versions: false``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .archive import ArchiveReader
from .model import ArchiveModel, row_version


@dataclass
class LatestOnlyPlan:
    """The document rows and files latest-only keeps."""

    rows: set[tuple[str, int]] = field(default_factory=set)
    files_dropped: set[str] = field(default_factory=set)
    pinned_versions_kept: int = 0
    versions_dropped: int = 0

    def keep_row(self, _namespace: str, entity_type: str, row: dict[str, Any]) -> bool:
        if entity_type == "documents":
            return _key(row) in self.rows
        if entity_type == "files":
            return row.get("file_id") not in self.files_dropped
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "versions_dropped": self.versions_dropped,
            "pinned_versions_kept": self.pinned_versions_kept,
        }


def _pinned(row: dict[str, Any]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for ref in row.get("references") or ():
        if ref.get("reference_type") != "document" or ref.get("version_strategy") != "pinned":
            continue
        resolved = ref.get("resolved") or {}
        doc_id = resolved.get("document_id")
        if isinstance(doc_id, str) and resolved.get("version") is not None:
            out.add((doc_id, row_version(resolved)))
    return out


def _key(row: dict[str, Any]) -> tuple[str, int] | None:
    """A document row's (document_id, version), its version counted as the
    model counts it (row_version), so a row the model indexed is never one
    this plan cannot name."""
    doc_id = row.get("document_id")
    if isinstance(doc_id, str) and doc_id:
        return (doc_id, row_version(row))
    return None


def _files(row: dict[str, Any]) -> set[str]:
    return {f["file_id"] for f in row.get("file_references") or ()
            if isinstance(f.get("file_id"), str)}


def plan_latest_only(reader: ArchiveReader, model: ArchiveModel) -> LatestOnlyPlan:
    plan = LatestOnlyPlan()
    present: set[tuple[str, int]] = set()
    wanted: set[tuple[str, int]] = set()
    files_kept: set[str] = set()
    files_seen: set[str] = set()

    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            key = _key(row)
            if key is None:
                continue
            present.add(key)
            files = _files(row)
            files_seen |= files
            entry = model.documents.get(key[0])
            if entry is not None and key[1] == entry.max_version:
                plan.rows.add(key)
                files_kept |= files
                wanted |= _pinned(row)

    # Older versions pinned references name, followed until nothing new.
    pending = (wanted & present) - plan.rows
    while pending:
        plan.rows |= pending
        plan.pinned_versions_kept += len(pending)
        wanted = set()
        for ns in model.namespaces:
            for row in reader.read_entities("documents", namespace=ns):
                if _key(row) in pending:
                    files_kept |= _files(row)
                    wanted |= _pinned(row)
        pending = (wanted & present) - plan.rows

    plan.versions_dropped = len(present) - len(plan.rows)
    plan.files_dropped = files_seen - files_kept
    return plan
