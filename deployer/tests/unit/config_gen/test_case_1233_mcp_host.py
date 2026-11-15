"""CASE-1233: /mcp reached through a proxy is answered 421 "Invalid Host header".

The MCP server's DNS-rebinding protection accepts only the hosts on its
allow-list. A proxy that forwards the client's Host (wip-router, or the
install's public hostname) is refused. So the /mcp route sends the server
its own host as Host, at the router and at the public edge, and the
allow-list holds every name a proxy dials: the short container name, and
on k8s the Service FQDN the edge dials.
"""

from __future__ import annotations

from wip_deploy.config_gen.caddy import generate_caddy_config
from wip_deploy.config_gen.env import Literal, resolve_component_env
from wip_deploy.config_gen.router import generate_router_config
from wip_deploy.config_gen.spec_context import SpecContext
from wip_deploy.discovery import Discovery
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.router_caddy import render_router_caddyfile
from wip_deploy.spec import Deployment

HOST_REWRITE = "header_up Host {upstream_hostport}"


def _handle_block(caddyfile: str, matcher: str) -> str:
    """The text of the first `handle <matcher> {` block. Both Caddyfiles
    close a site-level handle with a `}` line at four spaces' indent."""
    start = caddyfile.index(f"    handle {matcher} {{\n")
    return caddyfile[start:caddyfile.index("\n    }\n", start)]


def test_the_router_sends_the_mcp_server_its_own_host(
    compose_deployment: Deployment, real_discovery: Discovery,
) -> None:
    cfg = generate_router_config(compose_deployment, real_discovery.components, real_discovery.apps)
    caddyfile = render_router_caddyfile(cfg)
    for matcher in ("/mcp", "/mcp/*"):
        block = _handle_block(caddyfile, matcher)
        assert "reverse_proxy wip-mcp-server:8007 {" in block
        assert HOST_REWRITE in block, block
    # Only routes that ask for it: the API routes keep the caller's Host.
    assert HOST_REWRITE not in _handle_block(caddyfile, "/api/registry/*")


def test_a_streaming_router_route_keeps_its_flush_with_or_without_the_rewrite(
    compose_deployment: Deployment, real_discovery: Discovery,
) -> None:
    cfg = generate_router_config(compose_deployment, real_discovery.components, real_discovery.apps)
    block = _handle_block(render_router_caddyfile(cfg), "/api/document-store/*")
    assert "flush_interval -1" in block
    assert HOST_REWRITE not in block


def test_the_public_edge_sends_the_mcp_server_its_own_host(
    compose_deployment: Deployment, k8s_deployment: Deployment, real_discovery: Discovery,
) -> None:
    for deployment, dialled in (
        (compose_deployment, "wip-mcp-server:8007"),
        (k8s_deployment, "wip-mcp-server.wip.svc.cluster.local:8007"),
    ):
        caddyfile = render_caddyfile(
            generate_caddy_config(deployment, real_discovery.components, real_discovery.apps)
        )
        for matcher in ("/mcp", "/mcp/*"):
            block = _handle_block(caddyfile, matcher)
            assert f"reverse_proxy {dialled} {{" in block, block
            assert HOST_REWRITE in block, block


def _mcp_env(deployment: Deployment, ctx: SpecContext, discovery: Discovery) -> dict:
    mcp = next(c for c in discovery.components if c.metadata.name == "mcp-server")
    return resolve_component_env(mcp, deployment, ctx, discovery.components, discovery.apps).required


def test_the_allow_list_holds_every_host_a_proxy_dials(
    compose_deployment: Deployment, k8s_deployment: Deployment,
    ctx_compose: SpecContext, ctx_k8s: SpecContext, real_discovery: Discovery,
) -> None:
    # The router dials the short name on every target; the edge dials the
    # short name on compose and dev, the Service FQDN on k8s.
    compose = _mcp_env(compose_deployment, ctx_compose, real_discovery)
    assert compose["MCP_ALLOWED_HOST"] == Literal("wip-mcp-server")
    assert compose["MCP_ALLOWED_SERVICE_HOST"] == Literal("wip-mcp-server")

    k8s = _mcp_env(k8s_deployment, ctx_k8s, real_discovery)
    assert k8s["MCP_ALLOWED_HOST"] == Literal("wip-mcp-server")
    assert k8s["MCP_ALLOWED_SERVICE_HOST"] == Literal("wip-mcp-server.wip.svc.cluster.local")
    router = generate_router_config(k8s_deployment, real_discovery.components, real_discovery.apps)
    assert next(r for r in router.routes if r.path == "/mcp").backend_host == "wip-mcp-server"
