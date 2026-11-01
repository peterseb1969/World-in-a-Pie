"""The shared core of every shaping transform: select rows, never edit them.

A shaping transform (``filter``, ``prune``, ``latest-only``, ``strip-blobs``)
decides which rows of an archive travel into a new archive. This module does
everything else, identically for each of them:

- **Rows travel verbatim or not at all.** A kept row is copied as read; no
  reference inside it is rewritten. Identity hashes therefore cannot change.
- **A cut entity becomes a referent.** When a kept row references an entity
  that no longer travels, that entity's Registry row (primary composite key
  and synonyms) moves from the namespace's ``registry_entries.jsonl`` into the
  archive-wide ``referents.jsonl``. The restore door resolves referents on the
  target install by those keys, never by the old id, so a reference crossing
  the cut stays resolvable wherever the target holds the entity. Referents
  the input already carried stay while something still references them.
- **What leaves with a cut entity:** its Registry row (with the synonyms in
  it), its legacy ``synonyms.jsonl`` rows, and, by default, the blobs of cut
  file rows. An entity that is cut and referenced by nothing kept leaves no
  trace.
- **Provenance.** The output's manifest is the input's raw manifest, keys the
  Manifest model does not know included, with counts recomputed and one
  ``derived_from`` hop appended in the frozen shape: the SHA-256 of the whole
  input file, the parameters verbatim, and the verdict of verifying the
  output.
- **Verify is a postcondition.** The output is assembled once uncompressed
  and verified offline with the document check; the verdict and finding
  counts go into the hop, and the archive is assembled for real. A ``fail``
  verdict still writes the archive: it declares itself broken, and the
  restore door refuses it unless the request accepts that.
"""

from __future__ import annotations

import copy
import hashlib
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .archive import ENTITY_FILES, ArchiveReader, ArchiveWriter
from .model import ArchiveModel, Finding
from .referents import ReferentCollector

# The id that names each entity type's row; Registry rows carry the same id
# as their entry_id. Term relations have no Registry row.
ENTITY_ID_KEYS: dict[str, str] = {
    "terminologies": "terminology_id",
    "terms": "term_id",
    "templates": "template_id",
    "documents": "document_id",
    "files": "file_id",
}

# Every entity type a transform selects among, in archive file order.
# Registry rows are not selected: they follow their entities.
SELECTABLE_TYPES: tuple[str, ...] = tuple(t for t in ENTITY_FILES if t != "registry_entries")

# (namespace, entity_type, row) -> whether the row travels.
KeepRow = Callable[[str, str, dict[str, Any]], bool]
# file_id -> whether the blob travels; called only for blobs of kept file rows.
KeepBlob = Callable[[str], bool]

_HASH_CHUNK = 1 << 20


class OutputExistsError(FileExistsError):
    """The output path exists and overwriting it was not asked for."""


def verdict(findings: list[Finding]) -> str:
    """``fail`` on any error, ``warn`` on any warning, else ``pass``."""
    severities = {f.severity for f in findings}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "warn"
    return "pass"


