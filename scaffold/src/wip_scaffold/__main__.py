"""CLI for the surface engine.

Called by the bash entry points, which keep argument parsing, mode
detection, guards, and the not-yet-migrated surfaces:

  python -m wip_scaffold backend --wip-root DIR [--tier3] [--dry-run]
  python -m wip_scaffold app --wip-root DIR --app-dir DIR \
      --app-name S --app-slug S --dev-namespace S --key-file PATH \
      --preset S [--role-prefix S] [--tier3] [--refresh] \
      [--force-claude-md] [--dry-run]

Output: one line per action (same operator-facing style as the bash it
replaces), notes/warnings verbatim. Exit non-zero on any failure — the
wrappers run under `set -euo pipefail` and must abort with us.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import Context, run_surfaces
from .surfaces import (
    app_surfaces,
    backend_surfaces,
    bootstrap_surface,
    archive_wheel_surface,
    client_lib_surface,
    env_surface,
    mcp_json_surface,
    query_scaffold_surfaces,
    toolkit_surface,
)


def _add_mcp_args(parser) -> None:
    # Present => the engine writes .mcp.json; absent (remote backend
    # transports) => the wrapper writes its own shape and skips this.
    parser.add_argument("--mcp-python", default="")
    parser.add_argument("--mcp-base-url", default="")
    parser.add_argument("--mcp-key-file", default="")
    parser.add_argument("--mcp-key", default="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wip_scaffold")
    sub = parser.add_subparsers(dest="role", required=True)

    b = sub.add_parser("backend")
    b.add_argument("--wip-root", required=True, type=Path)
    b.add_argument("--tier3", action="store_true")
    b.add_argument("--dry-run", action="store_true")
    _add_mcp_args(b)

    a = sub.add_parser("app")
    a.add_argument("--wip-root", required=True, type=Path)
    a.add_argument("--app-dir", required=True, type=Path)
    a.add_argument("--app-name", required=True)
    a.add_argument("--app-slug", required=True)
    a.add_argument("--dev-namespace", required=True)
    a.add_argument("--key-file", required=True)
    a.add_argument("--preset", required=True)
    a.add_argument("--role-prefix", default="")
    a.add_argument("--tier3", action="store_true")
    a.add_argument("--refresh", action="store_true")
    a.add_argument("--force-claude-md", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--seed-bootstrap", action="store_true")
    a.add_argument("--query-scaffold", action="store_true")
    a.add_argument("--lib-client", default="")
    a.add_argument("--lib-react", default="")
    a.add_argument("--lib-proxy", default="")
    a.add_argument("--toolkit-wheel", default="")
    a.add_argument("--archive-wheel", default="")
    a.add_argument("--write-env", action="store_true")
    _add_mcp_args(a)

    args = parser.parse_args(argv)

    if args.role == "backend":
        ctx = Context(
            wip_root=args.wip_root.resolve(),
            target_root=args.wip_root.resolve(),
            tier3=args.tier3,
            dry_run=args.dry_run,
        )
        surfaces = backend_surfaces()
        if args.mcp_python:
            surfaces.insert(0, mcp_json_surface(
                args.mcp_python, args.mcp_base_url, args.mcp_key_file, args.mcp_key))
    else:
        ctx = Context(
            wip_root=args.wip_root.resolve(),
            target_root=args.app_dir.resolve(),
            tier3=args.tier3,
            refresh=args.refresh,
            dry_run=args.dry_run,
            force_claude_md=args.force_claude_md,
            role_prefix=args.role_prefix,
            tokens={
                "__APP_NAME__": args.app_name,
                "__APP_SLUG__": args.app_slug,
                "__DEV_NAMESPACE__": args.dev_namespace,
                "__WIP_API_KEY_FILE__": args.key_file,
            },
        )
        surfaces = app_surfaces(
            {
                "APP_NAME": args.app_name,
                "APP_SLUG": args.app_slug,
                "DEV_NAMESPACE": args.dev_namespace,
                "PRESET": args.preset,
            }
        )
        if args.mcp_python:
            surfaces.insert(0, mcp_json_surface(
                args.mcp_python, args.mcp_base_url, args.mcp_key_file, args.mcp_key))
        if args.query_scaffold:
            surfaces = query_scaffold_surfaces(
                args.app_name, args.app_slug, args.dev_namespace) + surfaces
        for lib, path in (("client", args.lib_client), ("react", args.lib_react),
                          ("proxy", args.lib_proxy)):
            if path:
                surfaces.append(client_lib_surface(lib, path))
        if args.toolkit_wheel:
            surfaces.append(toolkit_surface(args.toolkit_wheel))
        if args.archive_wheel:
            surfaces.append(archive_wheel_surface(args.archive_wheel))
        if args.seed_bootstrap:
            surfaces.append(bootstrap_surface())
        if args.write_env:
            surfaces.append(env_surface(args.key_file))

    log = run_surfaces(surfaces, ctx)
    for line in log:
        print(f"   {line}")
    for note in ctx.notes:
        print(f"   {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
