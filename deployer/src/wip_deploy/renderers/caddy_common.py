"""Caddyfile emit helpers shared by both Caddy renderers.

Kept in one place so the edge renderer (`compose_caddy`) and the internal
router renderer (`router_caddy`) can't drift on shared invariants — e.g.
the `/api/*` fall-through guard (CASE-513).
"""

from __future__ import annotations

from io import StringIO


def write_api_fallthrough_404(out: StringIO) -> None:
    """Emit a terminal block that answers unrouted `/api` paths with a JSON 404.

    Without it, a request to a stale aggregate path (e.g. /api/documents/query)
    or to bare `/api` falls through to Caddy's default: an empty 200
    (content-length 0). That's the CLAUDE.md §14 trap — a raw-REST consumer
    parses "" as a valid-but-empty body (the same silent-zero failure family
    as CASE-457). The body is the one every app returns for an unmatched API
    route (`{"error":"not_found"}`), so a JSON client can tell "route
    missing" from data without parsing an empty body or an HTML page.

    Every real `/api` route is service-prefixed (`/api/<service>/...`), so this
    guard is strictly less specific than every declared service handle; Caddy's
    longest-match keeps it from ever shadowing a live service or a bare-path
    redirect. A named matcher, because `handle` takes one matcher token and
    `/api/*` alone does not match bare `/api`.

    The k8s ingress gets the same answers from the router: its catch-all
    Ingress sends every unrouted path there (nginx_ingress.catch_all_backend).
    """
    out.write("    @api_unrouted path /api /api/*\n")
    out.write("    handle @api_unrouted {\n")
    out.write("        header Content-Type application/json\n")
    out.write('        respond `{"error":"not_found"}` 404\n')
    out.write("    }\n\n")


def write_unrouted_404(out: StringIO) -> None:
    """Emit a terminal, matcher-less block that 404s every other unrouted path.

    Matcher-less `handle` is the least specific block, so it only answers
    what no route, redirect or the /api guard took; without it those paths
    get Caddy's empty 200. Emit it only when no route claims `/` itself.
    """
    out.write("    handle {\n")
    out.write("        respond 404\n")
    out.write("    }\n\n")
