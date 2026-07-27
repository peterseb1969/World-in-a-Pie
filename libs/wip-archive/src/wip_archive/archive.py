"""ZIP archive I/O with JSONL streaming for WIP export/import."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from .exceptions import (
    ManifestParseError,
    MissingManifestError,
    NotAnArchiveError,
)
from .models import Manifest

# JSONL file names within the archive
MANIFEST_FILE = "manifest.json"
TERMINOLOGIES_FILE = "terminologies.jsonl"
TERMS_FILE = "terms.jsonl"
TEMPLATES_FILE = "templates.jsonl"
DOCUMENTS_FILE = "documents.jsonl"
FILES_FILE = "files.jsonl"
TERM_RELATIONS_FILE = "term_relations.jsonl"
SYNONYMS_FILE = "synonyms.jsonl"
REGISTRY_ENTRIES_FILE = "registry_entries.jsonl"
BLOBS_DIR = "blobs/"

ENTITY_FILES = {
    "terminologies": TERMINOLOGIES_FILE,
    "terms": TERMS_FILE,
    "term_relations": TERM_RELATIONS_FILE,
    "templates": TEMPLATES_FILE,
    "documents": DOCUMENTS_FILE,
    "files": FILES_FILE,
    "registry_entries": REGISTRY_ENTRIES_FILE,
}

# v3 (CASE-542): entity JSONL lives under a per-namespace subtree. Blobs stay
# flat at blobs/<file_id> (file_ids are globally-unique UUID7, so no collision
# across namespaces — keeps blob I/O namespace-agnostic, zero regression).
NAMESPACES_DIR = "namespaces"


def _entity_path(namespace: str, entity_type: str) -> str:
    """In-archive path for one namespace's entity JSONL (v3 layout)."""
    return f"{NAMESPACES_DIR}/{namespace}/{ENTITY_FILES[entity_type]}"


