"""Caddyfile emitter for wip-caddy, the public front door on every target.

Converts a `CaddyConfig` into Caddyfile syntax. Caddy isn't YAML — it
has its own directive format — so this is hand-built. Compose and dev
mount it into the wip-caddy container; k8s mounts it from a ConfigMap into
the wip-caddy Deployment behind the ingress controller.

Auth-protected routes use `forward_auth` to delegate to the gateway,
whose answer carries the user's identity (X-WIP-User / X-WIP-Groups)
onto the request. App routes have the client-sent identity headers
the gateway's answer does not overwrite removed.
Streaming routes set `flush_interval -1` so large file downloads
don't buffer.
"""

from __future__ import annotations

from io import StringIO

from wip_deploy.config_gen.caddy import CaddyConfig
from wip_deploy.config_gen.routing import (
    GATEWAY_IDENTITY_HEADERS,
    ResolvedRoute,
    verify_path,
)
from wip_deploy.renderers.caddy_common import write_api_fallthrough_404, write_unrouted_404


def render_caddyfile(cfg: CaddyConfig) -> str:
    """Render a Caddyfile as a string."""
    out = StringIO()

    # Global options (email for letsencrypt, etc.). Kept minimal here;
    # letsencrypt mode would add `email admin@example.com`.
    out.write("{\n")
    if cfg.plain_http_port is not None:
        # k8s: plain HTTP behind the ingress controller, which owns TLS.
        # Without this Caddy would try to manage certificates for a site
        # it never serves over TLS.
        out.write("    auto_https off\n")
    else:
        out.write("    auto_https disable_redirects\n")
    out.write("}\n\n")

    # Site block. Explicit :port so Caddy binds where our compose port
    # mapping expects (compose maps host:<https_port> → container:<https_port>).
    # Without the :port, Caddy would default to 443 inside the container
    # regardless of what the host-side mapping says.
    if cfg.plain_http_port is not None:
        # Any Host: the Ingress already matched the hostname.
        host_line = f":{cfg.plain_http_port}"
    elif cfg.hostname == "localhost":
        host_line = f"localhost:{cfg.https_port}"
    else:
        host_line = f"{cfg.hostname}:{cfg.https_port}, localhost:{cfg.https_port}"

    out.write(f"{host_line} {{\n")
    if cfg.plain_http_port is None:
        _write_tls(out, cfg)
        out.write("\n")

    # Hardening headers for publicly exposed installs. Site-block level
    # so they apply to every route, app routes included.
    if cfg.emit_hardening_headers:
        out.write("    header {\n")
        out.write('        Strict-Transport-Security "max-age=31536000"\n')
        out.write('        X-Content-Type-Options "nosniff"\n')
        out.write('        X-Frame-Options "SAMEORIGIN"\n')
        out.write('        Referrer-Policy "strict-origin-when-cross-origin"\n')
        out.write("    }\n\n")

    # Bare-host redirect (CASE-368): send `/` to the resolved target (the
    # first app's prefix, or /auth/login when only the gateway is up).
    # Emitted up front, but `handle /` is an EXACT-path matcher — it
    # doesn't overlap /api, /apps/*, etc., so order is irrelevant. Skipped
    # when a component declares its own `/` route (the catch-all below
    # owns it then). The explicit `*` matcher on `redir` avoids Caddy
    # parsing a leading-`/` destination as a matcher (same disambiguation
    # as the bare-path redirect in _write_route).
    has_root_route = any(r.path == "/" for r in cfg.routes)
    if cfg.root_redirect and not has_root_route:
        out.write("    handle / {\n")
        out.write(f"        redir * {cfg.root_redirect} permanent\n")
        out.write("    }\n\n")

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

    # Terminal /api/* guard (CASE-513): a stale aggregate path that matches
    # no /api/<service>/* route 404s loudly instead of falling to Caddy's
    # default empty-200. Less specific than every service handle, so Caddy's
    # longest-match never lets it shadow a live route; emitted before the
    # catch-all so /api paths never reach an SPA catch-all either.
    write_api_fallthrough_404(out)

    # Catch-all route (path "/") goes last. Without one, a terminal 404 so an
    # unrouted path is not answered with Caddy's empty 200 (CASE-944).
    if has_root_route:
        for route in sorted_routes:
            if route.path == "/":
                _write_catchall(out, route, cfg)
                break
    else:
        write_unrouted_404(out)

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


