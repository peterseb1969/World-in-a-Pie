"""wip_scaffold — the agent-scaffold engine (docs/design/scaffold-revamp.md).

Writes the surface matrix of a backend clone or an app repo: surfaces.py
names each file and the policy it is written under, and the engine
(engine.py) plans every surface before writing any. The wrappers,
scripts/setup-backend-agent.sh and scripts/create-app-project.sh, resolve
their inputs, run `python -m wip_scaffold`, and keep the actions (venv,
namespace, library builds, git init) and a few files bash still writes: the
backend's .mcp.json for the ssh and http transports, .claude/kb.json
(enable_kb), and the app's .gitignore sentinel entries.
"""

__version__ = "0.0.1"
