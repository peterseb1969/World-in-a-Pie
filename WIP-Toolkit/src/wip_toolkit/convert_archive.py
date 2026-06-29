"""Convert an old single-namespace (v2.0 flat) archive to the v3 multi-namespace
layout (CASE-542).

One-way: v2.0 → v3.0. The v3 engines read only v3 archives; run this once over
any archive produced before the format change.

Old layout (flat):   manifest.json, <entity>.jsonl, blobs/<file_id>
New layout (v3):      manifest.json, namespaces/<ns>/<entity>.jsonl, blobs/<file_id>

CLI:  python -m wip_toolkit.convert_archive OLD.zip NEW.zip
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from .archive import BLOBS_DIR, ENTITY_FILES, MANIFEST_FILE, SYNONYMS_FILE, _entity_path
from .models import Manifest, NamespaceEntry


def convert_archive(src_path: str | Path, dst_path: str | Path) -> Path:
    """Rewrite a flat v2.0 archive as a 1-namespace v3 archive.

    Idempotent on a v3 input is NOT supported — pass a v2.0 archive. Raises
    ValueError if the source is already v3 or carries no namespace.
    """
    src = Path(src_path)
    dst = Path(dst_path)

    with zipfile.ZipFile(src, "r") as zin:
        names = set(zin.namelist())
        old = Manifest(**json.loads(zin.read(MANIFEST_FILE)))

        if old.format_version.startswith("3") or any(
            n.startswith("namespaces/") for n in names
        ):
            raise ValueError(
                f"{src} is already v3 (format_version={old.format_version}); "
                "nothing to convert"
            )

        ns = old.namespace
        if not ns:
            raise ValueError(f"{src} has no namespace in its manifest to convert")

        v3 = Manifest(
            format_version="3.0",
            tool_version=old.tool_version,
            exported_at=old.exported_at,
            source_host=old.source_host,
            namespaces=[
                NamespaceEntry(
                    prefix=ns,
                    namespace_config=old.namespace_config,
                    counts=old.counts,
                )
            ],
            namespace=ns,
            namespace_config=old.namespace_config,
            source_install=old.source_install,
            include_inactive=old.include_inactive,
            include_files=old.include_files,
            include_all_versions=old.include_all_versions,
            closure=old.closure,
            counts=old.counts,
        )

        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.writestr(MANIFEST_FILE, v3.model_dump_json(indent=2))

            # Entity JSONL: root → namespaces/<ns>/<entity>.jsonl
            for entity_type, filename in ENTITY_FILES.items():
                if filename in names:
                    data = zin.read(filename)
                    if data.strip():
                        zout.writestr(_entity_path(ns, entity_type), data)

            # Legacy flat synonyms file (carried through unchanged).
            if SYNONYMS_FILE in names:
                zout.writestr(SYNONYMS_FILE, zin.read(SYNONYMS_FILE))

            # Blobs stay flat (namespace-agnostic, globally-unique file_ids).
            for name in names:
                if name.startswith(BLOBS_DIR) and len(name) > len(BLOBS_DIR):
                    zout.writestr(name, zin.read(name))

    return dst


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m wip_toolkit.convert_archive OLD.zip NEW.zip", file=sys.stderr)
        return 2
    try:
        out = convert_archive(args[0], args[1])
    except (ValueError, FileNotFoundError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"converted → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
