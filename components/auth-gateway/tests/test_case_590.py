"""CASE-590 — auth-gateway refuses to start in prod with the default session secret.

The gateway signs session cookies with SESSION_SECRET; the fallback
'change-me-in-production' is public, so cookies signed with it are
forgeable (full identity spoofing via the verify headers). Every other
backend service already has a WIP_VARIANT=prod startup guard; this pins
the gateway's local equivalent.
"""

import os

import pytest

# Settings is an import-time singleton; whichever test module imports it
# first fixes its values process-wide. Mirror test_verify.py's env block so
# collection order doesn't decide what the singleton captured.
os.environ.setdefault("OIDC_ISSUER", "https://wip.local/dex")
os.environ.setdefault("OIDC_INTERNAL_ISSUER", "http://wip-dex:5556/dex")
os.environ.setdefault("OIDC_CLIENT_ID", "wip-gateway")
os.environ.setdefault("OIDC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("WIP_HOSTNAME", "wip.local")
os.environ.setdefault("CALLBACK_URL", "https://wip.local/auth/callback")

from auth_gateway.config import (  # noqa: E402
    DEFAULT_SESSION_SECRET,
    check_production_security,
)


def test_prod_with_default_secret_refuses_to_start(monkeypatch):
    monkeypatch.setenv("WIP_VARIANT", "prod")
    with pytest.raises(SystemExit) as exc_info:
        check_production_security(session_secret=DEFAULT_SESSION_SECRET)
    assert exc_info.value.code == 1


def test_prod_with_real_secret_starts(monkeypatch):
    monkeypatch.setenv("WIP_VARIANT", "prod")
    check_production_security(session_secret="a-random-generated-secret")


def test_dev_with_default_secret_starts(monkeypatch):
    """Same contract as wip_auth.security: only WIP_VARIANT=prod arms it."""
    monkeypatch.setenv("WIP_VARIANT", "dev")
    check_production_security(session_secret=DEFAULT_SESSION_SECRET)


def test_unset_variant_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("WIP_VARIANT", raising=False)
    check_production_security(session_secret=DEFAULT_SESSION_SECRET)


def test_reads_settings_when_no_secret_passed(monkeypatch):
    """The no-arg form (the main.py call) reads the live settings object."""
    monkeypatch.setenv("WIP_VARIANT", "prod")
    from auth_gateway import config

    monkeypatch.setattr(config.settings, "session_secret", DEFAULT_SESSION_SECRET)
    with pytest.raises(SystemExit):
        check_production_security()
    monkeypatch.setattr(config.settings, "session_secret", "rotated-secret")
    check_production_security()
