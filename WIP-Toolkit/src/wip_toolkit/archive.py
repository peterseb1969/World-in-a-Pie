"""ZIP archive I/O with JSONL streaming for WIP export/import."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator, TextIO

from .models import Manifest


# JSONL file names within the archive
MANIFEST_FILE = "manifest.json"
TERMINOLOGIES_FILE = "terminologies.jsonl"
TERMS_FILE = "terms.jsonl"
TEMPLATES_FILE = "templates.jsonl"
DOCUMENTS_FILE = "documents.jsonl"
FILES_FILE = "files.jsonl"
RELATIONSHIPS_FILE = "relationships.jsonl"
SYNONYMS_FILE = "synonyms.jsonl"
BLOBS_DIR = "blobs/"

ENTITY_FILES = {
    "terminologies": TERMINOLOGIES_FILE,
    "terms": TERMS_FILE,
    "relationships": RELATIONSHIPS_FILE,
    "templates": TEMPLATES_FILE,
    "documents": DOCUMENTS_FILE,
    "files": FILES_FILE,
}


class ArchiveWriter:
    """Writes entities to a ZIP archive using temp files for O(1) memory.

    Each entity type is appended as a JSONL line to a temp file on disk.
    When write() is called, the temp files are streamed into the ZIP archive.
    """

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self._tmp_dir = tempfile.mkdtemp(prefix="wip-export-")
        self._handles: dict[str, TextIO] = {}
        self._counts: dict[str, int] = {name: 0 for name in ENTITY_FILES}
        self._blob_data: dict[str, bytes] = {}

    def _get_handle(self, entity_type: str) -> TextIO:
        """Get or create the temp file handle for an entity type."""
        if entity_type not in self._handles:
            filename = ENTITY_FILES[entity_type]
            path = Path(self._tmp_dir) / filename
            self._handles[entity_type] = open(path, "w", encoding="utf-8")
        return self._handles[entity_type]

    def add_entity(self, entity_type: str, entity: dict[str, Any]) -> None:
        """Append an entity as a JSONL line to the temp file."""
        fh = self._get_handle(entity_type)
        fh.write(json.dumps(entity, default=str))
        fh.write("\n")
        self._counts[entity_type] = self._counts.get(entity_type, 0) + 1

    def add_blob(self, file_id: str, data: bytes) -> None:
        """Add binary file content to the archive."""
        self._blob_data[file_id] = data

    def entity_count(self, entity_type: str) -> int:
        """Return the number of entities added for a given type."""
        return self._counts.get(entity_type, 0)

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

            # Write JSONL files from temp dir
            for entity_type, filename in ENTITY_FILES.items():
                tmp_path = Path(self._tmp_dir) / filename
                if tmp_path.exists() and tmp_path.stat().st_size > 0:
                    zf.write(tmp_path, filename)

            # Write synonyms.jsonl if present
            synonyms_path = Path(self._tmp_dir) / SYNONYMS_FILE
            if synonyms_path.exists() and synonyms_path.stat().st_size > 0:
                zf.write(synonyms_path, SYNONYMS_FILE)

            # Write blobs
            for file_id, data in self._blob_data.items():
                zf.writestr(f"{BLOBS_DIR}{file_id}", data)

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
        try:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except Exception:
            pass

    def __del__(self) -> None:
        # Safety cleanup if write() was never called
        for fh in self._handles.values():
            try:
                fh.close()
            except Exception:
                pass
        self._cleanup()


class ArchiveReader:
    """Reads entities from a ZIP archive with JSONL format."""

    def __init__(self, archive_path: str | Path) -> None:
        self.archive_path = Path(archive_path)
        if not self.archive_path.exists():
            raise FileNotFoundError(f"Archive not found: {self.archive_path}")
        self._zf = zipfile.ZipFile(self.archive_path, "r")

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> ArchiveReader:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def read_manifest(self) -> Manifest:
        """Read and parse the manifest."""
        data = json.loads(self._zf.read(MANIFEST_FILE))
        return Manifest(**data)

    def read_entities(self, entity_type: str) -> Iterator[dict[str, Any]]:
        """Iterate over entities of a given type."""
        filename = ENTITY_FILES[entity_type]
        try:
            content = self._zf.read(filename).decode("utf-8")
        except KeyError:
            return

        for line in content.splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)

    def read_synonyms(self) -> Iterator[dict[str, Any]]:
        """Iterate over synonyms from synonyms.jsonl (if present)."""
        try:
            content = self._zf.read(SYNONYMS_FILE).decode("utf-8")
        except KeyError:
            return

        for line in content.splitlines():
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

    def entity_count(self, entity_type: str) -> int:
        """Count entities of a given type without loading all into memory."""
        filename = ENTITY_FILES[entity_type]
        try:
            content = self._zf.read(filename).decode("utf-8")
        except KeyError:
            return 0
        return sum(1 for line in content.splitlines() if line.strip())

    def namelist(self) -> list[str]:
        """List all files in the archive."""
        return self._zf.namelist()

    def total_size(self) -> int:
        """Total uncompressed size of the archive."""
        return sum(info.file_size for info in self._zf.infolist())

    def compressed_size(self) -> int:
        """Total compressed size of the archive."""
        return sum(info.compress_size for info in self._zf.infolist())
