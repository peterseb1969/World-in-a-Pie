"""`wip-deploy examples` must keep up with the CLI surface.

The examples verb is the operator's front door (the root help epilog
points at it), so a verb that ships without at least a mention there is
invisible to anyone following the built-in guidance. That is exactly how
the security checklist and the durable-key tier went undiscovered for a
capability wave: the verbs landed with good --help text, but the curated
map never mentioned them.

The contract pinned here: every registered command name appears in
_EXAMPLES_TEXT — as a worked example or in the "More verbs" footer.
A new verb must add one or the other; there is no silent third option.
"""

from __future__ import annotations

from wip_deploy.cli import _EXAMPLES_TEXT, app

# The examples verb itself is the one legitimate omission: it prints the
# text, it does not need to advertise itself inside it.
EXEMPT = {"examples"}


def _registered_command_names() -> set[str]:
    names = set()
    for cmd in app.registered_commands:
        # Explicit name (e.g. @app.command("rotate-key")) wins; otherwise
        # typer derives the name from the function, dashifying underscores.
        name = cmd.name or cmd.callback.__name__.replace("_", "-")
        names.add(name)
    return names


def test_every_verb_is_mentioned_in_examples():
    missing = sorted(
        name
        for name in _registered_command_names() - EXEMPT
        if name not in _EXAMPLES_TEXT
    )
    assert not missing, (
        f"CLI verbs missing from _EXAMPLES_TEXT: {missing} — add a worked "
        "example or list them in the 'More verbs' footer."
    )


def test_examples_cover_the_safety_surface():
    """The two discovery-critical workflows stay as worked examples, not
    footer mentions: the pre-exposure checklist and the durable key tier."""
    for needle in (
        "verify --security",
        "--api-key",
        "--api-keys-file",
        "rotate-key",
        "redeploy",
    ):
        assert needle in _EXAMPLES_TEXT, f"examples lost the {needle!r} workflow"


def test_examples_do_not_repeat_the_restart_env_mistake():
    """`compose restart` keeps the container's environment; the examples
    must never again present restart as the way to apply an env change."""
    for line in _EXAMPLES_TEXT.splitlines():
        if "restart" in line and "env" in line.lower():
            assert "NOT" in line, (
                "examples text pairs restart with env changes without the "
                "does-NOT-re-read warning: " + line.strip()
            )
