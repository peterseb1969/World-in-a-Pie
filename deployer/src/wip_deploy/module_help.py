"""The optional modules, named in the CLI's help.

Help texts that take a module name (`--add`, `--remove`, `add-module`,
`remove-module`) and `--preset` list the optional modules, one line each,
read from the components' manifests (`category: optional`,
`metadata.description`) so the list cannot go stale. The help is built
when the CLI loads, so a repo that cannot be found gives the plain wording
instead of failing `--help`.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml

from wip_deploy.spec.deployment import APP_EDGE_MODULE

# What a module needs besides being named; the spec validators enforce it.
_NEEDS = {APP_EDGE_MODULE: "needs the auth gateway"}


def _summary(description: str) -> str:
    """The description's first sentence, cut at its first aside."""
    text = " ".join(description.split())
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].rstrip(".")
    return re.split(r" — | \(|; ", sentence, maxsplit=1)[0].strip()


@functools.lru_cache(maxsize=1)
def optional_modules() -> tuple[tuple[str, str], ...]:
    """(name, one-line summary) of every optional component, by name."""
    from wip_deploy.discovery import find_repo_root

    try:
        root = find_repo_root(Path(__file__).resolve().parent)
    except FileNotFoundError:
        return ()
    out = []
    for manifest in sorted((root / "components").glob("*/wip-component.yaml")):
        try:
            meta = (yaml.safe_load(manifest.read_text()) or {}).get("metadata") or {}
        except (OSError, yaml.YAMLError):
            continue
        if meta.get("category") != "optional" or not meta.get("name"):
            continue
        summary = _summary(meta.get("description") or "")
        if meta["name"] in _NEEDS:
            summary = f"{summary}; {_NEEDS[meta['name']]}" if summary else _NEEDS[meta["name"]]
        out.append((meta["name"], summary))
    return tuple(sorted(out))


def modules_line() -> str:
    """'app-edge (serves …; needs the auth gateway), garage (…), …', or ''."""
    return ", ".join(f"{n} ({s})" if s else n for n, s in optional_modules())


def with_modules(text: str) -> str:
    """`text` followed by the optional modules, when they can be read."""
    line = modules_line()
    return f"{text} Optional modules: {line}." if line else text
