"""Renderers — target-specific translators from (spec + configs) to a
`FileTree` the platform consumes."""

from wip_deploy.renderers.base import FileEntry, FileTree
from wip_deploy.renderers.compose import render_compose

__all__ = ["FileEntry", "FileTree", "render_compose"]
