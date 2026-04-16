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

    # Dex proxy — always before /api routes so /dex/* doesn't fall
    # through to the console handler.
    if cfg.has_dex:
        out.write("    handle /dex/* {\n")
        out.write(f"        reverse_proxy {cfg.dex_service}:5556\n")
        out.write("    }\n\n")

    # Auth gateway's own endpoints (sign-in, callback). Only when the
    # gateway is in the path.
    if cfg.gateway_enabled:
        out.write("    handle /auth/* {\n")
        out.write(
            f"        reverse_proxy {cfg.gateway_service}:{cfg.gateway_port}\n"
        )
        out.write("    }\n\n")

    # Routes — sorted by path-depth descending so longer paths match first.
    # Caddy's handle matching is order-independent for non-overlapping
    # prefixes but explicit ordering protects against future ambiguity.
    sorted_routes = sorted(cfg.routes, key=lambda r: (-len(r.path), r.path))

    for route in sorted_routes:
        if route.path == "/":
            # Catch-all route — render last, outside the loop below.
            continue
        _write_route(out, route, cfg)

    # Catch-all (typically the console) goes last.
    for route in sorted_routes:
        if route.path == "/":
            _write_catchall(out, route, cfg)
            break

    out.write("}\n")
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


def _write_route(out, route, cfg: CaddyConfig) -> None:  # type: ignore[no-untyped-def]
    out.write(f"    handle {route.path}/* {{\n")
    if route.auth_protected:
        out.write(f"        forward_auth {cfg.gateway_service}:{cfg.gateway_port} {{\n")
        out.write("            uri /auth/verify\n")
        out.write("            copy_headers X-WIP-User X-WIP-Groups X-API-Key\n")
        out.write("        }\n")
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
        out.write(f"        forward_auth {cfg.gateway_service}:{cfg.gateway_port} {{\n")
        out.write("            uri /auth/verify\n")
        out.write("            copy_headers X-WIP-User X-WIP-Groups X-API-Key\n")
        out.write("        }\n")
    out.write(
        f"        reverse_proxy wip-{route.backend_component}:{route.backend_port}\n"
    )
    out.write("    }\n")
