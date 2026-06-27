"""Caddyfile emit helpers shared by both Caddy renderers.

Kept in one place so the edge renderer (`compose_caddy`) and the internal
router renderer (`router_caddy`) can't drift on shared invariants — e.g.
the `/api/*` fall-through guard (CASE-513).
"""

from __future__ import annotations

from io import StringIO


def write_api_fallthrough_404(out: StringIO) -> None:
    """Emit a terminal `handle /api/*` block that returns 404.

    Without it, a request to a stale aggregate path (e.g. /api/documents/query,
    /api/templates) that matches no `/api/<service>/*` route falls through to
    Caddy's default: an empty 200 (content-length 0), not a 404. That's the
    CLAUDE.md §14 trap — a raw-REST consumer parses "" as a valid-but-empty
    body (the same silent-zero failure family as CASE-457).

    Every real `/api` route is service-prefixed (`/api/<service>/...`), so this
    guard is strictly less specific than every declared service handle; Caddy's
    longest-match keeps it from ever shadowing a live service or a bare-path
    redirect. k8s/nginx-ingress already 404s unmatched paths, so this is a
    Caddy-only concern — hence it lives in the Caddy emitters, not the route
    model.
    """
    out.write("    handle /api/* {\n")
    out.write("        respond 404\n")
    out.write("    }\n\n")
