"""Backup API findings from the restore-engine review.

Provenance: CASE-1194 (5-R3, 5-R10, the PENDING delete).

- Listing jobs during an authorization outage fails instead of answering an
  empty list that reads as "nothing you may see".
- A fresh restore given only a namespace_map is authorized on the map's
  targets, not on the URL namespace it never writes.
- A job that has not started yet cannot be deleted from under its runner.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from document_store.models.backup_job import BackupJobStatus
from tests.test_case_543 import _job
from tests.test_case_1098_restore_target import _archive, _post_restore

API = "document_store.api.backup.check_namespace_permission"


@pytest.mark.asyncio
async def test_listing_jobs_during_an_authorization_outage_is_a_503(
    client: AsyncClient, auth_headers: dict
):
    await _job(namespaces=["wip"])

    async def outage(identity, namespace, required):
        raise HTTPException(503, "Authorization service unavailable")

    with patch(API, side_effect=outage):
        resp = await client.get("/api/document-store/backup/jobs", headers=auth_headers)
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_listing_jobs_still_hides_refused_ones(client: AsyncClient, auth_headers: dict):
    await _job(namespaces=["wip"])

    async def refused(identity, namespace, required):
        raise HTTPException(404, "Namespace not found")

    with patch(API, side_effect=refused):
        resp = await client.get("/api/document-store/backup/jobs", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_a_map_only_fresh_restore_is_not_asked_admin_on_the_url_namespace(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    asked: list[tuple[str, str]] = []

    async def record(identity, namespace, required):
        asked.append((namespace, required))

    with patch(API, side_effect=record):
        await _post_restore(
            client, auth_headers, _archive(tmp_path, ["wip"]),
            {"mode": "fresh", "namespace_map": '{"wip": "wip-copy"}'},
        )
    assert ("wip-copy", "admin") in asked
    assert ("wip", "admin") not in asked


@pytest.mark.asyncio
async def test_a_pending_job_cannot_be_deleted(client: AsyncClient, auth_headers: dict):
    job = await _job(status=BackupJobStatus.PENDING)
    resp = await client.delete(f"/api/document-store/backup/jobs/{job.job_id}", headers=auth_headers)
    assert resp.status_code == 409, resp.text
    assert "pending" in resp.json()["detail"]
