"""Thin client for the server backup engine.

The toolkit no longer collects a namespace itself: the document-store's
backup engine reads the databases directly and writes a complete,
restorable archive. This module starts that job, follows it, and downloads
the archive it produced. Everything selective happens afterwards, offline,
in the transform verbs (`filter`, `prune`, `latest-only`, `strip-blobs`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import WIPClient


def build_request(
    *,
    namespaces: list[str] | None = None,
    all_namespaces: bool = False,
    include_files: bool = False,
    skip_documents: bool = False,
) -> dict[str, Any]:
    """The JSON body of one backup request: the engine's live options only.
    The endpoint refuses the retired toolkit-export options with a 400."""
    body: dict[str, Any] = {
        "all_namespaces": all_namespaces,
        "include_files": include_files,
        "skip_documents": skip_documents,
    }
    if namespaces:
        body["namespaces"] = namespaces
    return body


def start_backup(client: WIPClient, namespace: str, body: dict[str, Any]) -> dict[str, Any]:
    """Queue the backup anchored at ``namespace``. Returns the job snapshot."""
    return client.post("document-store", f"/backup/namespaces/{namespace}/backup", json=body)


def download_archive(client: WIPClient, job_id: str, output: Path) -> int:
    """Stream a complete job's archive to ``output``; returns its size.

    Written to a ``.partial`` sibling and renamed into place, so an
    interrupted download never leaves a truncated archive under the name
    asked for.
    """
    partial = output.with_name(output.name + ".partial")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with partial.open("wb") as fh:
            client.stream_to_file("document-store", f"/backup/jobs/{job_id}/download", fh)
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    return output.stat().st_size
