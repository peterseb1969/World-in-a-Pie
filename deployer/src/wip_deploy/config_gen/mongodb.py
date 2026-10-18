"""MongoDB authentication: one user per client, converged on every start.

MongoDB used to accept anonymous clients, so any workload on the network
could read and write every service's database. It now runs with
authentication, and each component or app that declares a `mongo_client`
block connects as its own user:

- platform components declare the databases they own (`readWrite`), plus
  narrow `collection_privileges` where they reach into another component's
  database — registry's namespace deletion may only `find` and `remove` in
  the seven collections it deletes from;
- apps hold roles only: `read-all` grants `read` on every database a
  component declares. Apps never write to MongoDB, so WIP data changes only
  through WIP's API, where validation, identity, events and audit apply.

The deployer generates one password secret per client
(`mongo-password-<name>`) plus `mongo-root-password`, injects
`MONGO_USER` / `MONGO_PASSWORD` into each client (read by
`wip_auth.mongo`), and gives MongoDB every password as an environment
variable.

**The start step** (`render_mongodb_entrypoint`) runs on every MongoDB
start, the same way for a fresh data directory, an existing one, and one
whose users or passwords changed:

1. start a temporary `mongod` WITHOUT `--auth`, bound to 127.0.0.1 on a
   side port — unreachable from other containers, and the TCP healthcheck
   on 27017 stays red meanwhile;
2. create or update root, every declared user and the custom roles, and
   drop the users and roles this step created earlier that are no longer
   declared (an app removed);
3. shut it down and `exec mongod --auth --bind_ip_all`.

Without `--auth` MongoDB enforces no users even when they exist, so no
root login and no localhost exception are involved, and an install whose
MongoDB ran without authentication comes up authenticated on its next
start. Passwords are read from the environment; the script holds no secret
(0644, a plain ConfigMap). MongoDB is a storage component, which the
per-workload config hash excludes; the renderers stamp
`mongodb_config_hash` on it instead, so a changed user set restarts it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component, MongoClientSpec

MONGODB_COMPONENT = "mongodb"
ROOT_USER = "root"
ROOT_PASSWORD_SECRET = "mongo-root-password"
ROOT_PASSWORD_ENV = "MONGO_ROOT_PASSWORD"
PASSWORD_SECRET_PREFIX = "mongo-password-"
CONVERGE_PORT = 27018

ENTRYPOINT_RENDER_PATH = "config/mongodb/entrypoint.sh"
ENTRYPOINT_CONTAINER_PATH = "/etc/wip-mongo/entrypoint.sh"

# Marks the users this step creates, so it only ever drops its own.
_MANAGED = {"managedBy": "wip-deploy"}
_ROLE_PREFIX = "wip-"


def mongodb_component(
    deployment: Deployment, components: list[Component]
) -> Component | None:
    for c in components:
        if c.metadata.name == MONGODB_COMPONENT and is_component_active(c, deployment):
            return c
    return None


def mongo_clients(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> list[tuple[str, MongoClientSpec]]:
    """Active components and enabled apps with a `mongo_client`, sorted;
    empty when MongoDB itself is inactive."""
    if mongodb_component(deployment, components) is None:
        return []
    enabled_apps = {a.name for a in deployment.spec.apps if a.enabled}
    owners: list[Component | App] = [
        *(c for c in components if is_component_active(c, deployment)),
        *(a for a in apps if a.metadata.name in enabled_apps),
    ]
    return sorted(
        (o.metadata.name, o.spec.mongo_client)
        for o in owners
        if o.spec.mongo_client is not None
    )


def password_secret(client: str) -> str:
    return f"{PASSWORD_SECRET_PREFIX}{client}"


def server_password_env(client: str) -> str:
    return "MONGO_PASSWORD_" + client.upper().replace("-", "_")


def _custom_role(client: str) -> str:
    return f"{_ROLE_PREFIX}{client}-collections"


def desired_state(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> dict[str, Any]:
    """The roles and users the start step converges MongoDB to."""
    clients = mongo_clients(deployment, components, apps)
    platform_dbs = sorted({db for _, spec in clients for db in spec.databases})
    roles: list[dict[str, Any]] = []
    users: list[dict[str, Any]] = [
        {"user": ROOT_USER, "passwordEnv": ROOT_PASSWORD_ENV,
         "roles": [{"role": "root", "db": "admin"}]},
    ]
    for name, spec in clients:
        granted = [{"role": "readWrite", "db": db} for db in spec.databases]
        if spec.collection_privileges:
            role = _custom_role(name)
            roles.append({
                "role": role,
                "privileges": [
                    {"resource": {"db": p.db, "collection": p.collection},
                     "actions": list(p.actions)}
                    for p in spec.collection_privileges
                ],
            })
            granted.append({"role": role, "db": "admin"})
        if "read-all" in spec.roles:
            granted += [{"role": "read", "db": db} for db in platform_dbs]
        users.append({"user": name, "passwordEnv": server_password_env(name), "roles": granted})
    return {"roles": roles, "users": users}


_CONVERGE_JS = """\
const desired = __DESIRED__;
const MANAGED = __MANAGED__;
const ROLE_PREFIX = __ROLE_PREFIX__;
const admin = db.getSiblingDB("admin");
for (const r of desired.roles) {
  if (admin.getRole(r.role)) admin.updateRole(r.role, {privileges: r.privileges, roles: []});
  else admin.createRole({role: r.role, privileges: r.privileges, roles: []});
}
for (const u of desired.users) {
  const pwd = process.env[u.passwordEnv];
  if (!pwd) throw new Error("missing password variable " + u.passwordEnv);
  const spec = {pwd: pwd, roles: u.roles, customData: MANAGED};
  if (admin.getUser(u.user)) admin.updateUser(u.user, spec);
  else admin.createUser(Object.assign({user: u.user}, spec));
}
// mongosh returns {users: [...], ok} / {roles: [...], ok}, not arrays.
const list = (res, key) => Array.isArray(res) ? res : res[key];
const wantUsers = new Set(desired.users.map(u => u.user));
for (const u of list(admin.getUsers(), "users")) {
  if (u.customData && u.customData.managedBy === MANAGED.managedBy && !wantUsers.has(u.user)) admin.dropUser(u.user);
}
const wantRoles = new Set(desired.roles.map(r => r.role));
for (const r of list(admin.getRoles(), "roles")) {
  if (r.role.startsWith(ROLE_PREFIX) && !wantRoles.has(r.role)) admin.dropRole(r.role);
}
print("wip-mongo: users " + list(admin.getUsers(), "users").map(u => u.user).sort().join(","));
"""


def render_mongodb_entrypoint(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> str | None:
    """The MongoDB start script, or None when MongoDB is inactive.
    Deterministic; extra `mongod` arguments come from the manifest command."""
    mongodb = mongodb_component(deployment, components)
    if mongodb is None:
        return None
    data_dir = mongodb.spec.storage[0].mount_path if mongodb.spec.storage else "/data/db"
    converge = (
        _CONVERGE_JS
        .replace("__DESIRED__", json.dumps(desired_state(deployment, components, apps), sort_keys=True))
        .replace("__MANAGED__", json.dumps(_MANAGED))
        .replace("__ROLE_PREFIX__", json.dumps(_ROLE_PREFIX))
    )
    return f"""#!/bin/bash
