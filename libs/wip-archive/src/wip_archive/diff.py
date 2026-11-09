"""`diff`: what differs between two archives, row by row.

Rows are matched by their identity in the archive, per namespace and entity
type: a terminology, term, file or Registry row by its id, a template or
document version by ``(id, version)``, a term relation by its two terms and
type. Referents (archive-wide) match by ``entry_id``, blobs by file id.

A matched row is ``changed`` when its content differs in any field, compared
as canonical JSON, so key order and formatting never count. For a capped
sample of changed rows the differing top-level fields are named.

Memory is one digest per row of the first archive; the second streams past
it. Naming the fields of the sampled changes costs one more pass over each
archive, for those rows only: the comparison keeps a digest of a first-archive
row, not the row, so both sides are read again for the sampled keys.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .archive import ENTITY_FILES, ArchiveReader
from .transform import input_chain

SAMPLE_CAP = 5

# Key fields per entity type; templates and documents are per version.
_KEYS: dict[str, tuple[str, ...]] = {
    "terminologies": ("terminology_id",),
    "terms": ("term_id",),
    "term_relations": ("source_term_id", "target_term_id", "relation_type"),
    "templates": ("template_id", "version"),
    "documents": ("document_id", "version"),
    "files": ("file_id",),
    "registry_entries": ("entry_id",),
}


def _digest(row: dict[str, Any]) -> bytes:
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).digest()[:16]


def _key(entity_type: str, row: dict[str, Any]) -> str:
    return "/".join(str(row.get(k)) for k in _KEYS[entity_type])


@dataclass
class TypeDiff:
    """Added, removed and changed rows of one entity type in one namespace."""

    added: int = 0
    removed: int = 0
    changed: int = 0
    unchanged: int = 0
    added_samples: list[str] = field(default_factory=list)
    removed_samples: list[str] = field(default_factory=list)
    # key -> the top-level fields that differ
    changed_samples: dict[str, list[str]] = field(default_factory=dict)

    @property
    def differs(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added, "removed": self.removed, "changed": self.changed,
            "unchanged": self.unchanged,
            "added_samples": self.added_samples, "removed_samples": self.removed_samples,
            "changed_samples": self.changed_samples,
        }


@dataclass
class ArchiveDiff:
    """Per (namespace, entity type) row differences, plus what lies outside
    the namespaces: referents, blobs and the manifests' chains."""

    types: dict[tuple[str, str], TypeDiff] = field(default_factory=dict)
    referents: TypeDiff = field(default_factory=TypeDiff)
    blobs: TypeDiff = field(default_factory=TypeDiff)
    namespaces_only_a: list[str] = field(default_factory=list)
    namespaces_only_b: list[str] = field(default_factory=list)
    chain_a: list[dict[str, Any]] = field(default_factory=list)
    chain_b: list[dict[str, Any]] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not (
            any(t.differs for t in self.types.values())
            or self.referents.differs or self.blobs.differs
            or self.namespaces_only_a or self.namespaces_only_b
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identical": self.identical,
            "namespaces_only_a": self.namespaces_only_a,
            "namespaces_only_b": self.namespaces_only_b,
            "types": [
                {"namespace": ns, "entity_type": t, **d.to_dict()}
                for (ns, t), d in sorted(self.types.items()) if d.differs or d.unchanged
            ],
            "referents": self.referents.to_dict(),
            "blobs": self.blobs.to_dict(),
            "chain_a": self.chain_a,
            "chain_b": self.chain_b,
        }


def _sample(samples: list[str], key: str) -> None:
    if len(samples) < SAMPLE_CAP:
        samples.append(key)


def _compare(
    a_rows: Iterator[tuple[str, dict[str, Any]]],
    b_rows: Iterator[tuple[str, dict[str, Any]]],
    out: TypeDiff,
) -> list[str]:
    """Fill ``out`` from two keyed row streams; returns the sampled changed
    keys, whose fields the caller names from the full rows."""
    seen: dict[str, bytes] = {key: _digest(row) for key, row in a_rows}
    changed_keys: list[str] = []
    for key, row in b_rows:
        before = seen.pop(key, None)
        if before is None:
            out.added += 1
            _sample(out.added_samples, key)
        elif before != _digest(row):
            out.changed += 1
            if len(changed_keys) < SAMPLE_CAP:
                changed_keys.append(key)
        else:
            out.unchanged += 1
    out.removed = len(seen)
    for key in sorted(seen)[:SAMPLE_CAP]:
        out.removed_samples.append(key)
    return changed_keys


def _differing_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if _digest({"v": a.get(k)}) != _digest({"v": b.get(k)}))


def _keyed(reader: ArchiveReader, entity_type: str, ns: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for row in reader.read_entities(entity_type, namespace=ns):
        yield _key(entity_type, row), row


def _blobs(reader: ArchiveReader) -> Iterator[tuple[str, dict[str, Any]]]:
    for file_id in reader.list_blobs():
        digest = hashlib.sha256()
        with reader.open_blob(file_id) as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        yield file_id, {"sha256": digest.hexdigest()}


def diff_archives(a: ArchiveReader, b: ArchiveReader) -> ArchiveDiff:
    result = ArchiveDiff()
    ns_a, ns_b = set(a.list_namespaces()), set(b.list_namespaces())
    result.namespaces_only_a = sorted(ns_a - ns_b)
    result.namespaces_only_b = sorted(ns_b - ns_a)
    result.chain_a = input_chain(a.read_manifest_raw())
    result.chain_b = input_chain(b.read_manifest_raw())

    for ns in sorted(ns_a | ns_b):
        for entity_type in ENTITY_FILES:
            out = TypeDiff()
            a_rows = _keyed(a, entity_type, ns) if ns in ns_a else iter(())
            b_rows = _keyed(b, entity_type, ns) if ns in ns_b else iter(())
            changed = _compare(a_rows, b_rows, out)
            if changed:
                wanted = set(changed)
                before = {k: r for k, r in _keyed(a, entity_type, ns) if k in wanted}
                after = {k: r for k, r in _keyed(b, entity_type, ns) if k in wanted}
                out.changed_samples = {k: _differing_fields(before[k], after[k]) for k in changed}
            if out.differs or out.unchanged:
                result.types[(ns, entity_type)] = out

    # The referent table is small: held whole, so changes name their fields.
    ref_a = {str(r.get("entry_id", "")): r for r in a.read_referents()}
    ref_b = {str(r.get("entry_id", "")): r for r in b.read_referents()}
    for key in _compare(iter(ref_a.items()), iter(ref_b.items()), result.referents):
        result.referents.changed_samples[key] = _differing_fields(ref_a[key], ref_b[key])
    for key in _compare(_blobs(a), _blobs(b), result.blobs):
        result.blobs.changed_samples[key] = ["content"]
    return result
