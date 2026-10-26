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
