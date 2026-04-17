"""Renderers — target-specific translators from (spec + configs) to a
`FileTree` the platform consumes."""

from wip_deploy.renderers.base import FileEntry, FileTree
from wip_deploy.renderers.compose import render_compose
from wip_deploy.renderers.k8s import render_k8s

__all__ = ["FileEntry", "FileTree", "render_compose", "render_k8s"]
