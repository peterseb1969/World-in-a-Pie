"""Test environment for the auth-gateway suite.

``auth_gateway.config.settings`` is an import-time singleton: whichever
test module imports the app first fixes its values for the whole process.
Individual modules mirror this block for the same reason, but that only
works while the module that sets the env happens to be collected first —
adding a module that sorts earlier and imports the app silently changed
what the singleton captured. conftest.py runs before any test module is
collected, so the environment is set once, before the first import,
whatever the collection order.
"""

import os

os.environ.setdefault("OIDC_ISSUER", "https://wip.local/dex")
os.environ.setdefault("OIDC_INTERNAL_ISSUER", "http://wip-dex:5556/dex")
os.environ.setdefault("OIDC_CLIENT_ID", "wip-gateway")
os.environ.setdefault("OIDC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("WIP_HOSTNAME", "wip.local")
os.environ.setdefault("CALLBACK_URL", "https://wip.local/auth/callback")
