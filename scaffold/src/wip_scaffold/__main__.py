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
from .surfaces import app_surfaces, backend_surfaces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wip_scaffold")
    sub = parser.add_subparsers(dest="role", required=True)

    b = sub.add_parser("backend")
    b.add_argument("--wip-root", required=True, type=Path)
    b.add_argument("--tier3", action="store_true")
    b.add_argument("--dry-run", action="store_true")

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

    args = parser.parse_args(argv)

    if args.role == "backend":
        ctx = Context(
            wip_root=args.wip_root.resolve(),
            target_root=args.wip_root.resolve(),
            tier3=args.tier3,
            dry_run=args.dry_run,
        )
        surfaces = backend_surfaces()
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

    log = run_surfaces(surfaces, ctx)
    for line in log:
        print(f"   {line}")
    for note in ctx.notes:
        print(f"   {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
