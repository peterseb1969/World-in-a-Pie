"""Runtime names of an install on the compose and dev targets.

Several installs can run side by side on one host, so every name the
container runtime holds host-wide carries the install's name:

- containers: ``<install>_wip-<component>`` (``default_wip-registry``);
- the network: ``<install>_wip-network``, created with netavark's
  ``isolate: strict``, so no other network on the host reaches it;
- the compose project: pinned to the install name by the top-level ``name:``
  the renderers emit, so it does not follow the install directory's basename.

The underscore cannot occur in an install name (``^[a-z][a-z0-9-]*$``), so
two installs never produce the same name, and the scheme matches the names
compose already gives the install's volumes (``<install>_wip-mongodb-data``).

Inside its network every service keeps the hostname ``wip-<component>``, as
a network alias: each install has its own ``wip-registry``, and nothing that
addresses a peer by that name (service env, the router, the edge) changes.

Images built from source on the dev target are tagged ``dev-<install>``:
installs built from different checkouts (one clone each) would otherwise
share ``<image>:dev``, and a rebuild in one clone would change what the
others run on their next recreate.

k8s scopes by namespace and keeps its ``wip-<component>`` Service names.
"""

from __future__ import annotations

# The key of the install's network in the rendered compose file.
NETWORK_KEY = "wip-network"

# Without it, netavark (1.x, as on Debian) routes between bridge networks,
# so a container on any other network on the host reaches this install's
# services by IP. "strict" also refuses traffic from non-isolated networks;
# "true" would not.
NETWORK_DRIVER_OPTS = {"isolate": "strict"}


def compose_project_name(install: str) -> str:
    """The compose project of ``install``, pinned in the rendered file.

    Left unset, compose derives the project from the install directory's
    basename, while every name this deployer keys on — containers, the network
    and the ``com.docker.compose.project`` label its own container queries
    filter on — is the install name. A ``--install-dir`` whose basename differs
    from ``--name`` would then leave those filters seeing no containers at all.
    """
    return install


def internal_host(component: str) -> str:
    """The hostname peers use for ``component`` inside its install's network."""
    return f"wip-{component}"


def container_name(install: str, component: str) -> str:
    """The container of ``component`` in ``install``."""
    return f"{install}_{internal_host(component)}"


def container_prefix(install: str) -> str:
    """The prefix every container of ``install`` carries."""
    return f"{install}_wip-"


def network_name(install: str) -> str:
    """The name of ``install``'s network on the host."""
    return f"{install}_{NETWORK_KEY}"


def network_block(install: str) -> dict:
    """The top-level ``networks:`` entry of a rendered compose file."""
    return {
        NETWORK_KEY: {
            "name": network_name(install),
            "driver": "bridge",
            "driver_opts": dict(NETWORK_DRIVER_OPTS),
        }
    }


def service_networks(component: str) -> dict:
    """A service's ``networks:`` entry: the install's network, reachable there
    as ``wip-<component>``."""
    return {NETWORK_KEY: {"aliases": [internal_host(component)]}}


def dev_image_tag(install: str) -> str:
    """The tag of the images ``install`` builds from source (dev target)."""
    return f"dev-{install}"
