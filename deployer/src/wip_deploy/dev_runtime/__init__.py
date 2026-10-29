"""Code that runs inside dev-target containers, never in the deployer.

The dev renderer reads these files as text, writes them into the install
directory, and bind-mounts them read-only into the containers that run
them. Nothing in the deployer imports this package.
"""