def _write_forward_auth(out: StringIO, cfg: CaddyConfig, route: ResolvedRoute) -> None:
    """Emit the forward_auth block shared by routes and the catch-all.

    The `@unauth status 401` + `handle_response` pair converts the
    gateway's 401 (returned when the session is missing/expired) into a
    302 redirect to /auth/login. Without this, Caddy would propagate
    the 401 straight to the browser, which sees a bare "Unauthorized"
    page instead of a login flow.

    The redir needs the explicit `*` matcher: Caddyfile's ambiguous
    grammar parses a first argument starting with `/` as a PATH MATCHER,
    so `redir /auth/login... 302` compiles to match-path-`/auth/login...`
    with destination "302" — it never matches the protected route, the
    handle_response block does nothing, and the browser gets Caddy's
    default empty 200 instead of the login flow. Same trap as the
    bare-path redirect in _write_route below.
    """
    out.write(f"        forward_auth {cfg.gateway_service}:{cfg.gateway_port} {{\n")
    # The route's allowed groups ride in the verify URI (verify_path), so
    # the gateway can answer 403 for a logged-in user outside them.
    out.write(f"            uri {verify_path(route)}\n")
    _write_forwarded(out, cfg, "            ")
    # copy_headers overwrites a header the client sent, and removes it when
    # the gateway's answer lacks it or carries it empty: a browser cannot
    # pass its own identity through a protected route (checked against
    # Caddy 2.11.7, the `caddy:2` image of 2026-11). Never add
    # `request_header -X-WIP-User` beside forward_auth in the same block:
    # Caddy orders request_header after forward_auth, so it would delete
    # the identity the gateway just supplied.
    out.write(f"            copy_headers {' '.join(GATEWAY_IDENTITY_HEADERS)}\n")
    out.write("            @unauth status 401\n")
    out.write("            handle_response @unauth {\n")
    out.write("                redir * /auth/login?return_to={http.request.uri} 302\n")
    out.write("            }\n")
    out.write("        }\n")




def _write_forwarded(out: StringIO, cfg: CaddyConfig, indent: str) -> None:
    """On k8s, set the public scheme and host toward the gateway and the
    backends. Caddy sees plain HTTP from the ingress controller, so it would
    otherwise send X-Forwarded-Proto: http; and a client's own X-Forwarded-*
    is never trusted, since the controller's addresses are unknown here."""
    if cfg.forwarded_scheme:
        out.write(f"{indent}header_up X-Forwarded-Proto {cfg.forwarded_scheme}\n")
    if cfg.forwarded_host:
        out.write(f"{indent}header_up X-Forwarded-Host {cfg.forwarded_host}\n")


