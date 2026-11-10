"""Renderers — target-specific translators from (spec + configs) to a
`FileTree` the platform consumes."""

from wip_deploy.renderers.base import FileEntry, FileTree
from wip_deploy.renderers.compose import external_ca_host_path, render_compose
from wip_deploy.renderers.dev_simple import render_dev_simple
from wip_deploy.renderers.k8s import render_k8s

__all__ = [
    "FileEntry",
    "FileTree",
    "external_ca_host_path",
    "render_compose",
    "render_dev_simple",
    "render_k8s",
]
