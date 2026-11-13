"""Shared unit-test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from ._kube import UNIT_CLUSTER_UID


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
def _no_live_trust_anchor() -> Iterator[None]:
    """A request carrying the install's keys first reads the install's CA
    (from its Caddy container, or its k8s TLS Secret). Unit tests must never
    reach a container or a cluster for it, so the anchor is the system store.
    (Tests of tls_trust import trust_anchor directly, which this patch does
    not replace.)"""
    with patch("wip_deploy.tls_trust.trust_anchor", return_value=True):
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


@pytest.fixture(autouse=True)
def _no_live_cluster(request: pytest.FixtureRequest) -> Iterator[None]:
    """No unit test may reach a real cluster through wip_deploy.kube, not even
    to read where the shell's kubectl points. The cluster check answers with
    UNIT_CLUSTER_UID, any other kubectl call through kube refuses, and library
    calls (apply_k8s, nuke, status) run as after a CLI selection of the
    shell's context. A test that puts its own fake kubectl on PATH opts out
    with the `fake_kubectl_binary` marker."""
    from wip_deploy import kube

    kube.reset_for_tests()
    if request.node.get_closest_marker("fake_kubectl_binary"):
        yield
        kube.reset_for_tests()
        return

    def no_cluster(*_args: object, **_kwargs: object) -> None:
        raise kube.KubeError("unit tests reach no cluster")

    with (
        patch("wip_deploy.kube._run", side_effect=no_cluster),
        patch("wip_deploy.kube.cluster_uid", return_value=UNIT_CLUSTER_UID),
        patch("wip_deploy.kube.current_context", return_value="unit-test"),
    ):
        kube.select_shell("unit-test")
        yield
    kube.reset_for_tests()
