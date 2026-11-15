"""document-store's reporting-sync key falls back to API_KEY, like every
other outbound chain (CASE-1307, review of ddbcc110 R1).

wip-auth copies API_KEY into WIP_AUTH_LEGACY_API_KEY only when the latter is
absent. Set but empty, it stays empty, and the reporting-sync chain
(REGISTRY_API_KEY, WIP_AUTH_LEGACY_API_KEY) held no key: document-store
refused to start although API_KEY satisfied every other chain it checks.
"""

import pytest

from document_store.main import OUTBOUND_KEYS
from document_store.services.reporting_client import REPORTING_SYNC_KEY
from wip_auth.service_keys import missing_service_keys, service_api_key


@pytest.fixture
def only_api_key(monkeypatch):
    for name in ("REGISTRY_API_KEY", "TEMPLATE_STORE_API_KEY", "DEF_STORE_API_KEY", "MASTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "")
    monkeypatch.setenv("API_KEY", "the-install-key")


def test_api_key_alone_is_the_reporting_sync_key(only_api_key):
    assert service_api_key(*REPORTING_SYNC_KEY) == "the-install-key"


def test_startup_finds_every_chain_with_api_key_alone(only_api_key):
    assert REPORTING_SYNC_KEY in [tuple(chain) for chain in OUTBOUND_KEYS]
    assert missing_service_keys(OUTBOUND_KEYS) == []