def finding_counts(findings: list[Finding]) -> dict[str, int]:
    """Findings per severity, every severity present."""
    out = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_chain(raw_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The input's ``derived_from`` chain as a list (a single object is a
    one-hop chain; absent is empty)."""
    chain = raw_manifest.get("derived_from")
    if chain is None:
        return []
    if isinstance(chain, dict):
        return [chain]
    return list(chain)


@dataclass
class TransformResult:
    """What a transform wrote and what verifying it found."""

    output: Path
    hop: dict[str, Any]
    model: ArchiveModel
    kept: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    referents: int = 0
    blobs_kept: int = 0
    blobs_dropped: int = 0

    @property
    def verdict(self) -> str:
        return str(self.hop["verify"]["verdict"])


def run_transform(
    input_path: str | Path,
    output_path: str | Path,
    *,
    transform: str,
    toolkit_version: str,
    params: dict[str, Any],
    keep_row: KeepRow,
    keep_blob: KeepBlob | None = None,
    summary: dict[str, Any] | None = None,
    params_redacted: bool = False,
    overwrite: bool = False,
    tmp_dir: str | Path | None = None,
) -> TransformResult:
    """Copy the rows ``keep_row`` keeps into a new archive at ``output_path``.

    ``transform`` is the CLI verb's name and ``params`` its parameters,
    recorded verbatim in the hop. ``summary`` adds transform-specific counts
    to the hop's summary, after the per-type kept/dropped counts this
    function records. Refuses an existing ``output_path`` unless
    ``overwrite``, and always refuses writing over the input itself.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    if output_path.exists():
        if output_path.resolve() == input_path.resolve():
            raise OutputExistsError(f"output is the input archive itself: {output_path}")
        if not overwrite:
            raise OutputExistsError(f"output exists: {output_path} (pass overwrite to replace it)")

    input_sha256 = file_sha256(input_path)
    scratch = Path(tempfile.mkdtemp(prefix="wip-transform-", dir=str(tmp_dir) if tmp_dir else None))
    try:
        with ArchiveReader(input_path) as reader:
            raw = reader.read_manifest_raw()
            writer = ArchiveWriter(output_path, tmp_dir=scratch)
            copied = _copy_rows(reader, writer, keep_row, keep_blob, raw)

        manifest = _output_manifest(raw, copied)
        chain = input_chain(raw)

        provisional = scratch / "provisional.zip"
        writer.assemble(provisional, {**manifest, "derived_from": chain} if chain else manifest,
                        compression=zipfile.ZIP_STORED)
        with ArchiveReader(provisional) as check_reader:
            model = ArchiveModel.load(check_reader, check_documents=True)
        findings = model.findings()

        hop: dict[str, Any] = {
            "transform": transform,
            "toolkit_version": toolkit_version,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "params": copy.deepcopy(params),
            "params_redacted": params_redacted,
            "input": {
                "sha256": input_sha256,
                "format_version": raw.get("format_version"),
                "derived": bool(chain),
            },
            "verify": {"verdict": verdict(findings), "findings": finding_counts(findings)},
            "summary": {**copied.summary(), **(summary or {})},
        }
        manifest["derived_from"] = [*chain, hop]

        partial = output_path.with_name(output_path.name + ".partial")
        writer.assemble(partial, manifest)
        partial.replace(output_path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return TransformResult(
        output=output_path,
        hop=hop,
        model=model,
        kept=copied.kept_counts(),
        dropped=copied.dropped_counts(),
        referents=copied.referent_rows,
        blobs_kept=copied.blobs_kept,
        blobs_dropped=copied.blobs_dropped,
    )


@dataclass
class _Copied:
    """Counts of one copy pass, per namespace and entity type."""

    kept: dict[tuple[str, str], int] = field(default_factory=dict)
    dropped: dict[tuple[str, str], int] = field(default_factory=dict)
    referent_rows: int = 0
    blobs_kept: int = 0
    blobs_dropped: int = 0
    namespaces: list[str] = field(default_factory=list)

    def bump(self, counts: dict[tuple[str, str], int], ns: str, entity_type: str) -> None:
        counts[(ns, entity_type)] = counts.get((ns, entity_type), 0) + 1

    def _per_type(self, counts: dict[tuple[str, str], int]) -> dict[str, int]:
        out: dict[str, int] = {}
        for (_ns, entity_type), n in counts.items():
            out[entity_type] = out.get(entity_type, 0) + n
        return out

    def kept_counts(self) -> dict[str, int]:
        return self._per_type(self.kept)

    def dropped_counts(self) -> dict[str, int]:
        return self._per_type(self.dropped)

    def ns_counts(self, ns: str) -> dict[str, int]:
        return {t: n for (n_ns, t), n in self.kept.items() if n_ns == ns}

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        kept, dropped = self.kept_counts(), self.dropped_counts()
        for entity_type in ENTITY_FILES:
            if entity_type in kept or entity_type in dropped:
                out[f"{entity_type}_kept"] = kept.get(entity_type, 0)
                out[f"{entity_type}_dropped"] = dropped.get(entity_type, 0)
        out["referents"] = self.referent_rows
        if self.blobs_kept or self.blobs_dropped:
            out["blobs_kept"] = self.blobs_kept
            out["blobs_dropped"] = self.blobs_dropped
        return out


def _copy_rows(
    reader: ArchiveReader,
    writer: ArchiveWriter,
    keep_row: KeepRow,
    keep_blob: KeepBlob | None,
    raw_manifest: dict[str, Any],
) -> _Copied:
    copied = _Copied()
    collector = ReferentCollector()
    carried: set[str] = set()
    kept_files: set[str] = set()

    namespaces = [
        n.get("prefix") for n in raw_manifest.get("namespaces") or [] if n.get("prefix")
    ] or reader.list_namespaces()
    copied.namespaces = namespaces

    # Pass 1: the selected rows, streamed, with what they carry and reference.
    for ns in namespaces:
        for entity_type in SELECTABLE_TYPES:
            id_key = ENTITY_ID_KEYS.get(entity_type)
            for row in reader.read_entities(entity_type, namespace=ns):
                if not keep_row(ns, entity_type, row):
                    copied.bump(copied.dropped, ns, entity_type)
                    continue
                writer.add_entity(entity_type, row, namespace=ns)
                copied.bump(copied.kept, ns, entity_type)
                collector.add(entity_type, row)
                if id_key and isinstance(row.get(id_key), str):
                    carried.add(row[id_key])
                    if entity_type == "files":
                        kept_files.add(row[id_key])

    referenced_outside = set(collector.external_ids()) - carried

    # Pass 2: Registry rows follow their entities; the rows of cut entities
    # that kept rows still reference become referents.
    referents: dict[str, dict[str, Any]] = {}
    for ns in namespaces:
        for row in reader.read_entities("registry_entries", namespace=ns):
            entry_id = row.get("entry_id")
            if entry_id in carried:
                writer.add_entity("registry_entries", row, namespace=ns)
                copied.bump(copied.kept, ns, "registry_entries")
                continue
            copied.bump(copied.dropped, ns, "registry_entries")
            if entry_id in referenced_outside:
                referents[entry_id] = row
    for row in reader.read_referents():
        entry_id = row.get("entry_id")
        if entry_id in referenced_outside and entry_id not in referents:
            referents[entry_id] = row
    copied.referent_rows = writer.write_referents_file(
        referents[entry_id] for entry_id in sorted(referents)
    )

    # Legacy toolkit archives keep synonyms in one root file, keyed entry_id.
    synonyms = [s for s in reader.read_synonyms() if s.get("entry_id") in carried]
    if synonyms:
        writer.write_synonyms_file(synonyms)

    for file_id in reader.list_blobs():
        if file_id in kept_files and (keep_blob is None or keep_blob(file_id)):
            with reader.open_blob(file_id) as src, writer.open_blob(file_id) as dst:
                shutil.copyfileobj(src, dst)
            copied.blobs_kept += 1
        else:
            copied.blobs_dropped += 1
    return copied


def _output_manifest(raw: dict[str, Any], copied: _Copied) -> dict[str, Any]:
    """The input's manifest with counts, the referent count and the file flag
    recomputed for the output. Every other key, known or not, is kept."""
    manifest = copy.deepcopy(raw)
    manifest.pop("derived_from", None)
    entries = []
    for entry in manifest.get("namespaces") or []:
        ns_counts = copied.ns_counts(entry.get("prefix", ""))
        if not any(ns_counts.values()):
            # Nothing of this namespace travels: listing it would make a
            # restore provision an empty namespace nobody selected.
            continue
        entry["counts"] = {t: ns_counts.get(t, 0) for t in ENTITY_FILES}
        entries.append(entry)
    if "namespaces" in manifest and len(entries) != len(manifest["namespaces"]):
        manifest["namespaces"] = entries
        # The single-namespace convenience mirrors the sole entry, if any.
        # Untouched when every namespace travels: the input's own fields stand.
        if len(entries) == 1:
            manifest["namespace"] = entries[0].get("prefix", "")
            manifest["namespace_config"] = entries[0].get("namespace_config")
        else:
            manifest["namespace"] = ""
            manifest["namespace_config"] = None
    kept = copied.kept_counts()
    manifest["counts"] = {t: kept.get(t, 0) for t in ENTITY_FILES}
    manifest["referents"] = copied.referent_rows
    if manifest.get("include_files") and copied.blobs_kept == 0:
        manifest["include_files"] = False
    return manifest
