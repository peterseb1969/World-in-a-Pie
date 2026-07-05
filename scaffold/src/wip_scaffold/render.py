"""Template rendering: token substitution + tier filtering.

Byte-compatible with the bash it replaces:
- Token substitution is literal string replacement (the old unquoted-heredoc
  interpolation / `sed s|__WIP_ROOT__|...|g`), never regex — values may
  contain any characters.
- Tier filtering reproduces the two sed programs from the scaffolds:
    tier 3:  delete the marker LINES only, keep content
    tier 2:  delete the inclusive marker-to-marker region
  Markers match whole lines exactly, same as the `^...$` sed anchors.
"""

from __future__ import annotations

TIER3_OPEN = "<!--TIER3-->"
TIER3_CLOSE = "<!--/TIER3-->"


def substitute(text: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


def tier_filter(text: str, tier3: bool) -> str:
    out: list[str] = []
    in_region = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped == TIER3_OPEN:
            in_region = True
            continue  # marker line never reaches the output
        if stripped == TIER3_CLOSE:
            in_region = False
            continue
        if in_region and not tier3:
            continue
        out.append(line)
    return "".join(out)


def render(text: str, tokens: dict[str, str], tier3: bool) -> str:
    return tier_filter(substitute(text, tokens), tier3)
