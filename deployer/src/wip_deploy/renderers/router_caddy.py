"""Caddyfile emitter for the wip-router internal API aggregator.

The router is a target-agnostic concept (see `config_gen/router.py`).
Both compose and k8s deploy wip-router as a Caddy container with the
same Caddyfile — same content, different mount mechanism (bind-mount
vs ConfigMap).

The Caddyfile here is intentionally minimal: plain HTTP on the
listen port, one `handle` block per route, no TLS, no forward_auth
(the router is internal; callers provide X-API-Key themselves).
"""

from __future__ import annotations

from io import StringIO

from wip_deploy.config_gen.router import RouterConfig


def render_router_caddyfile(cfg: RouterConfig) -> str:
    """Render the router's Caddyfile as a string."""
    out = StringIO()

    # Disable auto_https: this Caddy only listens on an internal HTTP
    # port, never terminates TLS. Without the directive Caddy probes
    # upstream ACME on startup even when no HTTPS site is declared.
    out.write("{\n")
    out.write("    auto_https off\n")
    out.write("}\n\n")

    out.write(f":{cfg.listen_port} {{\n")

    for route in cfg.routes:
        backend = f"{route.backend_host}:{route.backend_port}"
        # CASE-378: emit a sibling bare-path handle when the route opts
        # out of redirect (the /mcp pattern from CASE-312). Without it,
        # `handle <path>/*` doesn't match the bare path; Caddy returns
        # 200 + empty body; MCP client crashes with "Unexpected content
        # type: null". For routes that DO redirect bare to trailing-
        # slash (the /api/<svc>/* default), the bare match doesn't
        # matter — apps always send a sub-path.
        matchers: list[str] = []
        if not route.redirect_bare_path:
            matchers.append(route.path)
        matchers.append(f"{route.path}/*")

        for matcher in matchers:
            out.write(f"    handle {matcher} {{\n")
            if route.streaming:
                out.write(f"        reverse_proxy {backend} {{\n")
                out.write("            flush_interval -1\n")
                out.write("        }\n")
            else:
                out.write(f"        reverse_proxy {backend}\n")
            out.write("    }\n")

    out.write("}\n")

    return out.getvalue()
