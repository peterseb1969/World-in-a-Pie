"""Startup security checks for WIP services.

Called during service startup to refuse running with known-insecure
defaults in production mode. Failing fast is better than running
insecure.

Usage in a service's lifespan:
    from wip_auth.security import check_production_security
    check_production_security()  # Exits if prod + insecure defaults
"""

import logging
import os
import sys

logger = logging.getLogger("wip_auth.security")

DEFAULT_API_KEY = "dev_master_key_for_testing"


def check_production_security() -> None:
    """Check security configuration at startup.

    In production mode (WIP_VARIANT=prod), refuses to start if the
    well-known default API key is in use. This key is documented in
    CLAUDE.md, README, and seed scripts — anyone can use it.
    """
    variant = os.getenv("WIP_VARIANT", "dev")
    if variant != "prod":
        return

    # Check for default API key (check all possible env var names)
    api_key = (
        os.getenv("WIP_AUTH_LEGACY_API_KEY")
        or os.getenv("MASTER_API_KEY")
        or os.getenv("API_KEY", "")
    )

    if api_key == DEFAULT_API_KEY:
        logger.critical(
            "SECURITY: Default API key detected in production mode! "
            "The key 'dev_master_key_for_testing' is publicly documented. "
            "Set a secure API_KEY in .env or re-run: "
            "./scripts/setup.sh --preset <preset> --hostname <host> --prod -y -- "
            "Refusing to start."
        )
        sys.exit(1)

    if not api_key:
        logger.warning(
            "SECURITY: No API key configured in production mode. "
            "Auth may rely solely on JWT."
        )
