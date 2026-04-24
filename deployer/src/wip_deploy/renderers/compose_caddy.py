"""Caddyfile emitter.

Converts a `CaddyConfig` into Caddyfile syntax. Caddy isn't YAML — it
has its own directive format — so this is hand-built.

Auth-protected routes use `forward_auth` to delegate to the gateway
(which injects X-WIP-User / X-WIP-Groups / X-API-Key headers).
Streaming routes set `flush_interval -1` so large file downloads
don't buffer.
"""

from __future__ import annotations

from io import StringIO

from wip_deploy.config_gen.caddy import CaddyConfig


def render_caddyfile(cfg: CaddyConfig) -> str:
    """Render a Caddyfile as a string."""
    out = StringIO()

    # Global options (email for letsencrypt, etc.). Kept minimal here;
    # letsencrypt mode would add `email admin@example.com`.
    out.write("{\n")
    out.write("    auto_https disable_redirects\n")
    out.write("}\n\n")

    # Site block. Explicit :port so Caddy binds where our compose port
    # mapping expects (compose maps host:<https_port> → container:<https_port>).
    # Without the :port, Caddy would default to 443 inside the container
    # regardless of what the host-side mapping says.
    if cfg.hostname == "localhost":
        host_line = f"localhost:{cfg.https_port}"
    else:
        host_line = f"{cfg.hostname}:{cfg.https_port}, localhost:{cfg.https_port}"

    out.write(f"{host_line} {{\n")
    _write_tls(out, cfg)
    out.write("\n")

    # All browser-facing routes (/dex/*, /auth/*, /api/*, /apps/*, /)
    # flow through the component manifest → _write_route pipeline. No
    # renderer special-cases — both Caddy and nginx-ingress consume the
    # same route list so they can't disagree about what's exposed.

    # Routes — sorted by path-depth descending so longer paths match first.
    # Caddy's handle matching is order-independent for non-overlapping
    # prefixes but explicit ordering protects against future ambiguity.
    sorted_routes = sorted(cfg.routes, key=lambda r: (-len(r.path), r.path))

    for route in sorted_routes:
        if route.path == "/":
            # Catch-all route — render last, outside the loop below.
            continue
        _write_route(out, route, cfg)

    # Catch-all route (path "/") goes last.
    for route in sorted_routes:
        if route.path == "/":
            _write_catchall(out, route, cfg)
            break

    out.write("}\n")

    # Internal /api/* routing for SSR proxies used to live here as a
    # second `:8080` site block. That's now owned by the wip-router
    # component — its own Caddyfile is rendered by router_caddy.py
    # and emitted by the top-level compose renderer.

    return out.getvalue()


def _write_tls(out: StringIO, cfg: CaddyConfig) -> None:
    if cfg.tls_mode == "internal":
        out.write("    tls {\n")
        out.write("        issuer internal {\n")
        out.write("            lifetime 720h\n")
        out.write("        }\n")
        out.write("    }\n")
    elif cfg.tls_mode == "letsencrypt":
        # Handled via global email directive + auto_https.
        pass
    elif cfg.tls_mode == "external":
        # TLS terminated upstream; Caddy runs plain HTTP.
        out.write("    tls off\n")


def _write_forward_auth(out: StringIO, cfg: CaddyConfig) -> None:
    """Emit the forward_auth block shared by routes and the catch-all.

    The `@unauth status 401` + `handle_response` pair converts the
    gateway's 401 (returned when the session is missing/expired) into a
    302 redirect to /auth/login. Without this, Caddy would propagate
    the 401 straight to the browser, which sees a bare "Unauthorized"
    page instead of a login flow.
    """
    out.write(f"        forward_auth {cfg.gateway_service}:{cfg.gateway_port} {{\n")
    out.write("            uri /auth/verify\n")
    out.write("            copy_headers X-WIP-User X-WIP-Groups X-API-Key\n")
    out.write("            @unauth status 401\n")
    out.write("            handle_response @unauth {\n")
    out.write("                redir /auth/login?return_to={http.request.uri} 302\n")
    out.write("            }\n")
    out.write("        }\n")


def _write_route(out, route, cfg: CaddyConfig) -> None:  # type: ignore[no-untyped-def]
    # Bare-path redirect: /apps/rc (no trailing slash) must 301 to
    # /apps/rc/, otherwise the bare path falls through the /apps/rc/*
    # glob and 404s. CASE-49 / CASE-53 regression — the old setup-wip.sh
    # emitted this per-route; the v2 port missed it.
    #
    # Opted out per-route via `redirect_bare_path=false`. MCP's
    # StreamableHTTP transport mounts at the bare path and redirects
    # /mcp/ → /mcp itself, which loops with this redirect in the
    # opposite direction. SPAs want the redirect (relative-URL
    # resolution); backends with their own canonicalization don't.
    #
    # Important: Caddyfile's `redir` directive parses its first argument
    # as a MATCHER when it starts with `/` (ambiguous grammar). Writing
    # `redir /apps/rc/ permanent` makes Caddy compile `Location:
    # "permanent"` with the matcher `/apps/rc/`. Using the `*` matcher
    # explicitly disambiguates: match-all, destination is the path.
    if route.redirect_bare_path:
        out.write(f"    handle {route.path} {{\n")
        out.write(f"        redir * {route.path}/ permanent\n")
        out.write("    }\n\n")

    # `handle_path` strips the route's prefix before forwarding; `handle`
    # preserves the full request path. MinIO's S3 API is the canonical
    # strip_prefix case — it serves at the root and doesn't know about
    # the public /minio prefix, and SigV2 presigned URLs encode the
    # bucket+key path that must match what MinIO sees.
    directive = "handle_path" if route.strip_prefix else "handle"
    out.write(f"    {directive} {route.path}/* {{\n")
    if route.auth_protected:
        _write_forward_auth(out, cfg)
    backend = f"wip-{route.backend_component}:{route.backend_port}"
    if route.streaming:
        out.write(f"        reverse_proxy {backend} {{\n")
        out.write("            flush_interval -1\n")
        out.write("        }\n")
    else:
        out.write(f"        reverse_proxy {backend}\n")
    out.write("    }\n\n")


def _write_catchall(out, route, cfg: CaddyConfig) -> None:  # type: ignore[no-untyped-def]
    out.write("    handle {\n")
    if route.auth_protected:
        _write_forward_auth(out, cfg)
    out.write(
        f"        reverse_proxy wip-{route.backend_component}:{route.backend_port}\n"
    )
    out.write("    }\n")
