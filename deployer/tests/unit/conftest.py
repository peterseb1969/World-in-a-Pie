"""Shared unit-test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_live_cluster_secrets() -> Iterator[None]:
    """Every k8s apply reads the live `wip-secrets` to check the secret
    store against it. Unit tests must never reach a real cluster, so the
    read reports "nothing to read" unless a test patches it itself."""
    with patch("wip_deploy.cli.read_live_secrets", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _no_live_registry_reservations() -> Iterator[None]:
    """Every apply asks the running Registry for its runtime namespace
    reservations. Unit tests must never reach a real install, so the apply's
    check finds nothing to compare. (Tests of the check itself import the
    function directly, which this patch does not replace.)"""
    from wip_deploy.runtime_reservations import RuntimeReservationCheck

    with patch(
        "wip_deploy.runtime_reservations.check_runtime_reservations",
        return_value=RuntimeReservationCheck(),
    ):
        yield


@pytest.fixture(autouse=True)
def _no_live_registry_attachments() -> Iterator[None]:
    """Every apply asks the running Registry for its app attachments, so an
    app cannot take a route one owns. Unit tests must never reach a real
    install. (Tests of the check itself import the function directly.)"""
    from wip_deploy.attachment_check import AttachmentCheck

    with patch(
        "wip_deploy.attachment_check.check_attachment_collisions",
        return_value=AttachmentCheck(),
    ):
        yield
