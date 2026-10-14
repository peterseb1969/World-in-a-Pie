"""Three-way merge of a scaffold-owned JSON baseline into an app-extended file.

`.claude/settings.json` is both scaffold-owned (the one baseline every repo
must carry, so baseline changes reach every repo) and app-extended (apps add
their own hooks and permissions, and commit them). Regenerating the file
wholesale silently dropped every app-added hook, among them action-triggered
gates that exist precisely because a written rule did not hold. A plain union
is not enough either: it can never retire an entry the scaffold drops, and it
re-adds a baseline entry an app deliberately removed.

So each run compares three versions:

- ``old``: the baseline applied last time (recorded beside the file),
- ``new``: the baseline rendered now,
- ``cur``: the file as it is in the repo.

Rules, applied recursively:

- objects: keys only the app set are kept untouched; a key the baseline
  introduces is set when absent (unless the app removed it after the scaffold
  had set it); a key the baseline retires is removed only if the app left it
  exactly as the scaffold set it.
- lists (permission strings, hook matcher groups): entries the baseline adds
  are appended when missing; entries it retires are removed when present;
  everything else stays, in the app's order. Entries compare by canonical
  JSON, so an app that edited a scaffold entry made it its own: it is never
  removed, and a changed baseline entry is added beside it.
- scalars: the baseline's new value wins only if the app still holds the
  value the scaffold last set.

With no recorded ``old`` (the first run after this policy landed, or a file
the scaffold never wrote), nothing is removed: baseline entries are only
added.

Stdlib only, like the engine.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

MISSING: Any = object()
"""No value at all, as distinct from JSON null."""


def baseline_record_path(rel: str) -> str:
    """Where the applied baseline of `rel` is recorded: `.claude/settings.json`
    → `.claude/.settings-baseline.json`."""
    p = PurePosixPath(rel)
    return str(p.with_name(f".{p.stem}-baseline{p.suffix}"))


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _describe(value: Any) -> str:
    text = value if isinstance(value, str) else _key(value)
    return text if len(text) <= 80 else text[:77] + "..."


def three_way(old: Any, new: Any, cur: Any) -> tuple[Any, list[str]]:
    """Merge baseline `new` into `cur`, given the previously applied `old`
    (or MISSING). Returns the merged value and a list of readable changes."""
    changes: list[str] = []
    merged = _merge(old, new, cur, "", changes)
    return merged, changes


def _merge(old: Any, new: Any, cur: Any, path: str, changes: list[str]) -> Any:
    if isinstance(new, dict) and isinstance(cur, dict):
        return _merge_dict(old if isinstance(old, dict) else MISSING, new, cur, path, changes)
    if isinstance(new, list) and isinstance(cur, list):
        return _merge_list(old if isinstance(old, list) else MISSING, new, cur, path, changes)
    # Scalars, or a type the app changed: the baseline wins only where the
    # app still holds what the scaffold last set.
    if old is not MISSING and cur == old and new != cur:
        changes.append(f"{path or '.'}: {_describe(cur)} -> {_describe(new)}")
        return new
    return cur


def _merge_dict(old: Any, new: dict, cur: dict, path: str, changes: list[str]) -> dict:
    out = dict(cur)
    for k, nv in new.items():
        ov = old.get(k, MISSING) if old is not MISSING else MISSING
        if k not in cur:
            if ov is not MISSING:
                continue  # the app removed a key the scaffold had set
            out[k] = nv
            changes.append(f"{path}{k}: added")
        else:
            out[k] = _merge(ov, nv, cur[k], f"{path}{k}.", changes)
    if old is not MISSING:
        for k, ov in old.items():
            if k not in new and k in out and out[k] == ov:
                del out[k]
                changes.append(f"{path}{k}: removed (retired from the baseline)")
    return out


def _merge_list(old: Any, new: list, cur: list, path: str, changes: list[str]) -> list:
    where = path.rstrip(".") or "."
    old_keys = {_key(x) for x in old} if old is not MISSING else set()
    new_keys = {_key(x) for x in new}
    out = []
    for item in cur:
        k = _key(item)
        if k in old_keys and k not in new_keys:
            changes.append(f"{where}: - {_describe(item)}")
            continue
        out.append(item)
    present = {_key(x) for x in out}
    for item in new:
        k = _key(item)
        if k in present or k in old_keys:
            continue  # already there, or the app removed it deliberately
        out.append(item)
        present.add(k)
        changes.append(f"{where}: + {_describe(item)}")
    return out
