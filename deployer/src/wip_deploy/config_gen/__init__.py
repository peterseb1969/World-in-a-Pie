"""Shared config generators.

Pure functions that take (Deployment, components, apps) and produce
structured config objects. Renderers consume these objects; they never
parse generator output.

There is no strict layering: `spec_context` imports `dex` (inside a
function), `dex` imports `env` and `routing`, and `env` imports
`spec_context` back; `routing` uses `env`'s port resolution. Read the
imports, not a map. What holds throughout: generators read the spec and the
manifests and return values, and only renderers write files.
"""

from wip_deploy.config_gen.caddy import CaddyConfig, generate_caddy_config
from wip_deploy.config_gen.dex import (
    DexClientEntry,
    DexConfig,
    DexUserEntry,
    generate_dex_config,
)
from wip_deploy.config_gen.env import (
    EnvMap,
    EnvValue,
    Literal,
    ResolvedEnv,
    SecretRef,
    resolve_all_env,
    resolve_component_env,
    resolve_env_source,
)
from wip_deploy.config_gen.ingress import (
    IngressConfig,
    generate_ingress_config,
)
from wip_deploy.config_gen.routing import (
    ResolvedRoute,
    resolve_root_redirect,
    resolve_routes,
)
from wip_deploy.config_gen.spec_context import (
    SpecContext,
    SpecContextAuth,
    SpecContextFeatures,
    SpecContextNetwork,
    make_spec_context,
    resolve_from_spec,
)

# Grouped by subsystem for readability rather than alpha-sorted; RUF022
# would flatten these purpose-groups into one list, mislabelling the
# section comments (CASE-368 surfaced this when adding resolve_root_redirect).
__all__ = [  # noqa: RUF022
    # Env
    "EnvMap",
    "EnvValue",
    "Literal",
    "SecretRef",
    "ResolvedEnv",
    "resolve_env_source",
    "resolve_component_env",
    "resolve_all_env",
    # Spec context
    "SpecContext",
    "SpecContextAuth",
    "SpecContextNetwork",
    "SpecContextFeatures",
    "make_spec_context",
    "resolve_from_spec",
    # Routing
    "ResolvedRoute",
    "resolve_root_redirect",
    "resolve_routes",
    # Dex
    "DexConfig",
    "DexUserEntry",
    "DexClientEntry",
    "generate_dex_config",
    # Caddy
    "CaddyConfig",
    "generate_caddy_config",
    # NGINX Ingress
    "IngressConfig",
    "generate_ingress_config",
]
