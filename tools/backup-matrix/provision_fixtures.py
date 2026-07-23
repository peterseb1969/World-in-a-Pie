#!/usr/bin/env python3
"""Provision the backup/restore test-matrix fixture into a WIP deployment.

Phase 1 of CASE-773. Builds the two small namespaces (NS-A rich, NS-B
counterpart) that span the §3 entity checklist of
``docs/design/backup-restore-test-matrix.md``, through public APIs only, then
emits the measured EXPECTED_COUNTS table the archive/restore cells reuse.

The target is ALWAYS stated explicitly — a named wip-deploy install or a raw
base-url + key-file:

    # against a named install
    provision_fixtures.py --install default

    # against anything else
    provision_fixtures.py --base-url https://host --key-file ./key --no-verify-tls

Namespace names default to MTXA / MTXB for standalone use; the layer-L runner
(Phase 3) passes its own ``<HHMMSS>-00a`` / ``-00b`` names. ``--counts-out``
writes the EXPECTED_COUNTS JSON for downstream conservation asserts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fixtures import FixtureBuilder
from wip_http import TargetError, WipClient, resolve_target

DEFAULT_ONTOLOGY = Path.home() / "Downloads" / "onto-test" / "goslim_generic.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    tgt = p.add_argument_group("target (state it explicitly)")
    tgt.add_argument("--install", help="wip-deploy install name under ~/.wip-deploy/")
    tgt.add_argument("--base-url", help="raw origin, e.g. https://host:8443")
    tgt.add_argument("--key-file", help="path to an API key file")
    tgt.add_argument(
        "--no-verify-tls",
        action="store_true",
        help="skip TLS verification (self-signed dev/prod-test certs)",
    )

    p.add_argument("--ns-a", default="MTXA", help="NS-A prefix (default MTXA)")
    p.add_argument("--ns-b", default="MTXB", help="NS-B prefix (default MTXB)")
    p.add_argument(
        "--ontology",
        type=Path,
        default=DEFAULT_ONTOLOGY,
        help=f"OBO Graph JSON to import as E2 relations (default {DEFAULT_ONTOLOGY})",
    )
    p.add_argument(
        "--counts-out",
        type=Path,
        help="write the measured EXPECTED_COUNTS JSON here",
    )
    p.add_argument(
        "--count-only",
        action="store_true",
        help="skip building; just read and print EXPECTED_COUNTS",
    )
    p.add_argument(
        "--teardown",
        action="store_true",
        help="delete the two fixture namespaces (cascades) and exit",
    )
    args = p.parse_args(argv)

    try:
        target = resolve_target(
            install=args.install,
            base_url=args.base_url,
            key_file=args.key_file,
            verify_tls=not args.no_verify_tls,
        )
    except TargetError as exc:
        print(f"target error: {exc}", file=sys.stderr)
        return 2

    print(f"target: {target.source}")
    print(f"namespaces: NS-A={args.ns_a}  NS-B={args.ns_b}")

    with WipClient(target) as client:
        builder = FixtureBuilder(
            client,
            ns_a=args.ns_a,
            ns_b=args.ns_b,
            ontology_file=args.ontology,
        )

        if args.teardown:
            removed = builder.teardown()
            print(f"torn down: {', '.join(removed) if removed else '(nothing to delete)'}")
            return 0

        if not args.count_only:
            report = builder.build()
            print("\n=== provisioning steps ===")
            for s in report.steps:
                print(f"  + {s}")
            if report.warnings:
                print("\n=== warnings ===")
                for w in report.warnings:
                    print(f"  ! {w}")

        counts = builder.count()
        print("\n=== EXPECTED_COUNTS ===")
        print(json.dumps(counts, indent=2))

        if args.counts_out:
            args.counts_out.write_text(json.dumps(counts, indent=2) + "\n")
            print(f"\nwrote EXPECTED_COUNTS -> {args.counts_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
