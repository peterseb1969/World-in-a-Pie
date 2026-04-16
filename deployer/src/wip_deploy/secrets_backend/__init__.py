"""Secret backends — pluggable persistence for deployment secrets.

Currently only the file backend is implemented. The k8s-secret and sops
backends arrive in later steps alongside the renderers that need them.
"""

from wip_deploy.secrets_backend.base import ResolvedSecrets, SecretBackend
from wip_deploy.secrets_backend.file import FileSecretBackend

__all__ = ["ResolvedSecrets", "SecretBackend", "FileSecretBackend"]
