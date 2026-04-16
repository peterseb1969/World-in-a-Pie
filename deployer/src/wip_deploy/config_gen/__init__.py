"""Shared config generators.

Pure functions that take (Deployment, components, apps) and produce
structured config objects. Renderers consume these objects; they never
parse generator output.

Dependency direction:
  spec_context  ←  env, routing
  routing       ←  caddy, nginx_ingress
  env, routing  ←  (renderers, later)
  dex           ←  (renderers, later)
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
from wip_deploy.config_gen.nginx_ingress import (
    IngressConfig,
    IngressRule,
    generate_ingress_config,
)
from wip_deploy.config_gen.routing import ResolvedRoute, resolve_routes
from wip_deploy.config_gen.spec_context import (
    SpecContext,
    SpecContextAuth,
    SpecContextFeatures,
    SpecContextNetwork,
    make_spec_context,
    resolve_from_spec,
)

__all__ = [
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
    "IngressRule",
    "generate_ingress_config",
]