def _write_reverse_proxy(out, backend: str, route, cfg: CaddyConfig) -> None:  # type: ignore[no-untyped-def]
    """One reverse_proxy directive, with the route's proxy options.

    `upstream_host_header` sets Host to the upstream's host:port —
    backends that validate the signed Host of presigned URLs (object
    storage) reject the public host otherwise. `streaming` disables
    response buffering.
    """
    opts: list[str] = []
    if cfg.forwarded_scheme:
        opts.append(f"            header_up X-Forwarded-Proto {cfg.forwarded_scheme}\n")
    if cfg.forwarded_host:
        opts.append(f"            header_up X-Forwarded-Host {cfg.forwarded_host}\n")
    if route.upstream_host_header:
        opts.append("            header_up Host {upstream_hostport}\n")
    if route.streaming:
        opts.append("            flush_interval -1\n")
    if opts:
        out.write(f"        reverse_proxy {backend} {{\n")
        for line in opts:
            out.write(line)
        out.write("        }\n")
    else:
        out.write(f"        reverse_proxy {backend}\n")

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

    # CASE-248: preserve-prefix subpaths under a strip_prefix route.
    # An object store's S3 API at /<prefix>/<bucket>/<key> needs the
    # prefix stripped (presigned URLs encode the bucket+key path), while
    # any of its own endpoints that live under the public prefix need it
    # preserved. Emit `handle` blocks for those subpaths first; Caddy's
    # longest-match picks them over the trailing handle_path block.
    backend = cfg.backend(route.backend_component, route.backend_port)
    if route.strip_prefix and route.preserve_prefix_subpaths:
        for sub in route.preserve_prefix_subpaths:
            out.write(f"    handle {sub}/* {{\n")
            if route.auth_protected:
                _write_forward_auth(out, cfg, route)
            _write_identity_strip(out, route)
            _write_reverse_proxy(out, backend, route, cfg)
            out.write("    }\n\n")

    # `handle_path` strips the route's prefix before forwarding; `handle`
    # preserves the full request path. Garage's S3 API is the canonical
    # strip_prefix case — it serves at the root and doesn't know about
    # the public /garage prefix, and presigned URLs encode the bucket+key
    # path that must match what the store sees.
    directive = "handle_path" if route.strip_prefix else "handle"

    def _emit_block(matcher_path: str) -> None:
        out.write(f"    {directive} {matcher_path} {{\n")
        if route.auth_protected:
            _write_forward_auth(out, cfg, route)
        _write_identity_strip(out, route)
        _write_reverse_proxy(out, backend, route, cfg)
        out.write("    }\n\n")

    # CASE-312: when redirect_bare_path is False, no companion `handle
    # {route.path}` block was emitted above. The wildcard matcher
    # `{route.path}/*` alone does NOT match the bare path (Caddy's path
    # glob requires the trailing slash). Without an explicit bare-path
    # block, requests for `/<path>` fall through to Caddy's default
    # 200-with-empty-body behaviour — which the MCP StreamableHTTP
    # transport hits as "Unexpected content type: null" and crashes.
    # Emit a sibling bare-path handle block (Caddy's first-match wins,
    # both forward to the same backend) so bare requests are served too.
    if not route.redirect_bare_path:
        _emit_block(route.path)
    _emit_block(f"{route.path}/*")


def _write_identity_strip(out, route: ResolvedRoute, indent: str = "        ") -> None:  # type: ignore[no-untyped-def]
    """Remove the client-sent identity headers the gateway's answer does not
    overwrite, so an app never reads a user the browser made up. Safe beside
    forward_auth only because none of these is one the gateway supplies
    (request_header runs after forward_auth's copy).

    Only the hyphenated spellings are named. Caddy itself drops a header
    spelled with underscores (X_WIP_User, X-WIP_Groups) before any route
    sees it, so such a header never reaches an app; checked against Caddy
    2.11.7, the `caddy:2` image of 2026-11, while Traefik 3.7 forwards them.
    If a later Caddy forwards them, add those spellings here and in the app
    edge's strip."""
    for header in route.client_identity_headers_to_strip():
        out.write(f"{indent}request_header -{header}\n")


def _write_catchall(out, route, cfg: CaddyConfig) -> None:  # type: ignore[no-untyped-def]
    out.write("    handle {\n")
    if route.auth_protected:
        _write_forward_auth(out, cfg, route)
    _write_identity_strip(out, route, indent="        ")
    _write_reverse_proxy(out, cfg.backend(route.backend_component, route.backend_port), route, cfg)
    out.write("    }\n")
