"""Thin client for the server restore engine.

The toolkit never writes restored data itself: the document-store's restore
endpoint is the one validating door, so this module uploads the archive,
starts the job, and (optionally) follows it to the end.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .client import WIPClient

TERMINAL_STATUSES = ("complete", "failed")


def build_form(
    mode: str,
    *,
    target_namespace: str | None = None,
    namespace_map: dict[str, str] | None = None,
    on_clash: str = "skip",
    older_versions: str = "retire",
    add_missing: bool = False,
    extend_terminologies: bool = False,
    skip_documents: bool = False,
    skip_files: bool = False,
    dry_run: bool = False,
    allow_failed_verify: bool = False,
) -> dict[str, str]:
    """The multipart fields for one restore request.

    The merge policies are sent only for a merge: the endpoint rejects a
    non-default policy on the other modes rather than ignoring it.
    """
    import json

    data: dict[str, str] = {
        "mode": mode,
        "skip_documents": str(skip_documents).lower(),
        "skip_files": str(skip_files).lower(),
        "dry_run": str(dry_run).lower(),
        "allow_failed_verify": str(allow_failed_verify).lower(),
    }
    if target_namespace:
        data["target_namespace"] = target_namespace
    if namespace_map:
        data["namespace_map"] = json.dumps(namespace_map)
    if mode == "merge":
        data["on_clash"] = on_clash
        data["older_versions"] = older_versions
        data["add_missing"] = str(add_missing).lower()
        data["extend_terminologies"] = str(extend_terminologies).lower()
    return data


def start_restore(
    client: WIPClient, archive_path: Path, namespace: str, form: dict[str, str]
) -> dict[str, Any]:
    """Upload the archive and queue the restore. Returns the job snapshot."""
    with archive_path.open("rb") as fh:
        return client.post_form(
            "document-store",
            f"/backup/namespaces/{namespace}/restore",
            data=form,
            files={"archive": (archive_path.name, fh, "application/zip")},
        )


def wait_for_job(
    client: WIPClient,
    job_id: str,
    *,
    poll_interval: float = 1.0,
    timeout: float = 3600.0,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Poll the job until it reaches a terminal status or the timeout passes.

    On timeout the last snapshot is returned as it stands; the job keeps
    running on the server.
    """
    deadline = clock() + timeout
    while True:
        snapshot = client.get("document-store", f"/backup/jobs/{job_id}")
        if on_progress is not None:
            on_progress(snapshot)
        if snapshot.get("status") in TERMINAL_STATUSES or clock() >= deadline:
            return snapshot
        sleep(poll_interval)
