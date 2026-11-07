"""The help names every optional module, from their manifests (CASE-1106).

The app edge stopped being part of any preset, so an operator now has to
know it exists to turn it on. Every help text that takes a module name
lists the optional modules, one line each, read from the components'
manifests (`category: optional`, the description's first sentence), so the
list cannot go stale; `--preset` says what each preset turns on, and
`examples` shows how to serve attached apps.

Assertions collapse whitespace and strip colour codes: Rich wraps help to the
terminal width and colours it, both of which differ between a local shell and
CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.cli import app
from wip_deploy.module_help import modules_line, optional_modules
from wip_deploy.presets import PRESETS

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _flat(text: str) -> str:
    # Rich's colour codes, box-drawing borders and wrapping removed, so a
    # phrase split across lines or styles still matches. CI's runner gets
    # coloured output (a local shell does not): a styled preset name breaks
    # the phrase with escape codes.
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", text).split())


def _manifest_optional() -> set[str]:
    names = set()
    for f in (REPO_ROOT / "components").glob("*/wip-component.yaml"):
        meta = yaml.safe_load(f.read_text())["metadata"]
        if meta.get("category") == "optional":
            names.add(meta["name"])
    return names


def test_every_optional_manifest_is_listed_with_a_summary():
    listed = dict(optional_modules())
    assert set(listed) == _manifest_optional()
    assert all(listed.values()), listed


def test_the_app_edge_line_says_what_it_needs():
    summary = dict(optional_modules())["app-edge"]
    assert "attached" in summary and summary.endswith("needs the auth gateway")


@pytest.mark.parametrize("argv", [["install", "--help"], ["add-module", "--help"], ["remove-module", "--help"]])
def test_module_options_name_every_optional_module(argv):
    out = _flat(runner.invoke(app, argv, env={"COLUMNS": "200"}).output)
    for name in _manifest_optional():
        assert name in out, (argv, name)
    assert "needs the auth gateway" in out


def test_preset_help_says_what_each_preset_turns_on():
    out = _flat(runner.invoke(app, ["install", "--help"], env={"COLUMNS": "200"}).output)
    for name, preset in PRESETS.items():
        mods = preset.get("modules", {}).get("optional", [])
        assert f"{name} ({', '.join(mods) if mods else 'no optional modules'})" in out, name


def test_examples_show_how_to_serve_attached_apps():
    out = runner.invoke(app, ["examples"]).output
    assert "wip-deploy add-module app-edge" in out
    assert "/api/registry/attachments" in out


def test_modules_line_is_the_same_list():
    assert modules_line().count("(") == len(_manifest_optional())
