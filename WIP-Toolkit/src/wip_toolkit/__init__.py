"""WIP Toolkit - Backup, migration, and data management for World In a Pie."""

from importlib.metadata import PackageNotFoundError, version

# Single source of truth is pyproject.toml — a hand-maintained constant here
# drifted (0.2.3 while the CLI shipped 0.5.0) because nothing tied them.
try:
    __version__ = version("wip-toolkit")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0.dev0"
