"""The `strip-blobs` plan: an archive without file content.

The blobs are what make an archive large; ``strip-blobs`` removes them. It
removes the file rows with them, because a file row without its bytes does
not restore as anything useful: a fresh restore re-mints the file id and
copies the blob under it, and with no blob it still inserts the row, a file
that lists but has no content.

So files are cut like any other entity (``wip_archive.transform``): every
file a kept document references becomes a referent. Files are
content-addressed (their Registry key is the checksum of their bytes, within
their namespace), so on an install that holds the same bytes the restore
points the documents at that file, and elsewhere it reports the reference
as unresolved. A file nothing references leaves no trace.

Everything else travels unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StripBlobsPlan:
    """Keeps every row except file rows; the core drops their blobs."""

    def keep_row(self, _namespace: str, entity_type: str, _row: dict[str, Any]) -> bool:
        return entity_type != "files"
