"""ZIP archive I/O with JSONL streaming for WIP export/import."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

from .models import Manifest


# JSONL file names within the archive
MANIFEST_FILE = "manifest.json"
TERMINOLOGIES_FILE = "terminologies.jsonl"
TERMS_FILE = "terms.jsonl"
TEMPLATES_FILE = "templates.jsonl"
DOCUMENTS_FILE = "documents.jsonl"
FILES_FILE = "files.jsonl"
BLOBS_DIR = "blobs/"

ENTITY_FILES = {
    "terminologies": TERMINOLOGIES_FILE,
    "terms": TERMS_FILE,
    "templates": TEMPLATES_FILE,
    "documents": DOCUMENTS_FILE,
    "files": FILES_FILE,
}


class ArchiveWriter:
    """Writes entities to a ZIP archive with JSONL format."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self._buffers: dict[str, list[str]] = {
            name: [] for name in ENTITY_FILES.values()
        }
        self._blob_data: dict[str, bytes] = {}

    def add_entity(self, entity_type: str, entity: dict[str, Any]) -> None:
        """Add an entity to the archive buffer."""
        filename = ENTITY_FILES[entity_type]
        self._buffers[filename].append(json.dumps(entity, default=str))

    def add_blob(self, file_id: str, data: bytes) -> None:
        """Add binary file content to the archive."""
        self._blob_data[file_id] = data

    def write(self, manifest: Manifest) -> Path:
        """Write the archive to disk."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(self.output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Write manifest
            zf.writestr(
                MANIFEST_FILE,
                manifest.model_dump_json(indent=2),
            )

            # Write JSONL files
            for filename, lines in self._buffers.items():
                if lines:
                    zf.writestr(filename, "\n".join(lines) + "\n")

            # Write blobs
            for file_id, data in self._blob_data.items():
                zf.writestr(f"{BLOBS_DIR}{file_id}", data)

        return self.output_path


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