class ArchiveWriter:
    """Writes entities to a ZIP archive using temp files for O(1) memory.

    Each entity type is appended as a JSONL line to a temp file on disk.
    File blobs are streamed to a per-file tempfile under ``blobs/`` so peak
    memory stays O(one chunk) regardless of total blob size (CASE-28).
    When write() is called, the temp files are streamed into the ZIP archive.

    The temp directory location can be overridden via ``tmp_dir`` so a
    server caller (e.g. document-store's /backup endpoint) can co-locate
    scratch storage with the configured ``WIP_BACKUP_DIR`` instead of the
    system default ``/tmp`` (CASE-29).
    """

    def __init__(
        self,
        output_path: str | Path,
        tmp_dir: str | Path | None = None,
        default_namespace: str = "",
    ) -> None:
        self.output_path = Path(output_path)
        self._tmp_dir = tempfile.mkdtemp(
            prefix="wip-export-",
            dir=str(tmp_dir) if tmp_dir else None,
        )
        # v3: handles + counts are keyed by (namespace, entity_type). A caller
        # that omits the namespace falls back to default_namespace — the
        # single-namespace convenience that keeps legacy callers working while
        # still producing a v3 archive (a 1-namespace one).
        self._default_namespace = default_namespace
        self._handles: dict[tuple[str, str], TextIO] = {}
        self._counts: dict[tuple[str, str], int] = {}
        self._namespaces: list[str] = []  # insertion order
        self._blobs_dir = Path(self._tmp_dir) / "blobs"
        self._blobs_dir.mkdir()

    def _resolve_ns(self, namespace: str) -> str:
        ns = namespace or self._default_namespace
        if not ns:
            raise ValueError(
                "ArchiveWriter (v3) requires a namespace: pass namespace= or "
                "construct with default_namespace="
            )
        if ns not in self._namespaces:
            self._namespaces.append(ns)
        return ns

    def _get_handle(self, namespace: str, entity_type: str) -> TextIO:
        """Get or create the temp file handle for (namespace, entity type)."""
        key = (namespace, entity_type)
        if key not in self._handles:
            d = Path(self._tmp_dir) / NAMESPACES_DIR / namespace
            d.mkdir(parents=True, exist_ok=True)
            # A long-lived handle stored per (ns, entity) and closed in
            # write()/_cleanup; a `with` block would close it too early.
            self._handles[key] = open(  # noqa: SIM115
                d / ENTITY_FILES[entity_type], "w", encoding="utf-8"
            )
        return self._handles[key]

    def add_entity(
        self, entity_type: str, entity: dict[str, Any], *, namespace: str = ""
    ) -> None:
        """Append an entity as a JSONL line under its namespace's subtree."""
        ns = self._resolve_ns(namespace)
        fh = self._get_handle(ns, entity_type)
        fh.write(json.dumps(entity, default=str))
        fh.write("\n")
        self._counts[(ns, entity_type)] = self._counts.get((ns, entity_type), 0) + 1

    def flush(self) -> None:
        """Flush every open entity-file handle.

        Callers that scan the staged temp files while the writer is still
        open (an exporter collecting entity ids for its registry pass) must
        see complete lines, not whatever happens to have left the buffers.
        """
        for fh in self._handles.values():
            fh.flush()

    @contextmanager
    def open_blob(self, file_id: str) -> Iterator[BinaryIO]:
        """Open a binary file handle for streaming a blob to disk.

        Preferred entry point for callers that already have a streaming
        source (e.g. an HTTP download). Composes naturally with
        :meth:`WIPClient.stream_to_file` so no full blob is ever held in
        Python memory.
        """
        path = self._blobs_dir / file_id
        with open(path, "wb") as fh:
            yield fh

    def add_blob(self, file_id: str, data: bytes) -> None:
        """Add binary file content to the archive (in-memory entry point).

        Thin wrapper that writes ``data`` to the same per-file tempfile
        as :meth:`open_blob`. Preserves the original public API for
        existing callers and tests; new code should prefer ``open_blob``.
        """
        (self._blobs_dir / file_id).write_bytes(data)

    def entity_count(self, entity_type: str, *, namespace: str = "") -> int:
        """Return the number of entities added for (namespace, entity type)."""
        ns = namespace or self._default_namespace
        return self._counts.get((ns, entity_type), 0)

    def namespaces(self) -> list[str]:
        """Namespaces written to this archive, in first-seen order."""
        return list(self._namespaces)

    def write(self, manifest: Manifest) -> Path:
        """Flush temp files and assemble the ZIP archive."""
        # Close all open handles
        for fh in self._handles.values():
            fh.close()
        self._handles.clear()

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(self.output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Write manifest
            zf.writestr(
                MANIFEST_FILE,
                manifest.model_dump_json(indent=2),
            )

            # Write per-namespace JSONL files (v3 layout: namespaces/<ns>/…)
            for ns in self._namespaces:
                for entity_type in ENTITY_FILES:
                    tmp_path = (
                        Path(self._tmp_dir) / NAMESPACES_DIR / ns / ENTITY_FILES[entity_type]
                    )
                    if tmp_path.exists() and tmp_path.stat().st_size > 0:
                        zf.write(tmp_path, _entity_path(ns, entity_type))

            # Write synonyms.jsonl if present (legacy single-file; toolkit path)
            synonyms_path = Path(self._tmp_dir) / SYNONYMS_FILE
            if synonyms_path.exists() and synonyms_path.stat().st_size > 0:
                zf.write(synonyms_path, SYNONYMS_FILE)

            # Write blobs (streamed from per-file tempfiles; flat, namespace-agnostic)
            for blob_file in sorted(self._blobs_dir.iterdir()):
                zf.write(blob_file, f"{BLOBS_DIR}{blob_file.name}")

        self._cleanup()
        return self.output_path

    def write_synonyms_file(self, synonyms: list[dict[str, Any]]) -> None:
        """Write synonyms.jsonl to the temp directory."""
        path = Path(self._tmp_dir) / SYNONYMS_FILE
        with open(path, "w", encoding="utf-8") as f:
            for syn in synonyms:
                f.write(json.dumps(syn, default=str))
                f.write("\n")

    def _cleanup(self) -> None:
        """Remove the temp directory."""
        with suppress(Exception):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def __del__(self) -> None:
        # Safety cleanup if write() was never called
        for fh in self._handles.values():
            with suppress(Exception):
                fh.close()
        self._cleanup()


class ArchiveReader:
    """Reads entities from a ZIP archive with JSONL format."""

    def __init__(self, archive_path: str | Path) -> None:
        self.archive_path = Path(archive_path)
        if not self.archive_path.exists():
            raise FileNotFoundError(f"Archive not found: {self.archive_path}")
        try:
            self._zf = zipfile.ZipFile(self.archive_path, "r")
        except zipfile.BadZipFile as exc:
            raise NotAnArchiveError(
                f"not a valid archive (unreadable zip): {self.archive_path}"
            ) from exc

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> ArchiveReader:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def read_manifest_raw(self) -> dict:
        """The manifest as the raw JSON object, keys preserved.

        The Manifest model ignores unknown keys, so forward-declared fields
        written by newer producers (e.g. a ``derived_from`` marker stamped by
        an archive transform) are invisible through :meth:`read_manifest`.
        Consumers that must see such keys read the raw dict instead.

        Raises MissingManifestError when manifest.json is absent and
        ManifestParseError when it is not valid JSON, so a malformed archive
        fails with a typed, actionable error rather than a raw KeyError /
        JSONDecodeError.
        """
        try:
            raw = self._zf.read(MANIFEST_FILE)
        except KeyError as exc:
            raise MissingManifestError("archive has no manifest.json") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManifestParseError(
                f"archive manifest.json is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ManifestParseError(
                "archive manifest.json must be a JSON object"
            )
        return data

    def read_manifest(self) -> Manifest:
        """Read and parse the manifest (typed; unknown keys are ignored)."""
        return Manifest(**self.read_manifest_raw())

    def list_namespaces(self) -> list[str]:
        """The namespaces present in this v3 archive, sorted.

        Derived from the ``namespaces/<ns>/…`` entries in the ZIP, so it works
        even without parsing the manifest.
        """
        found: set[str] = set()
        prefix = f"{NAMESPACES_DIR}/"
        for name in self._zf.namelist():
            if name.startswith(prefix):
                parts = name.split("/")
                if len(parts) >= 3 and parts[1]:  # namespaces/<ns>/<file>
                    found.add(parts[1])
        return sorted(found)

    def _resolve_ns(self, namespace: str) -> str | None:
        """Resolve an optional namespace to a concrete one.

        Explicit namespace passes through. Omitted: a single-namespace archive
        resolves to its sole namespace (the legacy-caller convenience); an
        empty archive resolves to None (callers yield nothing); a multi-
        namespace archive raises — the caller must say which.
        """
        if namespace:
            return namespace
        nss = self.list_namespaces()
        if len(nss) == 1:
            return nss[0]
        if not nss:
            return None
        raise ValueError(
            f"archive carries {len(nss)} namespaces {nss}; pass namespace= to pick one"
        )

    def read_entities(
        self, entity_type: str, *, namespace: str = ""
    ) -> Iterator[dict[str, Any]]:
        """Iterate entities of a given type within a namespace.

        Streams line by line out of the ZIP rather than decoding the member
        whole. The writer side has always been O(1) in memory (it spools to
        temp files); the reader used to hold an entire entity file as one
        Python string, which for a large namespace's documents is hundreds of
        megabytes for something consumed one row at a time.
        """
        ns = self._resolve_ns(namespace)
        if ns is None:
            return
        try:
            handle = self._zf.open(_entity_path(ns, entity_type))
        except KeyError:
            return

        with handle, io.TextIOWrapper(handle, encoding="utf-8") as text:
            for line in text:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def read_synonyms(self) -> Iterator[dict[str, Any]]:
        """Iterate over synonyms from synonyms.jsonl (if present)."""
        try:
            handle = self._zf.open(SYNONYMS_FILE)
        except KeyError:
            return

        with handle, io.TextIOWrapper(handle, encoding="utf-8") as text:
            for line in text:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def has_synonyms(self) -> bool:
        """Check if the archive contains synonyms.jsonl."""
        return SYNONYMS_FILE in self._zf.namelist()

    def read_blob(self, file_id: str) -> bytes | None:
        """Read binary file content from the archive."""
        try:
            return self._zf.read(f"{BLOBS_DIR}{file_id}")
        except KeyError:
            return None

    def list_blobs(self) -> list[str]:
        """List all blob file IDs in the archive."""
        prefix = BLOBS_DIR
        return [
            name[len(prefix):]
            for name in self._zf.namelist()
            if name.startswith(prefix) and len(name) > len(prefix)
        ]

    def entity_count(self, entity_type: str, *, namespace: str = "") -> int:
        """Count entities of a type within a namespace, without loading them.

        Streamed for the same reason as read_entities: a dry run counts every
        entity type, and doing that by materialising each file would make the
        cheap preview as memory-hungry as the restore it is previewing.
        """
        ns = self._resolve_ns(namespace)
        if ns is None:
            return 0
        try:
            handle = self._zf.open(_entity_path(ns, entity_type))
        except KeyError:
            return 0
        with handle, io.TextIOWrapper(handle, encoding="utf-8") as text:
            return sum(1 for line in text if line.strip())

    def namelist(self) -> list[str]:
        """List all files in the archive."""
        return self._zf.namelist()

    def total_size(self) -> int:
        """Total uncompressed size of the archive."""
        return sum(info.file_size for info in self._zf.infolist())

    def compressed_size(self) -> int:
        """Total compressed size of the archive."""
        return sum(info.compress_size for info in self._zf.infolist())