# Generated by wip-deploy — do not edit. Holds no secrets: passwords come from
# the MONGO_ROOT_PASSWORD / MONGO_PASSWORD_* environment.
#
# Every start: converge root, the declared users and roles through a temporary
# mongod WITHOUT --auth on 127.0.0.1:{CONVERGE_PORT} (unreachable from other
# containers; the healthcheck on 27017 stays red), then serve with --auth.
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
    # Same as the image's own entrypoint: own the data dir, drop to mongodb.
    find -L {data_dir} \\! -user mongodb -exec chown mongodb '{{}}' +
    exec gosu mongodb bash "$BASH_SOURCE" "$@"
fi

mongod --bind_ip 127.0.0.1 --port {CONVERGE_PORT} --dbpath {data_dir} \\
    --fork --logpath /tmp/wip-mongo-converge.log "$@" >/dev/null
for _ in $(seq 1 60); do
    mongosh --quiet --port {CONVERGE_PORT} --eval 'db.runCommand({{ping: 1}})' >/dev/null 2>&1 && break
    sleep 1
done

cat > /tmp/wip-mongo-converge.js <<'WIP_MONGO_JS'
{converge}WIP_MONGO_JS
mongosh --quiet --port {CONVERGE_PORT} admin --file /tmp/wip-mongo-converge.js
rm -f /tmp/wip-mongo-converge.js

mongod --shutdown --dbpath {data_dir} >/dev/null
exec mongod --auth --bind_ip_all --dbpath {data_dir} "$@"
"""


def mongodb_config_hash(script: str) -> str:
    return hashlib.sha256(script.encode()).hexdigest()[:16]
