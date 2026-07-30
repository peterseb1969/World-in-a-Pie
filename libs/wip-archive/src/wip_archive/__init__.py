"""WIP backup archive format contract and fresh-restore id remapping.

The platform-core boundary: the document-store engine imports these modules
in-process; the WIP-Toolkit operator CLI consumes them for export/inspect.
See docs/design/wip-archive-split.md for the boundary rationale.
"""

from .archive import ArchiveReader, ArchiveWriter
from .exceptions import (
    ArchiveError,
    ManifestParseError,
    MissingManifestError,
    NotAnArchiveError,
)
from .model import (
    SCHEMA_VERSION,
    ArchiveModel,
    EdgeTypeReport,
    Finding,
    Island,
    TemplateReport,
)
from .models import Manifest
from .remap import IDRemapper

__all__ = [
    "SCHEMA_VERSION",
    "ArchiveError",
    "ArchiveModel",
    "ArchiveReader",
    "ArchiveWriter",
    "EdgeTypeReport",
    "Finding",
    "IDRemapper",
    "Island",
    "Manifest",
    "ManifestParseError",
    "MissingManifestError",
    "NotAnArchiveError",
    "TemplateReport",
]
