"""wip_scaffold — the agent-scaffold engine (docs/design/scaffold-revamp.md).

Writes every file surface of a backend clone or an app repo: the surface
matrix (surfaces.py) names each file and the policy it is written under, and
the engine (engine.py) plans every surface before writing any. The wrappers,
scripts/setup-backend-agent.sh and scripts/create-app-project.sh, resolve
their inputs, run `python -m wip_scaffold`, and keep the actions that are not
file writes.
"""

__version__ = "0.0.1"
